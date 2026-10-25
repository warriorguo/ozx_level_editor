"""`requireFullGround` (OLE-40 / OZX-1265, area-wide OZX-1340).

A hard filter in `RoomTilemapQuery`: only templates whose ground has no
empty cell. The composite builder has no such filter, so a composite room
that asks throws; a pinned template skips the query, so the flag is inert.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.plandefaults import merge_floor  # noqa: E402
from ozxlevel.validate import validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def level(ds):
    doc = ds.get("LevelData", "chapter_1")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def unpinned(level):
    room = next(r for r in level["floors"][0]["rooms"] if not r.get("templateId"))
    return room


def found(ds, code):
    return [i for i in validate_level(ds, "chapter_1") if i["code"] == code]


def test_a_plain_room_may_ask(ds, level):
    unpinned(level)["requireFullGround"] = True
    assert not [c for c in ("ROOM_FULL_GROUND_COMPOSITE", "ROOM_FULL_GROUND_PINNED")
                if found(ds, c)]


def test_a_composite_room_cannot_ask(ds, level):
    room = unpinned(level)
    room.update(requireFullGround=True, roomCols=2)
    assert found(ds, "ROOM_FULL_GROUND_COMPOSITE")[0]["severity"] == "error"


def test_nor_can_one_in_a_full_ground_area(ds, level):
    level["floors"][0]["requireFullGround"] = True
    unpinned(level)["roomRows"] = 2
    bad = found(ds, "ROOM_FULL_GROUND_COMPOSITE")
    assert bad and "its area" in bad[0]["message"]


def test_a_pin_makes_it_inert(ds, level):
    room = next(r for r in level["floors"][0]["rooms"] if r.get("templateId"))
    room["requireFullGround"] = True
    assert found(ds, "ROOM_FULL_GROUND_PINNED")[0]["severity"] == "warning"


def test_the_area_flag_is_on_or_inherited_never_off():
    assert merge_floor({"requireFullGround": True}, {})[0]["requireFullGround"] is True
    assert merge_floor({"requireFullGround": True},
                       {"requireFullGround": False})[0]["requireFullGround"] is True


def test_the_view_carries_the_room_flag(ds, level):
    unpinned(level)["requireFullGround"] = True
    rooms = ds.level_view("chapter_1")["floors"][0]["rooms"]
    assert any(r["requireFullGround"] for r in rooms)
