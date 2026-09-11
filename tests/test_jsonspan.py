"""Acceptance tests for the byte-preserving JSON editor.

The headline test is the round trip: open every one of the 386 production
files, make no edit, write it back, and assert zero byte difference. That is
the only thing that proves the writer will not detonate a diff the first time
a designer saves.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import jsonspan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
STREAMING = OZX_BASE / "Assets/StreamingAssets"


def all_data_files():
    if not STREAMING.exists():
        return []
    return sorted(
        list((STREAMING / "GameData").rglob("*.json"))
        + list((STREAMING / "TilemapData").rglob("*.json"))
    )


FILES = all_data_files()


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
def test_corpus_is_the_expected_size():
    # The TD's snapshot was 363 + 23 at ozx_base@d867b644. GameData grows, so
    # this is a floor rather than an equality — the point is that the round
    # trip below is running against the whole corpus, not a handful of files.
    game = [f for f in FILES if "GameData" in f.parts]
    tile = [f for f in FILES if "TilemapData" in f.parts]
    assert len(game) >= 363, f"only {len(game)} GameData files found"
    assert len(tile) == 23, f"expected 23 tilemaps, found {len(tile)}"


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_open_then_save_is_byte_identical(path):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    doc = jsonspan.parse(text)
    out = jsonspan.patch(doc, [])
    assert out.encode("utf-8") == raw, f"{path} changed on a no-op save"


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_decoded_value_matches_stdlib(path):
    text = path.read_text(encoding="utf-8")
    assert jsonspan.parse(text).value == json.loads(text)


# ── the formatting outliers, pinned explicitly ───────────────────────────


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
@pytest.mark.parametrize("rel", [
    "GameData/projectiles/small_red_bullet.json",
    "GameData/rooms/room_cave_01.json",
])
def test_crlf_files_keep_crlf(rel):
    raw = (STREAMING / rel).read_bytes()
    assert b"\r\n" in raw
    doc = jsonspan.parse(raw.decode("utf-8"))
    assert jsonspan.patch(doc, []).encode("utf-8") == raw


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
def test_four_space_file_keeps_indent():
    path = STREAMING / "GameData/skills/resilient_string_throw.json"
    raw = path.read_bytes()
    doc = jsonspan.parse(raw.decode("utf-8"))
    assert jsonspan.patch(doc, []).encode("utf-8") == raw


@pytest.mark.skipif(not FILES, reason="sibling ozx_base checkout not found")
def test_minified_tilemaps_stay_minified():
    tiles = [f for f in FILES if "TilemapData" in f.parts]
    no_newline = [f for f in tiles if not f.read_bytes().endswith(b"\n")]
    assert len(no_newline) == 22, "expected 22 tilemaps with no trailing newline"
    for path in tiles:
        raw = path.read_bytes()
        doc = jsonspan.parse(raw.decode("utf-8"))
        assert jsonspan.patch(doc, []).encode("utf-8") == raw


# ── targeted edits leave everything else alone ───────────────────────────

INLINE_SAMPLE = """{
  "dataType": "LevelData",
  "id": "chapter_1",
  "floors": [
    {
      "index": 0,
      "rooms": [
        {
          "roomId": "f0_room_0_0",
          "stageType": "start",
          "doors": [
            { "direction": 0, "toRoomId": "f0_room_0_1", "toDoorId": 1, "locked": false }
          ],
          "staticPlacements": [
            { "kind": "cargo", "cells": [{ "x": 4, "y": 11 }] }
          ]
        }
      ]
    }
  ]
}
"""


def test_set_scalar_touches_only_that_value():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [
        {"op": "set", "path": "/floors/0/rooms/0/stageType", "value": "teaching"},
    ])
    assert '"stageType": "teaching"' in out
    # The inline door array is untouched, on one line, spaces intact.
    assert '{ "direction": 0, "toRoomId": "f0_room_0_1", "toDoorId": 1, "locked": false }' in out
    assert '"cells": [{ "x": 4, "y": 11 }]' in out
    assert len(out.splitlines()) == len(INLINE_SAMPLE.splitlines())


def test_append_to_inline_array_stays_inline():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [
        {"op": "append",
         "path": "/floors/0/rooms/0/staticPlacements/0/cells",
         "value": {"x": 9, "y": 2}},
    ])
    # the appended object adopts GameData's inner-padding style, matching the
    # element already there rather than json.dumps' default
    assert '"cells": [{ "x": 4, "y": 11 }, { "x": 9, "y": 2 }]' in out
    assert len(out.splitlines()) == len(INLINE_SAMPLE.splitlines())


def test_append_to_multiline_array_matches_indent():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [
        {"op": "append",
         "path": "/floors/0/rooms/0/doors",
         "value": {"direction": 3, "toRoomId": "f0_room_1_0", "toDoorId": 2, "locked": False}},
    ])
    assert json.loads(out)["floors"][0]["rooms"][0]["doors"][1]["direction"] == 3
    lines = [l for l in out.splitlines() if '"direction"' in l]
    assert len(lines) == 2
    # both door lines carry the same indentation
    assert len(lines[0]) - len(lines[0].lstrip()) == len(lines[1]) - len(lines[1].lstrip())


def test_remove_array_element():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [
        {"op": "remove", "path": "/floors/0/rooms/0/doors/0"},
    ])
    assert json.loads(out)["floors"][0]["rooms"][0]["doors"] == []


def test_multiple_edits_apply_together():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [
        {"op": "set", "path": "/floors/0/rooms/0/stageType", "value": "peak"},
        {"op": "set", "path": "/floors/0/rooms/0/roomId", "value": "renamed"},
        {"op": "set", "path": "/id", "value": "chapter_9"},
    ])
    parsed = json.loads(out)
    assert parsed["id"] == "chapter_9"
    assert parsed["floors"][0]["rooms"][0]["roomId"] == "renamed"
    assert parsed["floors"][0]["rooms"][0]["stageType"] == "peak"


def test_unicode_survives_round_trip():
    src = '{\n  "displayName": "\\u5de5\\u5382",\n  "raw": "工厂"\n}\n'
    doc = jsonspan.parse(src)
    assert jsonspan.patch(doc, []) == src
    assert doc.value["raw"] == "工厂"


# ── render style matches GameData's conventions ──────────────────────────


def test_rendered_objects_carry_inner_padding():
    from ozxlevel.jsonspan import _render
    assert _render({"direction": 0, "locked": False}) == '{ "direction": 0, "locked": false }'
    # arrays do not pad, objects inside them still do
    assert _render([{"x": 6, "y": 10}]) == '[{ "x": 6, "y": 10 }]'
    assert _render(["key_gold"]) == '["key_gold"]'
    assert _render({}) == "{}"
    assert _render([]) == "[]"


def test_removing_sole_element_collapses_to_empty_array():
    src = ('{\n  "doors": [\n'
           '    { "direction": 2, "toRoomId": "a", "locked": false }\n'
           '  ]\n}\n')
    doc = jsonspan.parse(src)
    out = jsonspan.patch(doc, [{"op": "remove", "path": "/doors/0"}])
    assert '"doors": []' in out
    assert json.loads(out)["doors"] == []
    # no whitespace-only leftovers
    assert not any(line.strip() == "" and line for line in out.splitlines())


def test_appended_door_matches_surrounding_inline_style():
    doc = jsonspan.parse(INLINE_SAMPLE)
    out = jsonspan.patch(doc, [{
        "op": "append", "path": "/floors/0/rooms/0/doors",
        "value": {"direction": 3, "toRoomId": "b", "toDoorId": 2, "locked": False},
    }])
    assert '{ "direction": 3, "toRoomId": "b", "toDoorId": 2, "locked": false }' in out
