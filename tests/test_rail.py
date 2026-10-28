"""Rail carts and rail floors (OLE-32 / OLE-33).

OZX-1159 turned the ride from a steam train into a rail cart and renamed
`RoomNodeData.steamTrain` to `railCart`. OZX-1164 then added the floor-wide
version: `FloorPlanData.railCart` lays rail through every room of an area
(`RoomNodeData.requireRail`, stamped per room by the assigner) and parks a
cart in its start room.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
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
    doc = ds.get("LevelData", "level_test")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def first_room(level):
    return level["floors"][0]["rooms"][0]


def found(ds, code):
    return [i for i in validate_level(ds, "level_test") if i["code"] == code]


def test_the_old_name_is_refused_not_read(ds, level):
    first_room(level)["steamTrain"] = True
    bad = found(ds, "LEVEL_ROOM_FIELD_RENAMED")
    assert bad and bad[0]["pointer"].endswith("/steamTrain")


def test_the_new_name_is_not_a_finding(ds):
    assert not found(ds, "LEVEL_ROOM_FIELD_RENAMED")


# ── OZX-1164: requireRail, and a pin that skips the rail filter ──────────


def pin(level, template, **flags):
    room = first_room(level)
    room.update(templateId=template, **flags)
    room.pop("roomCategory", None)
    return room


def test_a_cart_parked_on_a_pin_without_track_is_an_error(ds, level):
    """`normal/all_start_1_02` has no rail; the adapter throws."""
    assert ds.pinned_template_rail("normal/all_start_1_02") is False
    pin(level, "normal/all_start_1_02", railCart=True)
    bad = found(ds, "LEVEL_ROOM_RAIL_MISSING")
    assert bad and bad[0]["severity"] == "error"


def test_track_required_on_a_pin_without_it_warns(ds, level):
    """A pin skips RoomTilemapQuery's rail filter; the ride just ends."""
    pin(level, "normal/all_start_1_02", requireRail=True)
    bad = found(ds, "LEVEL_ROOM_RAIL_MISSING")
    assert bad and bad[0]["severity"] == "warning"


def test_a_pin_with_track_is_fine(ds, level):
    assert ds.pinned_template_rail("test/all_teaching_5_05") is True
    pin(level, "test/all_teaching_5_05", railCart=True, requireRail=True)
    assert not found(ds, "LEVEL_ROOM_RAIL_MISSING")


def test_a_name_in_two_folders_is_two_templates(ds, level):
    """Before OZX-1571 the pool was searched by file name and which copy a
    pin got was load order. The id now carries the folder, so each copy is
    judged on its own track."""
    rails = {f: ds.pinned_template_rail(f"{f}/all_teaching_15_04")
             for f in ("basement", "normal")}
    assert None not in rails.values()
    folder = next((f for f, has in rails.items() if not has), None)
    if folder is None:
        pytest.skip("both copies carry rail now")
    pin(level, f"{folder}/all_teaching_15_04", railCart=True)
    assert found(ds, "LEVEL_ROOM_RAIL_MISSING")


def test_the_floor_switch_reaches_the_plan_view(ds):
    """chapter_5 runs its two basement areas on rail."""
    floors = ds.plan_view("chapter_5")["floors"]
    assert [f["railCart"] for f in floors] == [False, False, True, True]


def test_a_chapter_wide_rail_switch_cannot_be_turned_off_by_a_floor(ds):
    doc = ds.get("LevelBasePlanData", "chapter_5")
    defaults = doc.value["floorDefaults"]
    defaults["railCart"] = True
    doc.value["floors"][0]["railCart"] = False
    try:
        floors = ds.plan_view("chapter_5")["floors"]
        assert all(f["railCart"] for f in floors)
        assert "railCart" in floors[0]["inherited"]
    finally:
        defaults.pop("railCart")
        doc.value["floors"][0].pop("railCart")


def test_the_room_flags_reach_the_level_view(ds):
    rooms = [r for f in ds.level_view("level_test")["floors"] for r in f["rooms"]]
    assert any(r["requireRail"] for r in rooms)
