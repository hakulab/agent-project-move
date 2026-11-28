"""Work with the workspace fields in Antigravity CLI protobuf messages."""

from collections.abc import Callable, Iterable, Iterator
from urllib.parse import quote, unquote, urlsplit


Field = tuple[int, int, bytes]
PathMapper = Callable[[str], str]
PayloadRewrite = Callable[[bytes], bytes]


def _varint(value: int) -> bytes:
    data = bytearray()
    while value >= 128:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            break
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Invalid Antigravity protobuf varint")


def _fields(data: bytes) -> Iterator[tuple[Field, bytes]]:
    offset = 0
    while offset < len(data):
        start = offset
        key, value_start = _read_varint(data, offset)
        number, wire_type = divmod(key, 8)
        if not 0 < number < 1 << 29:
            raise ValueError("Invalid Antigravity protobuf field number")

        if wire_type == 0:
            _, offset = _read_varint(data, value_start)
        elif wire_type == 1:
            offset = value_start + 8
        elif wire_type == 2:
            size, value_start = _read_varint(data, value_start)
            offset = value_start + size
        elif wire_type == 5:
            offset = value_start + 4
        else:
            raise ValueError(f"Unsupported Antigravity protobuf wire type {wire_type}")

        if offset > len(data):
            raise ValueError("Truncated Antigravity protobuf field")
        yield (number, wire_type, data[value_start:offset]), data[start:offset]


def parse_fields(data: bytes) -> Iterator[Field]:
    return (field for field, _ in _fields(data))


def _encode_field(number: int, wire_type: int, value: bytes) -> bytes:
    key = _varint(number * 8 + wire_type)
    length = _varint(len(value)) if wire_type == 2 else b""
    return key + length + value


def encode_fields(fields: Iterable[Field]) -> bytes:
    return b"".join(_encode_field(*field) for field in fields)


def _rewrite_fields(data: bytes, rewrites: dict[int, PayloadRewrite]) -> bytes:
    parts = []
    for (number, wire_type, value), raw in _fields(data):
        rewrite = rewrites.get(number)
        if wire_type == 2 and rewrite is not None:
            updated = rewrite(value)
            if updated != value:
                raw = _encode_field(number, wire_type, updated)
        parts.append(raw)
    return b"".join(parts)


def uri_path(uri: str) -> str | None:
    try:
        parts = urlsplit(uri)
        if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
            return None
        if not parts.path.startswith("/") or "?" in uri or "#" in uri:
            return None
        path = unquote(parts.path, errors="strict")
        if "\0" not in path:
            return path
    except (UnicodeError, ValueError):
        pass
    return None


def file_uri(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError(f"Antigravity workspace path is not absolute: {path}")
    return "file://" + quote(path, safe="/:$&+,;=@")


def _payloads(data: bytes, number: int) -> Iterator[bytes]:
    return (value for field, wire, value in parse_fields(data) if field == number and wire == 2)


def workspace_path(metadata: bytes) -> str | None:
    for workspace in _payloads(metadata, 1):
        location = next(_payloads(workspace, 1), None)
        if location is not None:
            return uri_path(location.decode("utf-8"))
    location = next(_payloads(metadata, 7), None)
    return uri_path(location.decode("utf-8")) if location is not None else None


def _rewrite_uri(value: bytes, mapper: PathMapper) -> bytes:
    path = uri_path(value.decode("utf-8"))
    if path is not None:
        target = mapper(path)
        if target != path:
            return file_uri(target).encode("utf-8")
    return value


def _rewrite_workspace(workspace: bytes, mapper: PathMapper) -> bytes:
    def rewrite(value: bytes) -> bytes:
        return _rewrite_uri(value, mapper)

    return _rewrite_fields(workspace, {1: rewrite, 2: rewrite})


def rewrite_metadata(metadata: bytes, mapper: PathMapper) -> bytes:
    def rewrite_uri(value: bytes) -> bytes:
        return _rewrite_uri(value, mapper)

    return _rewrite_fields(
        metadata,
        {
            1: lambda value: _rewrite_workspace(value, mapper),
            7: rewrite_uri,
            22: lambda value: _rewrite_fields(value, {1: rewrite_uri}),
        },
    )


def rewrite_summary(summary: bytes, mapper: PathMapper) -> bytes:
    return _rewrite_fields(
        summary,
        {
            9: lambda value: _rewrite_workspace(value, mapper),
            17: lambda value: rewrite_metadata(value, mapper),
        },
    )


def _jetbox_entry(entry: bytes) -> tuple[str, bytes]:
    keys = list(_payloads(entry, 1))
    values = list(_payloads(entry, 2))
    if not keys or not keys[-1] or not values:
        raise ValueError("Incomplete Antigravity summary map entry")
    return keys[-1].decode("utf-8"), values[-1]


def jetbox_entries(data: bytes) -> dict[str, bytes]:
    return dict(_jetbox_entry(entry) for entry in _payloads(data, 1))


def rewrite_jetbox(data: bytes, updates: dict[str, bytes | None]) -> bytes:
    parts = []
    seen = set()
    for (number, wire_type, entry), raw in _fields(data):
        if number == 1 and wire_type == 2:
            key, value = _jetbox_entry(entry)
            seen.add(key)
            if key in updates:
                updated = updates[key]
                if updated is None:
                    continue
                if updated != value:
                    entry = _rewrite_fields(entry, {2: lambda _: updated})
                    raw = _encode_field(1, 2, entry)
        parts.append(raw)

    for key, updated in updates.items():
        if key not in seen and updated is not None:
            entry = encode_fields([(1, 2, key.encode("utf-8")), (2, 2, updated)])
            parts.append(_encode_field(1, 2, entry))

    return b"".join(parts)
