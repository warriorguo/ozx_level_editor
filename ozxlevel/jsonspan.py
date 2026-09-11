"""Span-recording JSON parser and byte-preserving patcher.

GameData is hand-formatted and must stay that way. Inline arrays like
``"cells": [{ "x": 6, "y": 10 }]`` sit next to multi-line ones in the same
file, two files are CRLF, one uses 4-space indent, and all TilemapData is
minified. No pretty-printer reproduces that, so we never reprint a file:
we keep the original text and swap only the byte ranges that actually
changed.

``parse`` returns the decoded value plus a map from JSON Pointer (RFC 6901)
to the ``(start, end)`` offsets of that node's text. ``patch`` applies edits
against those spans, right-to-left so earlier offsets stay valid.

The acceptance test for this module is that opening any of the 386 files and
saving it back without edits is a zero-byte change.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse", "patch", "ParsedDoc", "JsonSpanError", "escape_token"]

WS = " \t\n\r"


class JsonSpanError(ValueError):
    pass


def escape_token(token: str) -> str:
    """Encode one path segment as a JSON Pointer token (RFC 6901)."""
    return token.replace("~", "~0").replace("/", "~1")


class ParsedDoc:
    """A parsed document: original text, decoded value, and node spans."""

    __slots__ = ("text", "value", "spans", "newline", "trailing_newline")

    def __init__(self, text: str, value: Any, spans: dict[str, tuple[int, int]]):
        self.text = text
        self.value = value
        self.spans = spans
        # Preserve the file's own line ending and whether it ends with one.
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.trailing_newline = text.endswith(("\n", "\r"))

    def span(self, pointer: str) -> tuple[int, int]:
        try:
            return self.spans[pointer]
        except KeyError:
            raise JsonSpanError(f"no such node: {pointer!r}") from None

    def text_at(self, pointer: str) -> str:
        start, end = self.span(pointer)
        return self.text[start:end]


# ── parsing ──────────────────────────────────────────────────────────────


def parse(text: str) -> ParsedDoc:
    """Parse *text*, recording the span of every node by JSON Pointer."""
    spans: dict[str, tuple[int, int]] = {}
    i = _skip_ws(text, 0)
    value, i = _parse_value(text, i, "", spans)
    i = _skip_ws(text, i)
    if i != len(text):
        # Trailing whitespace/newline is fine; anything else is not.
        if text[i:].strip():
            raise JsonSpanError(f"trailing content at offset {i}")
    return ParsedDoc(text, value, spans)


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in WS:
        i += 1
    return i


def _parse_value(text: str, i: int, ptr: str, spans: dict) -> tuple[Any, int]:
    if i >= len(text):
        raise JsonSpanError("unexpected end of input")
    ch = text[i]
    start = i
    if ch == "{":
        value, i = _parse_object(text, i, ptr, spans)
    elif ch == "[":
        value, i = _parse_array(text, i, ptr, spans)
    elif ch == '"':
        value, i = _parse_string(text, i)
    else:
        value, i = _parse_literal(text, i)
    spans[ptr] = (start, i)
    return value, i


def _parse_object(text: str, i: int, ptr: str, spans: dict) -> tuple[dict, int]:
    assert text[i] == "{"
    i += 1
    out: dict[str, Any] = {}
    i = _skip_ws(text, i)
    if i < len(text) and text[i] == "}":
        return out, i + 1
    while True:
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != '"':
            raise JsonSpanError(f"expected object key at offset {i}")
        key, i = _parse_string(text, i)
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != ":":
            raise JsonSpanError(f"expected ':' at offset {i}")
        i = _skip_ws(text, i + 1)
        child_ptr = f"{ptr}/{escape_token(key)}"
        value, i = _parse_value(text, i, child_ptr, spans)
        out[key] = value
        i = _skip_ws(text, i)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        if i < len(text) and text[i] == "}":
            return out, i + 1
        raise JsonSpanError(f"expected ',' or '}}' at offset {i}")


def _parse_array(text: str, i: int, ptr: str, spans: dict) -> tuple[list, int]:
    assert text[i] == "["
    i += 1
    out: list[Any] = []
    i = _skip_ws(text, i)
    if i < len(text) and text[i] == "]":
        return out, i + 1
    index = 0
    while True:
        i = _skip_ws(text, i)
        value, i = _parse_value(text, i, f"{ptr}/{index}", spans)
        out.append(value)
        index += 1
        i = _skip_ws(text, i)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        if i < len(text) and text[i] == "]":
            return out, i + 1
        raise JsonSpanError(f"expected ',' or ']' at offset {i}")


_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _parse_string(text: str, i: int) -> tuple[str, int]:
    m = _STRING_RE.match(text, i)
    if not m:
        raise JsonSpanError(f"bad string at offset {i}")
    return json.loads(m.group(0)), m.end()


_LITERAL_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|true|false|null")


def _parse_literal(text: str, i: int) -> tuple[Any, int]:
    m = _LITERAL_RE.match(text, i)
    if not m:
        raise JsonSpanError(f"bad literal at offset {i}: {text[i:i + 12]!r}")
    return json.loads(m.group(0)), m.end()


# ── patching ─────────────────────────────────────────────────────────────


def _render(value: Any) -> str:
    """Render a value in GameData's inline style.

    The convention in this data is that objects carry inner padding and
    arrays do not::

        { "direction": 0, "toRoomId": "f0_room_0_1", "locked": false }
        "cells": [{ "x": 6, "y": 10 }]
        "requiredForExitItemIds": ["key_gold"]

    ``json.dumps`` cannot express the asymmetry, so render it directly.
    """
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {_render(v)}"
                         for k, v in value.items())
        return "{ " + body + " }"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(_render(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def patch(doc: ParsedDoc, edits: list[dict]) -> str:
    """Apply *edits* to ``doc`` and return the new text.

    Each edit is a dict with an ``op``:

    - ``{"op": "set", "path": ptr, "value": v}`` — replace that node's text.
    - ``{"op": "remove", "path": ptr}`` — remove an array element or object
      member, taking one adjacent separator with it.
    - ``{"op": "append", "path": arrayPtr, "value": v}`` — append to an array,
      copying the whitespace style of the element before it.

    Edits are applied from the end of the file backwards so that spans
    recorded against the original text stay valid throughout.
    """
    ops = []
    for edit in edits:
        op = edit.get("op")
        path = edit.get("path", "")
        if op == "set":
            start, end = doc.span(path)
            ops.append((start, end, _render(edit["value"])))
        elif op == "remove":
            ops.append(_removal(doc, path))
        elif op == "append":
            ops.append(_append(doc, path, edit["value"]))
        else:
            raise JsonSpanError(f"unknown op {op!r}")

    # Right-to-left keeps every remaining offset valid.
    ops.sort(key=lambda o: o[0], reverse=True)
    text = doc.text
    last_start = len(text) + 1
    for start, end, replacement in ops:
        if end > last_start:
            raise JsonSpanError("overlapping edits")
        text = text[:start] + replacement + text[end:]
        last_start = start
    return text


def _resolve(value: Any, pointer: str) -> Any:
    """Walk a JSON Pointer into a decoded value."""
    if not pointer:
        return value
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _removal(doc: ParsedDoc, path: str) -> tuple[int, int, str]:
    """Span covering a node plus the separator that joins it to its siblings."""
    start, end = doc.span(path)
    text = doc.text
    parent_ptr, _, token = path.rpartition("/")

    # Removing the only child leaves an empty container. Collapse it to `[]`
    # or `{}` rather than leaving a whitespace-only line behind, which is how
    # GameData writes empty collections anyway.
    parent = _resolve(doc.value, parent_ptr)
    if isinstance(parent, (list, dict)) and len(parent) == 1:
        p_start, p_end = doc.span(parent_ptr)
        return (p_start + 1, p_end - 1, "")

    # An object member's key precedes its value; take the key too.
    if not token.isdigit():
        key_start = text.rfind('"', 0, text.rfind(":", 0, start) + 1)
        key_start = text.rfind('"', 0, key_start)
        start = key_start

    # Eat a following comma if there is one, else a preceding comma.
    after = _skip_ws(text, end)
    if after < len(text) and text[after] == ",":
        end = after + 1
        # also swallow the whitespace up to the next token so indentation
        # of the surviving siblings is untouched
        end = _skip_ws(text, end)
        # ...but keep the line break that belongs to the next element
        line_start = text.rfind("\n", 0, end)
        if line_start > start:
            end = line_start + 1
            start = text.rfind("\n", 0, start) + 1
    else:
        before = start
        while before > 0 and text[before - 1] in WS:
            before -= 1
        if before > 0 and text[before - 1] == ",":
            start = before - 1
    return (start, end, "")


def _append(doc: ParsedDoc, array_ptr: str, value: Any) -> tuple[int, int, str]:
    """Insert *value* before an array's closing bracket, matching its style."""
    start, end = doc.span(array_ptr)
    text = doc.text
    rendered = _render(value)

    existing = _resolve(doc.value, array_ptr)
    if not isinstance(existing, list):
        raise JsonSpanError(f"{array_ptr!r} is not an array")

    close = end - 1  # index of ']'
    if not existing:
        # Empty array: keep it inline, which is how GameData writes them.
        return (close, close, rendered)

    last_ptr = f"{array_ptr}/{len(existing) - 1}"
    last_start, last_end = doc.span(last_ptr)
    between = text[last_end:close]

    if "\n" in between:
        # Multi-line array: copy the indentation of the last element.
        line_start = text.rfind("\n", 0, last_start) + 1
        indent = text[line_start:last_start]
        if indent.strip():
            indent = ""
        sep = "," + doc.newline + indent
    else:
        # Inline array: match whatever spacing separates existing elements.
        sep = ", " if len(existing) < 2 else _inline_sep(doc, array_ptr)
    return (last_end, last_end, sep + rendered)


def _inline_sep(doc: ParsedDoc, array_ptr: str) -> str:
    a_end = doc.span(f"{array_ptr}/0")[1]
    b_start = doc.span(f"{array_ptr}/1")[0]
    return doc.text[a_end:b_start]
