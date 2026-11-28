#!/usr/bin/env nu

# ==================================================================================================
# Agent Project Move: Antigravity CLI Tests
# ==================================================================================================

use std/assert
use std-rfc/str

use ../common.testlib.nu [fixture-env run-cli]
use ../lib/sqlite.nu [sql-quote sqlite-rows]
use ./agy.nu

const ROOT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
const CHILD_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
const PEER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
const TARGET_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

def agy-env [home: string]: nothing -> record {
  fixture-env $home | insert HOME $home
}

def agy-root [home: string]: nothing -> string {
  $home | path join ".gemini/antigravity-cli"
}

def varint [number: int]: nothing -> binary {
  mut rest = $number
  mut data = 0x[]
  while $rest >= 128 {
    let next = (($rest mod 128 + 128) | into binary --compact)
    $data = (bytes build $data $next)
    $rest = ($rest // 128)
  }
  bytes build $data ($rest | into binary --compact)
}

def field [number: int, payload: any]: nothing -> binary {
  let data = ($payload | into binary)
  bytes build (varint ($number * 8 + 2)) (varint ($data | bytes length | into int)) $data
}

def file-uri [path: string]: nothing -> string {
  $"file://($path | str replace --all ' ' '%20')"
}

def fixture-metadata [uris: list<string>]: nothing -> binary {
  let workspaces = ($uris | each {|uri| field 1 (field 1 $uri) })
  let roots = ($uris | each {|uri| field 7 $uri })
  let cloned = (field 22 (field 1 $"($uris.0)/clone"))
  bytes build ...$workspaces ...$roots $cloned 0x[A0 06 2A]
}

def summary [id: string, uris: list<string>]: nothing -> binary {
  let workspaces = ($uris | each {|uri| field 9 (field 1 $uri) })
  bytes build (
    field 1 "Fixture summary"
  ) (field 4 $id) ...$workspaces (field 17 (fixture-metadata $uris))
}

def jetbox-entry [id: string, uris: list<string>]: nothing -> binary {
  field 1 (bytes build (field 1 $id) (field 2 (summary $id $uris)))
}

def conversation [store: string, id: string]: nothing -> string {
  $store | path join "conversations" $"($id).db"
}

def annotation [store: string, id: string]: nothing -> string {
  $store | path join "annotations" $"($id).pbtxt"
}

def make-session [
  store: string
  id: string
  cwd: string
  parent: string = ""
  --extra-workspace: string
] {
  let db = (conversation $store $id)
  let uris = ([$cwd $extra_workspace] | compact | each {|path| file-uri $path })
  let blob = (fixture-metadata $uris | encode hex)
  let artifact = ($store | path join "brain" $id "notes.md")
  let note = (annotation $store $id)
  mkdir ($db | path dirname) ($artifact | path dirname) ($note | path dirname)
  (
    $"
    CREATE TABLE trajectory_meta \(trajectory_id TEXT PRIMARY KEY, cascade_id TEXT,
      trajectory_type INTEGER, source INTEGER\);
    CREATE TABLE trajectory_metadata_blob \(id TEXT PRIMARY KEY DEFAULT 'main', data BLOB\);
    CREATE TABLE steps \(idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER,
      step_payload BLOB\);
    INSERT INTO trajectory_meta VALUES \((sql-quote $id), (sql-quote $id), 4, 17\);
    INSERT INTO trajectory_metadata_blob VALUES \('main', X'($blob)'\);
    INSERT INTO steps VALUES \(0, 14, 3, X'00FF'\);
    "
    | str dedent
    | ^sqlite3 -bail $db
  )
  $id | save $artifact
  $"user_title: \"($id)\"" | save $note

  let index = ($store | path join "conversation_summaries.db")
  let raw = (summary $id $uris | encode hex)
  (
    $"
    CREATE TABLE IF NOT EXISTS conversation_summaries \(
      conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
      preview TEXT NOT NULL DEFAULT '', step_count INTEGER NOT NULL DEFAULT 0,
      last_modified_time DATETIME NOT NULL, workspace_uris TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
      project_id TEXT NOT NULL DEFAULT '', agent_name TEXT NOT NULL DEFAULT '',
      parent_conversation_id TEXT NOT NULL DEFAULT '',
      nesting_depth INTEGER NOT NULL DEFAULT 0, battle_id TEXT NOT NULL DEFAULT '',
      winning_conversation_id TEXT NOT NULL DEFAULT '',
      not_fully_idle NUMERIC NOT NULL DEFAULT false, killed NUMERIC NOT NULL DEFAULT false,
      last_user_input_time DATETIME NOT NULL,
      last_user_input_step_index INTEGER NOT NULL DEFAULT -1,
      app_data_dir TEXT NOT NULL DEFAULT '', raw_summary BLOB, group_id TEXT NOT NULL DEFAULT ''
    \);
    INSERT INTO conversation_summaries
      \(conversation_id, title, last_modified_time, workspace_uris, project_id,
        parent_conversation_id, last_user_input_time, app_data_dir, raw_summary\)
      VALUES \((sql-quote $id), 'Fixture summary', '2026-01-01T00:00:00Z',
        (sql-quote ($uris | to json --raw)),
        'default-cli-project', (sql-quote $parent), '2026-01-01T00:00:00Z',
        'antigravity-cli', X'($raw)'\);
    "
    | str dedent
    | ^sqlite3 -bail $index
  )
  jetbox-entry $id $uris | save --append ($store | path join "jetbox_summaries_proto.pb")
}

def set-last [store: string, entries: record] {
  let cache = ($store | path join "cache")
  mkdir $cache
  $entries | to json | save ($cache | path join "last_conversations.json")
}

def assert-session [
  store: string
  id: string
  cwd: string
  parent: string = ""
  --extra-workspace: string
  --payload: string = "00FF"
] {
  let db = (conversation $store $id)
  let uris = ([$cwd $extra_workspace] | compact | each {|path| file-uri $path })
  let metadata_sql = "SELECT hex(data) AS data FROM trajectory_metadata_blob WHERE id = 'main'"
  let summary_sql = (
    "SELECT workspace_uris, parent_conversation_id AS parent, hex(raw_summary) AS raw "
    + $"FROM conversation_summaries WHERE conversation_id = (sql-quote $id)"
  )
  let indexed = (sqlite-rows ($store | path join "conversation_summaries.db") $summary_sql)
  let cache = (open --raw ($store | path join "jetbox_summaries_proto.pb") | into binary)

  assert equal (sqlite-rows $db $metadata_sql | get 0.data) (fixture-metadata $uris | encode hex)
  assert equal (sqlite-rows $db "SELECT hex(step_payload) AS data FROM steps") [{data: $payload}]
  assert equal ($indexed | get 0.workspace_uris | from json) $uris
  assert equal ($indexed | get 0.parent) $parent
  assert equal ($indexed | get 0.raw) (summary $id $uris | encode hex)
  assert (($cache | bytes index-of (jetbox-entry $id $uris)) >= 0)
  assert equal (open --raw ($store | path join "brain" $id "notes.md")) $id
  assert equal (open --raw (annotation $store $id)) $"user_title: \"($id)\""
}

def commit-uncheckpointed-step [db: string] {
  let script = (
    '
    import os
    import sqlite3
    import sys

    db = sqlite3.connect(sys.argv[1])
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA wal_autocheckpoint = 0")
    db.execute("UPDATE steps SET step_payload = ? WHERE idx = 0", (bytes.fromhex("00FF01"),))
    db.commit()
    os._exit(0)
    '
    | str dedent
  )
  ^python3 -Ic $script $db
  assert ($"($db)-wal" | path exists)
  rm $"($db)-shm"
}

def "test agy exact and default moves update workspace associations" [] {
  let home = (mktemp -d | path expand)
  let store = (agy-root $home)
  let old = "/home/u/src/case study"
  let child = ($old | path join "child")
  let peer = "/home/u/src/peer"
  let shared = ($old | path join "shared")
  let new = ($home | path join "new project")
  let last = ($store | path join "cache/last_conversations.json")
  mkdir $new
  make-session $store $ROOT_ID $old
  make-session $store $CHILD_ID $child $ROOT_ID
  make-session $store $PEER_ID $peer --extra-workspace $shared
  make-session $store $TARGET_ID $new
  let pointers = {
    ($old): $ROOT_ID
    ($child): $CHILD_ID
    ($shared): $PEER_ID
    ($new): $TARGET_ID
  }
  set-last $store $pointers
  let databases = [
    (conversation $store $ROOT_ID)
    ($store | path join "conversation_summaries.db")
  ]
  for db in $databases {
    let wal = (^sqlite3 -bail $db "PRAGMA journal_mode = WAL;" | complete)
    assert equal $wal.exit_code 0 $wal.stderr
    assert equal ($wal.stdout | str trim) "wal"
  }
  let sidecars = ($databases | each {|db| [$"($db)-shm" $"($db)-wal"] } | flatten)
  assert ($sidecars | all {|file| not ($file | path exists) })

  with-env (agy-env $home) {
    let scanned = (run-cli scan --assistant "agy")
    assert equal $scanned.exit_code 0 $scanned.stderr
    assert str contains $scanned.stdout $old
    assert str contains $scanned.stdout $peer

    let preview = (run-cli move $old $new --assistant "agy" --exact)
    assert equal $preview.exit_code 0 $preview.stderr
    assert str contains $preview.stdout "Dry run"

    assert ($sidecars | all {|file| not ($file | path exists) })
    assert-session $store $ROOT_ID $old
    assert equal (open $last) $pointers
    assert not (($home | path join "state/agent-project-move") | path exists)

    let moved = (run-cli move $old $new --assistant "agy" --exact --apply)
    assert equal $moved.exit_code 0 $moved.stderr
    assert str contains $moved.stdout "Backed up to"
    let last_used = (agy scan | where path == $new | get 0.last_used)
    assert equal $last_used ("2026-01-01T00:00:00Z" | into datetime)
  }

  let new_child = ($new | path join "child")
  assert-session $store $ROOT_ID $new
  assert-session $store $CHILD_ID $new_child $ROOT_ID
  assert-session $store $PEER_ID $peer --extra-workspace $shared
  assert-session $store $TARGET_ID $new
  assert equal (open $last) {
    ($new): $TARGET_ID
    ($new_child): $CHILD_ID
    ($shared): $PEER_ID
  }

  let moved = (with-env (agy-env $home) {
    run-cli move $old $new --assistant "agy" --apply
  })
  assert equal $moved.exit_code 0 $moved.stderr

  let new_shared = ($new | path join "shared")
  assert-session $store $PEER_ID $peer --extra-workspace $new_shared
  assert equal (open $last) {
    ($new): $TARGET_ID
    ($new_child): $CHILD_ID
    ($new_shared): $PEER_ID
  }
}

def "test agy transfer preserves destination sessions and retires only selected copies" [] {
  let root = (mktemp -d | path expand)
  let source = ($root | path join "source")
  let target = ($root | path join "target")
  let old = "/home/u/src/app"
  let new = ($root | path join "new")
  let child = ($old | path join "child")
  let new_child = ($new | path join "child")
  let bundle = ($root | path join "bundle")
  let outbound = (agy-root $source)
  let inbound = (agy-root $target)
  let fresh_home = ($root | path join "fresh")
  let fresh = (agy-root $fresh_home)
  mkdir $new

  make-session $outbound $ROOT_ID $old
  make-session $outbound $CHILD_ID $child $ROOT_ID
  make-session $outbound $PEER_ID $old
  make-session $inbound $TARGET_ID $new
  set-last $outbound {
    ($old): $ROOT_ID
    ($child): $CHILD_ID
  }
  set-last $inbound {($new): $TARGET_ID}
  let transcript = (conversation $outbound $ROOT_ID)
  commit-uncheckpointed-step $transcript
  let before = (open --raw $transcript | hash sha256)

  let exported = (with-env (agy-env $source) {
    run-cli export $old $bundle --assistant "agy" --session-ids $ROOT_ID
  })
  assert equal $exported.exit_code 0 $exported.stderr
  assert equal (open --raw $transcript | hash sha256) $before
  assert not ($"($transcript)-shm" | path exists)

  let blocked = ($root | path join "blocked")
  let blocked_store = (agy-root $blocked)
  let protected = ($root | path join "protected")
  let brain = ($blocked_store | path join "brain")
  mkdir $brain $protected
  let link = ($brain | path join $ROOT_ID)
  ^ln -s $protected $link

  let blocked_import = (with-env (agy-env $blocked) {
    run-cli import $bundle --assistant "agy" --target $new
  })
  assert not equal $blocked_import.exit_code 0
  assert str contains $blocked_import.stderr "refuses symlink destination"
  assert (ls $protected | is-empty)
  rm $link
  let incompatible = ($blocked_store | path join "conversation_summaries.db")
  "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY);"
  | ^sqlite3 -bail $incompatible
  let blocked_import = (with-env (agy-env $blocked) {
    run-cli import $bundle --assistant "agy" --target $new
  })
  assert not equal $blocked_import.exit_code 0
  assert str contains $blocked_import.stderr "summary columns differ"
  assert not ((conversation $blocked_store $ROOT_ID) | path exists)

  with-env (agy-env $target) {
    let preview = (run-cli import $bundle --assistant "agy" --target $new)
    assert equal $preview.exit_code 0 $preview.stderr
    assert str contains $preview.stdout "Dry run"
    assert not ((conversation $inbound $ROOT_ID) | path exists)

    let imported = (run-cli import $bundle --assistant "agy" --target $new --apply)
    assert equal $imported.exit_code 0 $imported.stderr

    let repeated = (run-cli import $bundle --assistant "agy" --target $new --apply)
    assert equal $repeated.exit_code 0 $repeated.stderr
    assert str contains $repeated.stdout "No changes."
  }

  assert-session $inbound $ROOT_ID $new --payload "00FF01"
  assert-session $inbound $CHILD_ID $new_child $ROOT_ID
  assert-session $inbound $TARGET_ID $new
  assert not ((conversation $inbound $PEER_ID) | path exists)
  assert equal (
    open ($inbound | path join "cache/last_conversations.json")
  ) {
    ($new): $TARGET_ID
    ($new_child): $CHILD_ID
  }

  let imported_db = (conversation $inbound $ROOT_ID)
  "UPDATE steps SET step_payload = X'DEADBEEF' WHERE idx = 0;" | ^sqlite3 -bail $imported_db
  let rejected = (with-env (agy-env $target) {
    run-cli import $bundle --assistant "agy" --target $new --apply
  })
  assert not equal $rejected.exit_code 0
  assert equal (sqlite-rows $imported_db "SELECT hex(step_payload) AS data FROM steps") [
    {data: "DEADBEEF"}
  ]

  let initialized = (with-env (agy-env $fresh_home) {
    run-cli import $bundle --assistant "agy" --target $new --apply
  })
  assert equal $initialized.exit_code 0 $initialized.stderr

  assert-session $fresh $ROOT_ID $new --payload "00FF01"
  assert-session $fresh $CHILD_ID $new_child $ROOT_ID
  assert equal (open ($fresh | path join "cache/last_conversations.json")) {
    ($new): $ROOT_ID
    ($new_child): $CHILD_ID
  }

  let retired = (with-env (agy-env $source) {
    run-cli retire $old --assistant "agy" --session-ids $ROOT_ID --apply
  })
  assert equal $retired.exit_code 0 $retired.stderr
  assert str contains $retired.stdout "Backed up to"

  for id in [$ROOT_ID $CHILD_ID] {
    assert not ((conversation $outbound $id) | path exists)
    assert not (($outbound | path join "brain" $id) | path exists)
    assert not ((annotation $outbound $id) | path exists)
  }
  assert-session $outbound $PEER_ID $old
  assert equal (sqlite-rows ($outbound | path join "conversation_summaries.db") (
    "SELECT conversation_id FROM conversation_summaries"
  )) [{conversation_id: $PEER_ID}]
  assert equal (open ($outbound | path join "cache/last_conversations.json")) {}
  assert equal (
    open --raw ($outbound | path join "jetbox_summaries_proto.pb") | into binary
  ) (jetbox-entry $PEER_ID [(file-uri $old)])
}
