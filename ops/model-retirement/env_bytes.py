"""Pure native EnvironmentFile parser/rendering reused from the qualified trial helper.

Only parsing/rendering primitives are retained. No trial model decisions,
controllers, authorization, or runtime effects are imported.
"""
import re
_ASSIGN = re.compile(r"^([ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*)(.*)$")

def _decode_value(raw):
    raw = raw.strip(" \t")
    if not raw:
        return ""
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise ValueError("multiline or NUL environment value")
    if raw.startswith("'"):
        if not raw.endswith("'") or "'" in raw[1:-1]:
            raise ValueError("invalid single-quoted environment value")
        return raw[1:-1]
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"'):
            raise ValueError("invalid double-quoted environment value")
        text = raw[1:-1]
        result = []
        index = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                raise ValueError("unescaped quote in environment value")
            if char == "\\" and index + 1 < len(text) and text[index + 1] in '\\"`$':
                index += 1
                char = text[index]
            elif char == "\\" and index + 1 == len(text):
                raise ValueError("trailing environment escape")
            result.append(char)
            index += 1
        return "".join(result)
    result = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            index += 1
            if index == len(raw):
                raise ValueError("continued environment assignments are unsupported")
            char = raw[index]
        result.append(char)
        index += 1
    return "".join(result)


def parse_env_bytes(raw):
    """Parse a strict single-line systemd EnvironmentFile without filesystem I/O."""
    if not isinstance(raw, bytes):
        raise TypeError("environment input must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("environment is not UTF-8") from exc
    result = {}
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        match = _ASSIGN.fullmatch(line)
        if not match:
            raise ValueError("unsupported environment assignment")
        key = match.group(2)
        if key in result:
            raise ValueError("duplicate environment key: " + key)
        result[key] = _decode_value(match.group(3))
    return result


def _encode_value(value):
    if not isinstance(value, str) or any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("environment updates must be single-line strings")
    if "'" not in value:
        return "'" + value + "'"
    return '"' + re.sub(r'([\\"`$])', r'\\\1', value) + '"'


def update_env_bytes(raw, updates):
    """Replace/remove named assignments, preserving every unrelated line byte-for-byte.

    A None update removes that assignment. New assignments use the file's first
    newline style. Existing assignment indentation/export/key spacing survive.
    """
    parsed = parse_env_bytes(raw)
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("invalid environment update key")
        if value is not None:
            _encode_value(value)
    chunks = raw.decode("utf-8").split("\n")
    lines = [chunk + "\n" for chunk in chunks[:-1]]
    if chunks[-1]:
        lines.append(chunks[-1])
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    result = []
    seen = set()
    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = _ASSIGN.fullmatch(content)
        key = match.group(2) if match else None
        if key not in updates:
            result.append(line)
            continue
        seen.add(key)
        if updates[key] is not None:
            # Do not rewrite a value that already has the requested meaning.
            result.append(line if parsed[key] == updates[key] else
                          match.group(1) + _encode_value(updates[key]) + ending)
    additions = [key for key in updates if key not in seen and updates[key] is not None]
    if additions and result and not result[-1].endswith(("\n", "\r")):
        result.append(newline)
    for key in additions:
        result.append(key + "=" + _encode_value(updates[key]) + newline)
    output = "".join(result).encode("utf-8")
    expected = dict(parsed)
    for key, value in updates.items():
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = value
    if parse_env_bytes(output) != expected:
        raise ValueError("environment update did not round-trip")
    return output
