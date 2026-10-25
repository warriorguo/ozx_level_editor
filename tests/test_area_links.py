"""`LevelBasePlanData.areaLinks` and stair links (OLE-36 / OZX-1185, -1186, -1188).

A plan may say how its areas join instead of taking the implicit chain:
each link names `fromArea` → `toArea`, a `door` (a seam, optionally keyed)
or a `stair` (into or out of a basement, on 1..N rooms). chapter_5 is the
shipped reference — `[A1, A2, B1, B2]`, 0→1 keyed door, 0→2 stair on 2–3
rooms, 2→3 door, 3→1 stair. `AreaLinkPlan.Build` throws on every rule
tested here.

The level side is `LevelValidator.ValidateStairLinks`, which this tool did
not check at all before; OZX-1188 added "one stair end per room" to it.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level, validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def restoring(ds, data_type, entity_id):
    doc = ds.get(data_type, entity_id)
    before = copy.deepcopy(doc.value)

    def restore():
        doc.value.clear()
        doc.value.update(before)
    return doc.value, restore


@pytest.fixture
def plan(ds):
    value, restore = restoring(ds, "LevelBasePlanData", "chapter_5")
    yield value
    restore()


@pytest.fixture
def level(ds):
    value, restore = restoring(ds, "LevelData", "level_test")
    yield value
    restore()


def plan_codes(ds, plan_id="chapter_5"):
    return [i["code"] for i in validate_plan(ds, plan_id)]


def level_codes(ds):
    return [i["code"] for i in validate_level(ds, "level_test")]


# ── plans ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("plan_id", ["chapter_4", "chapter_5", "chapter_6"])
def test_the_shipped_links_are_clean(ds, plan_id):
    assert not [c for c in plan_codes(ds, plan_id) if c.startswith("LEVEL_AREA")]


@pytest.mark.parametrize("link,code", [
    ({"fromArea": 0, "toArea": 9, "kind": "door"}, "LEVEL_AREA_LINK_OUT_OF_RANGE"),
    ({"fromArea": 1, "toArea": 1, "kind": "door"}, "LEVEL_AREA_LINK_SELF"),
    ({"fromArea": 1, "toArea": 0, "kind": "door"}, "LEVEL_AREA_LINK_INTO_START"),
    ({"fromArea": 0, "toArea": 3, "kind": "lift"}, "LEVEL_AREA_LINK_KIND_UNKNOWN"),
    ({"fromArea": 0, "toArea": 3, "kind": "door", "stairRoomsMax": 2},
     "LEVEL_AREA_LINK_FIELD_MISPLACED"),
])
def test_a_malformed_link_is_refused(ds, plan, link, code):
    plan["areaLinks"].append(link)
    assert code in plan_codes(ds)


def test_two_areas_are_joined_once(ds, plan):
    plan["areaLinks"].append({"fromArea": 0, "toArea": 1, "kind": "stair"})
    assert "LEVEL_AREA_LINK_DUPLICATE" in plan_codes(ds)


def test_a_key_on_a_stair_gates_nothing(ds, plan):
    stair = next(l for l in plan["areaLinks"] if l["kind"] == "stair")
    stair["keyId"] = "key_gold"
    assert "LEVEL_AREA_LINK_FIELD_MISPLACED" in plan_codes(ds)


def test_a_key_on_a_door_must_exist(ds, plan):
    door = next(l for l in plan["areaLinks"] if l.get("keyId"))
    door["keyId"] = "key_nope"
    assert "DATA_REFERENCE_MISSING" in plan_codes(ds)


def test_a_reversed_stair_room_range_is_refused(ds, plan):
    stair = next(l for l in plan["areaLinks"] if l.get("stairRoomsMax"))
    stair.update(stairRoomsMin=3, stairRoomsMax=2)
    assert "LEVEL_AREA_LINK_STAIR_ROOMS_INVALID" in plan_codes(ds)


def test_a_stair_between_areas_of_one_height_has_no_direction(ds, plan):
    plan["floors"][2]["elevation"] = 0
    assert "LEVEL_AREA_LINK_STAIR_LEVEL" in plan_codes(ds)


def test_a_basement_above_its_stairs_reads_upside_down(ds, plan):
    for f in (2, 3):
        plan["floors"][f]["elevation"] = 1
    assert "LEVEL_AREA_LINK_STAIR_LEVEL" in plan_codes(ds)


def test_a_floor_key_is_refused_once_links_are_declared(ds, plan):
    plan["floors"][0]["transitionKeyId"] = "key_gold"
    assert "LEVEL_AREA_LINK_FLOOR_KEY" in plan_codes(ds)


def test_a_static_floor_cannot_be_linked(ds, plan):
    plan["floors"][1]["generatorType"] = "static"
    assert "LEVEL_AREA_LINK_STATIC_FLOOR" in plan_codes(ds)


def test_an_area_holds_one_seam_each_way(ds, plan):
    plan["areaLinks"].append({"fromArea": 0, "toArea": 3, "kind": "door"})
    assert "LEVEL_AREA_LINK_FAN_OUT" in plan_codes(ds)


def test_a_multi_room_landing_must_be_left_by_a_door(ds, plan):
    """B1 is entered on 2–3 rooms; take away its door on and the stairs
    back cannot be told from a way onward."""
    plan["areaLinks"] = [l for l in plan["areaLinks"] if (l["fromArea"], l["toArea"]) != (2, 3)]
    plan["areaLinks"].append({"fromArea": 3, "toArea": 2, "kind": "door"})
    assert "LEVEL_AREA_LINK_LANDING_NEEDS_DOOR" in plan_codes(ds)


def test_an_area_no_link_reaches_is_never_entered(ds, plan):
    plan["areaLinks"] = [l for l in plan["areaLinks"] if 3 not in (l["fromArea"], l["toArea"])]
    assert "LEVEL_AREA_UNREACHABLE" in plan_codes(ds)


def test_a_plan_without_links_takes_the_chain(ds):
    assert not [c for c in plan_codes(ds, "chapter_3") if c.startswith("LEVEL_AREA")]


def test_the_view_names_the_links_and_the_basements(ds):
    view = ds.plan_view("chapter_5")
    assert [(l["fromArea"], l["toArea"], l["kind"]) for l in view["areaLinks"]] == \
        [(0, 1, "door"), (0, 2, "stair"), (2, 3, "door"), (3, 1, "stair")]
    assert view["areaLinks"][1]["stairRooms"] == [2, 3]
    assert [f["category"] for f in view["floors"]] == ["factory", "factory", "basement", "basement"]


# ── levels: ValidateStairLinks ───────────────────────────────────────────


def stairs(level):
    return next((fi, f) for fi, f in enumerate(level["floors"]) if f.get("stairLinks"))


def test_the_shipped_stairs_are_clean(ds):
    for level_id in ("level_test", "factory2_test", "battlefield_test"):
        assert not [i for i in validate_level(ds, level_id) if "STAIR" in i["code"]], level_id


def test_a_stair_without_its_twin_is_one_way(ds, level):
    fi, floor = stairs(level)
    link = floor["stairLinks"][0]
    level["floors"][link["targetFloor"]]["stairLinks"] = [
        t for t in level["floors"][link["targetFloor"]]["stairLinks"]
        if not (t.get("roomIdA") == link["roomIdB"] and t.get("roomIdB") == link["roomIdA"])]
    assert "LEVEL_STAIR_TWIN_MISSING" in level_codes(ds)


def test_a_stair_end_on_another_floor_is_refused(ds, level):
    fi, floor = stairs(level)
    floor["stairLinks"][0]["roomIdA"] = "no_such_room"
    assert "LEVEL_STAIR_ROOM_NOT_ON_FLOOR" in level_codes(ds)


def test_a_stair_to_its_own_floor_is_refused(ds, level):
    fi, floor = stairs(level)
    floor["stairLinks"][0]["targetFloor"] = fi
    assert "LEVEL_STAIR_TARGET_INVALID" in level_codes(ds)


def test_a_room_holds_one_stair_end(ds, level):
    fi, floor = stairs(level)
    extra = dict(floor["stairLinks"][0], caveId="extra")
    floor["stairLinks"].append(extra)
    assert "LEVEL_STAIR_ROOM_TWO_ENDS" in level_codes(ds)


def test_a_cave_floor_may_stack_stairs(ds, level):
    fi, floor = stairs(level)
    floor["stairLinks"].append(dict(floor["stairLinks"][0], caveId="extra"))
    floor["themeId"] = "themes/cave"
    assert "LEVEL_STAIR_ROOM_TWO_ENDS" not in level_codes(ds)


def test_a_stair_between_floors_of_one_height_has_no_direction(ds, level):
    for f in level["floors"]:
        f["elevation"] = 0
    assert "LEVEL_STAIR_NO_DIRECTION" in level_codes(ds)
