# ==================================================================================================
# Agent Project Move: Antigravity CLI Store
# ==================================================================================================

import json
import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from filecmp import cmp
from itertools import zip_longest
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from agy_wire import (
    file_uri,
    jetbox_entries,
    parse_fields,
    rewrite_jetbox,
    rewrite_metadata,
    rewrite_summary,
    uri_path,
    workspace_path,
)

ASSISTANT = "agy"
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
SUMMARY_DB = "conversation_summaries.db"
JETBOX = "jetbox_summaries_proto.pb"
LAST = "cache/last_conversations.json"
METADATA = "SELECT data FROM trajectory_metadata_blob WHERE id = 'main'"
SUMMARY_COLUMNS = {
    "conversation_id": "TEXT PRIMARY KEY",
    "title": "TEXT NOT NULL DEFAULT ''",
    "preview": "TEXT NOT NULL DEFAULT ''",
    "step_count": "INTEGER NOT NULL DEFAULT 0",
    "last_modified_time": "DATETIME NOT NULL",
    "workspace_uris": "TEXT NOT NULL",
    "status": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT NOT NULL DEFAULT ''",
    "agent_name": "TEXT NOT NULL DEFAULT ''",
    "parent_conversation_id": "TEXT NOT NULL DEFAULT ''",
    "nesting_depth": "INTEGER NOT NULL DEFAULT 0",
    "battle_id": "TEXT NOT NULL DEFAULT ''",
    "winning_conversation_id": "TEXT NOT NULL DEFAULT ''",
    "not_fully_idle": "NUMERIC NOT NULL DEFAULT false",
    "killed": "NUMERIC NOT NULL DEFAULT false",
    "last_user_input_time": "DATETIME NOT NULL",
    "last_user_input_step_index": "INTEGER NOT NULL DEFAULT -1",
    "app_data_dir": "TEXT NOT NULL DEFAULT ''",
    "raw_summary": "BLOB",
    "group_id": "TEXT NOT NULL DEFAULT ''",
}


@contextmanager
def read_db(path):
    wal = Path(f"{path}-wal")
    if wal.exists() and not Path(f"{path}-shm").exists():
        # SQLite may create a shared-memory file even on a read-only connection.
        with TemporaryDirectory() as directory:
            temporary = Path(directory) / path.name
            copyfile(path, temporary)
            copyfile(wal, f"{temporary}-wal")
            with open_db(temporary, "?mode=ro") as db:
                yield db
    else:
        options = "?mode=ro" if wal.exists() else "?mode=ro&immutable=1"
        with open_db(path, options) as db:
            yield db


@contextmanager
def open_db(path, options):
    with closing(sqlite3.connect(path.absolute().as_uri() + options, uri=True)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        db.row_factory = sqlite3.Row
        yield db


def sql_value(value):
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    if isinstance(value, bytes):
        return f"X'{value.hex()}'"
    if isinstance(value, str):
        return f"CAST(X'{value.encode().hex()}' AS TEXT)"
    raise ValueError("Invalid Antigravity CLI summary value")


def belongs(root, path):
    return path == root or path.startswith(root.rstrip("/") + "/")


def mapper(old, new):
    def remap(path):
        if new is None or not belongs(old, path) or old == new:
            return path
        suffix = path[len(old.rstrip("/")):].lstrip("/")
        return new.rstrip("/") + "/" + suffix if suffix else new
    return remap


def summary_rows(path):
    if not path.exists():
        return {}
    with read_db(path) as db:
        return {
            row["conversation_id"]: dict(row)
            for row in db.execute("SELECT * FROM conversation_summaries")
        }


def read_json(path):
    return json.loads(path.read_bytes()) if path.exists() else {}


def read_bytes(path):
    return path.read_bytes() if path.exists() else b""


def conversation(path, session_id):
    with read_db(path) as db:
        identity = db.execute(
            "SELECT 1 FROM trajectory_meta WHERE cascade_id = ? AND trajectory_type = 4",
            (session_id,),
        ).fetchone()
        row = db.execute(METADATA).fetchone()
    if identity is None or row is None or not isinstance(row["data"], bytes):
        raise ValueError(f"Missing main Antigravity CLI trajectory in {path}")
    return row["data"]


def parent_id(metadata):
    return next(
        (
            value.decode()
            for field, wire, value in parse_fields(metadata)
            if (field, wire) == (5, 2)
        ),
        None,
    )


def last_used(path, summary):
    recorded = summary.get("last_modified_time")
    if recorded:
        return recorded
    wal = Path(f"{path}-wal")
    mtime = max(
        file.stat().st_mtime
        for file in (path, wal)
        if file.exists()
    )
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def sessions(root, summaries):
    rows = []
    for path in sorted((root / "conversations").glob("*.db")):
        session_id = path.stem
        if not UUID.fullmatch(session_id) or not path.is_file():
            continue
        metadata = conversation(path, session_id)
        summary = summaries.get(session_id, {})
        rows.append({
            "id": session_id,
            "file": str(path),
            "cwd": workspace_path(metadata),
            "parent": parent_id(metadata) or summary.get("parent_conversation_id") or None,
            "last_used": last_used(path, summary),
        })
    return rows


def annotation_path(session_id):
    return Path("annotations") / f"{session_id}.pbtxt"


def owned_paths(session_id):
    return (Path("brain") / session_id, annotation_path(session_id))


def artifact_files(root, session_id):
    brain, annotation = owned_paths(session_id)
    if (root / annotation).is_file() and not (root / annotation).is_symlink():
        yield annotation
    if (root / brain).is_symlink():
        raise ValueError(f"Antigravity CLI artifact directory is a symlink: {root / brain}")
    yield from (
        path.relative_to(root) for path in sorted((root / brain).rglob("*"))
        if path.is_file() and not path.is_symlink()
    )


def export_bundle(root, rows, old, summaries):
    files = []
    entries = []
    last = read_json(root / LAST)
    jetbox = jetbox_entries(read_bytes(root / JETBOX))
    for row in rows:
        sid = row["id"]
        summary = summaries.get(sid)
        if summary is not None and summary.get("raw_summary") is not None:
            summary = {**summary, "raw_summary": summary["raw_summary"].hex()}
        entries.append({
            "id": sid,
            "summary": summary,
            "jetbox": jetbox[sid].hex() if sid in jetbox else None,
            "last": [cwd for cwd, value in last.items() if value == sid],
        })
        database = Path(row["file"])
        files.append({
            "kind": "conversation",
            "project": row["cwd"] or old,
            "name": database.name,
            "session": sid,
            "source": str(database),
        })
        files.extend({
            "kind": "artifact",
            "project": row["cwd"] or old,
            "name": str(path),
            "session": sid,
            "source": str(root / path),
        } for path in artifact_files(root, sid))
    return {
        "assistant": ASSISTANT,
        "old": old,
        "files": files,
        "tables": [],
        "sessions": entries,
    }


def action(op, store, path, description, **extras):
    return {
        "op": op,
        "store": store,
        "path": str(path),
        "description": description,
        **extras,
    }


def write_action(path, store, content):
    return prepared_action(path, store, {"content": content.hex()}, edit=path.exists())


def prepared_action(path, store, preparation, *, edit=False, source=None):
    verb = "rewrite" if edit else "create"
    return action(
        "prepared", store, source or path, f"{verb} {store}",
        to=None if edit else str(path), preparation=preparation,
    )


def metadata_updates(data, remap):
    mapped = rewrite_metadata(data, remap)
    if mapped == data:
        return []
    return [f"UPDATE trajectory_metadata_blob SET data = {sql_value(mapped)} WHERE id = 'main'"]


def mapped_summary(row, remap):
    uris = json.loads(row["workspace_uris"])
    mapped = []
    for uri in uris:
        path = uri_path(uri)
        updated = remap(path) if path is not None else None
        mapped.append(file_uri(updated) if updated != path else uri)
    result = dict(row)
    if mapped != uris:
        result["workspace_uris"] = json.dumps(mapped, ensure_ascii=False, separators=(",", ":"))
    if row.get("raw_summary") is not None:
        result["raw_summary"] = rewrite_summary(row["raw_summary"], remap)
    return result


def update_summary(previous, updated):
    assignments = ", ".join(
        f"{key} = {sql_value(updated[key])}"
        for key in ("workspace_uris", "raw_summary")
        if key in previous and previous[key] != updated[key]
    )
    if not assignments:
        return None
    return (
        f"UPDATE conversation_summaries SET {assignments} "
        f"WHERE conversation_id = {sql_value(previous['conversation_id'])}"
    )


def cache_actions(root, jetbox, updates, last, wanted):
    actions = []
    if updates:
        updated = rewrite_jetbox(jetbox, updates)
        if updated != jetbox:
            actions.append(write_action(root / JETBOX, "picker cache", updated))
    if wanted != last:
        content = (json.dumps(wanted, ensure_ascii=False, indent=2) + "\n").encode()
        actions.append(write_action(root / LAST, "continue cache", content))
    return actions


def plan_move(root, rows, summaries, remap):
    jetbox = read_bytes(root / JETBOX)
    picker = jetbox_entries(jetbox)
    last = read_json(root / LAST)
    ids = {row["id"] for row in rows}
    wanted = {path: sid for path, sid in last.items() if sid not in ids or remap(path) == path}
    for path, sid in last.items():
        if sid in ids:
            wanted.setdefault(remap(path), sid)

    actions = []
    updates = {}
    for row in rows:
        path = Path(row["file"])
        sid = row["id"]
        statements = metadata_updates(conversation(path, sid), remap)
        if statements:
            actions.append(action("sql", "conversation", path, "rewrite workspace", sql=statements))
        if sid in summaries:
            previous = summaries[sid]
            statement = update_summary(previous, mapped_summary(previous, remap))
            if statement:
                updates[sid] = statement
    if updates:
        actions.append(action(
            "sql", "picker index", root / SUMMARY_DB, "rewrite workspaces",
            sql=list(updates.values()), derived=True,
        ))
    picker_updates = {sid: rewrite_summary(picker[sid], remap) for sid in ids if sid in picker}
    return cache_actions(root, jetbox, picker_updates, last, wanted) + actions


def plan_retire(root, rows, summaries):
    if not rows:
        return []
    ids = {row["id"] for row in rows}
    jetbox = read_bytes(root / JETBOX)
    last = read_json(root / LAST)
    wanted = {path: sid for path, sid in last.items() if sid not in ids}
    actions = cache_actions(root, jetbox, dict.fromkeys(ids), last, wanted)
    values = ", ".join(sql_value(sid) for sid in sorted(ids) if sid in summaries)
    if values:
        actions.append(action(
            "sql", "picker index", root / SUMMARY_DB, "remove selected summaries",
            sql=[f"DELETE FROM conversation_summaries WHERE conversation_id IN ({values})"],
            rows_first=True,
        ))
    for row in rows:
        path = Path(row["file"])
        artifacts = [root / relative for relative in owned_paths(row["id"])]
        for owned in [path, Path(f"{path}-wal"), Path(f"{path}-shm"), *artifacts]:
            if owned.exists() or owned.is_symlink():
                actions.append(action("delete", "conversation", owned, "retire selected state"))
    return actions


def insert_summary(row):
    keys = [key for key in SUMMARY_COLUMNS if key in row]
    values = ", ".join(sql_value(row[key]) for key in keys)
    return f"INSERT INTO conversation_summaries ({', '.join(keys)}) VALUES ({values})"


def check_summary_columns(path, rows):
    if not rows:
        return
    if path.exists():
        with read_db(path) as db:
            columns = db.execute("PRAGMA table_info(conversation_summaries)").fetchall()
        available = {column["name"] for column in columns}
        required = {
            column["name"]
            for column in columns
            if column["notnull"] and column["dflt_value"] is None
        }
    else:
        available = SUMMARY_COLUMNS.keys()
        required = {
            "conversation_id",
            "last_modified_time",
            "workspace_uris",
            "last_user_input_time",
        }
    for row in rows:
        if row.keys() - available or required - row.keys():
            raise ValueError("Antigravity CLI summary columns differ from the destination schema")


def create_summary_schema():
    columns = ", ".join(f"{name} {kind}" for name, kind in SUMMARY_COLUMNS.items())
    return [f"CREATE TABLE conversation_summaries ({columns})"] + [
        f"CREATE INDEX idx_conversation_summaries_{key} ON conversation_summaries ({key})"
        for key in ("last_user_input_time", "last_modified_time")
    ]


def same_conversation(incoming, existing, statements):
    with closing(sqlite3.connect(":memory:")) as mapped:
        with read_db(incoming) as source:
            source.backup(mapped)
        for statement in statements:
            mapped.execute(statement)
        with read_db(existing) as local:
            return all(
                left == right
                for left, right in zip_longest(mapped.iterdump(), local.iterdump())
            )


def checked_summary(entry, session_id):
    summary = entry["summary"]
    if summary is None:
        return None
    if summary["conversation_id"] != session_id or set(summary) - SUMMARY_COLUMNS.keys():
        raise ValueError(f"Invalid Antigravity CLI summary for {session_id}")
    if summary.get("raw_summary") is not None:
        summary = {**summary, "raw_summary": bytes.fromhex(summary["raw_summary"])}
    return summary


def import_target(root, relative):
    target = root / relative
    for path in [target, *target.parents]:
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f"Antigravity CLI import refuses symlink destination: {path}")
        if path != target and path.exists() and not path.is_dir():
            raise ValueError(f"Antigravity CLI import requires a directory at {path}")
    return target


def import_files(root, files, entries, remap):
    actions = []
    seen = set()
    names = set()
    for file in files:
        sid = file["session"]
        name = Path(file["name"])
        source = Path(file["bundle"])
        if sid not in entries:
            raise ValueError(f"Unlisted Antigravity CLI session {sid}")
        if (file["kind"], name) in names:
            raise ValueError(f"Duplicate Antigravity CLI bundle entry {name}")
        names.add((file["kind"], name))
        if file["kind"] == "conversation" and name == Path(f"{sid}.db"):
            target = import_target(root, Path("conversations") / name)
            statements = metadata_updates(conversation(source, sid), remap)
            if target.exists():
                if not same_conversation(source, target, statements):
                    raise ValueError(f"Existing Antigravity CLI conversation {sid} differs")
            else:
                actions.append(prepared_action(
                    target, "conversation", {"source": str(source), "sql": statements},
                    source=source,
                ))
            seen.add(sid)
        elif file["kind"] == "artifact" and (
            name == annotation_path(sid) or Path("brain") / sid in name.parents
        ):
            target = import_target(root, name)
            if target.exists():
                if not cmp(source, target, shallow=False):
                    raise ValueError(f"Existing Antigravity CLI artifact differs: {target}")
            else:
                actions.append(prepared_action(
                    target, "artifact", {"source": str(source)}, source=source,
                ))
        else:
            raise ValueError(f"Invalid Antigravity CLI bundle entry {name}")
    if seen != entries.keys():
        raise ValueError("Antigravity CLI bundle is missing conversation databases")
    return actions


def plan_import(root, bundle, target, summaries):
    if not bundle["old"].startswith("/"):
        raise ValueError("Invalid Antigravity CLI bundle root")
    remap = mapper(bundle["old"], target)
    rows = bundle["sessions"]
    entries = {row["id"]: row for row in rows}
    if len(entries) != len(rows) or any(not UUID.fullmatch(sid) for sid in entries):
        raise ValueError("Invalid Antigravity CLI session IDs in bundle")
    actions = import_files(root, bundle["files"], entries, remap)
    jetbox = read_bytes(root / JETBOX)
    picker = jetbox_entries(jetbox)
    last = read_json(root / LAST)
    wanted = dict(last)
    incoming_summaries = []
    updates = {}
    for sid, entry in entries.items():
        summary = checked_summary(entry, sid)
        if summary is not None and sid not in summaries:
            incoming_summaries.append(mapped_summary(summary, remap))
        if entry["jetbox"] is not None and sid not in picker:
            updates[sid] = rewrite_summary(bytes.fromhex(entry["jetbox"]), remap)
        for path in entry["last"]:
            if not path.startswith("/"):
                raise ValueError(f"Invalid Antigravity CLI continue path for {sid}")
            wanted.setdefault(remap(path), sid)
    if incoming_summaries:
        path = root / SUMMARY_DB
        check_summary_columns(path, incoming_summaries)
        statements = [insert_summary(row) for row in incoming_summaries]
        if path.exists():
            actions.append(action("sql", "picker index", path, "import summaries", sql=statements))
        else:
            actions.append(prepared_action(
                path, "picker index", {"sql": create_summary_schema() + statements},
            ))
    return actions + cache_actions(root, jetbox, updates, last, wanted)


def prepare(spec, target):
    if "content" in spec:
        target.write_bytes(bytes.fromhex(spec["content"]))
        return
    if "sql" not in spec:
        copyfile(spec["source"], target)
        return
    with closing(sqlite3.connect(target)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        if "source" in spec:
            with read_db(Path(spec["source"])) as source:
                source.backup(db)
        db.execute("PRAGMA journal_mode = DELETE")
        with db:
            for statement in spec["sql"]:
                db.execute(statement)


def main(command, location, params):
    root = Path(location)
    if command == "prepare":
        prepare(params, root)
        return None
    summaries = summary_rows(root / SUMMARY_DB)
    if command == "sessions":
        return sessions(root, summaries)
    if command == "move":
        return plan_move(root, params["rows"], summaries, mapper(params["old"], params["new"]))
    if command == "retire":
        return plan_retire(root, params["rows"], summaries)
    if command == "export":
        return export_bundle(root, params["rows"], params["old"], summaries)
    if command == "import":
        return plan_import(root, params["bundle"], params["target"], summaries)
    raise ValueError(f"Unknown Antigravity CLI store command {command}")


if __name__ == "__main__":
    try:
        result = main(*sys.argv[1:], json.load(sys.stdin))
        json.dump(result, sys.stdout, ensure_ascii=False)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        sys.exit(str(error))
