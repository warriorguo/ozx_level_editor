"""Areas joined by ordinary doors (OLE-28 / OZX-1116).

A `FloorData` is an AREA — a deployment set — not a storey. Areas are laid
side by side and joined by an ordinary twin pair of doors, because
`FloorGraph.Build(LevelData)` now indexes every room of the level at once.
Stairs did not go away; they were reassigned to the vertical side-branches
(caves, basements) a room drills down into.

Three rules moved with that, and chapter_2 is the shipped reference: its
`f0_room_1_5` Up door meets `f1_room_1_0`'s Down door, and `f1_room_1_0`
stopped being a `start`.
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

LEVEL = "chapter_2"
EXIT, ENTRY = "f0_room_1_5", "f1_room_1_0"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def level(ds):
    doc = ds.get("LevelData", LEVEL)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def room(level, room_id):
    return next(r for f in level["floors"] for r in f["rooms"] if r["roomId"] == room_id)


def found(ds, code, level_id=LEVEL):
    return [i for i in validate_level(ds, level_id) if i["code"] == code]


def test_the_seam_is_an_ordinary_twin_pair(ds, level):
    up = next(d for d in room(level, EXIT)["doors"] if d["toRoomId"] == ENTRY)
    down = next(d for d in room(level, ENTRY)["doors"] if d["toRoomId"] == EXIT)
    assert (up["direction"], down["direction"]) == (0, 1)
    assert not any(f.get("stairLinks") for f in level["floors"])


@pytest.mark.parametrize("level_id", ["chapter_2", "level_test",
                                      "level_generated_tree", "level_generated_cycle"])
def test_a_door_into_another_area_is_legal(ds, level_id):
    """These used to be dead data (`GetRoom` returned null across areas) and
    walk now. Every one of them is a twin pair, so none is a one-way trap."""
    codes = {i["code"] for i in validate_level(ds, level_id)}
    assert not codes & {"LEVEL_CROSS_FLOOR_DOOR_NO_STAIR", "LEVEL_DOOR_TARGET_MISSING",
                        "LEVEL_DOOR_TWIN_MISSING"}, codes


def test_a_seam_without_its_return_door_is_one_way(ds, level):
    room(level, ENTRY)["doors"] = [d for d in room(level, ENTRY)["doors"]
                                   if d["toRoomId"] != EXIT]
    twin = found(ds, "LEVEL_DOOR_TWIN_MISSING")
    assert twin and EXIT in twin[0]["message"]
    # The fix creates the return door in the OTHER area's room.
    assert twin[0]["fix"]["path"].startswith("/floors/1/")


def test_a_return_door_facing_the_wrong_way_is_not_a_twin(ds, level):
    down = next(d for d in room(level, ENTRY)["doors"] if d["toRoomId"] == EXIT)
    down["direction"] = 2
    assert found(ds, "LEVEL_DOOR_TWIN_MISSING")


def test_a_wrong_arrival_door_is_a_warning(ds, level):
    up = next(d for d in room(level, EXIT)["doors"] if d["toRoomId"] == ENTRY)
    up["toDoorId"] = 3
    bad = found(ds, "LEVEL_DOOR_ARRIVAL_MISMATCH")
    assert bad and bad[0]["severity"] == "warning"
    assert bad[0]["fix"]["value"] == 1


def test_a_room_id_reused_in_another_area_is_refused(ds, level):
    """`Build(LevelData)` throws on it: one flat index for the whole level."""
    room(level, ENTRY)["roomId"] = "f0_room_1_0"
    dup = found(ds, "LEVEL_ROOM_ID_DUPLICATE")
    assert dup and "area 0" in dup[0]["message"]


def test_start_belongs_to_the_first_area_only(ds, level):
    """`start` has only mask-1 tilemaps; a later area's entry room carries a
    return door (mask 5), so RoomTilemapQuery would throw."""
    room(level, ENTRY)["stageType"] = "start"
    bad = found(ds, "LEVEL_START_STAGE_NOT_FIRST_AREA")
    assert bad and bad[0]["severity"] == "error"
    assert bad[0]["pointer"].startswith("/floors/1/")


def test_start_on_the_first_area_is_fine(ds):
    assert not found(ds, "LEVEL_START_STAGE_NOT_FIRST_AREA", "chapter_1")


def test_the_map_names_the_area_a_seam_leads_to(ds):
    view = ds.level_view(LEVEL)
    stub = next(c for c in view["floors"][0]["layout"]["connectors"]
                if c["crossFloor"])
    assert (stub["to"], stub["toArea"], stub["direction"]) == (ENTRY, 1, "N")


# ── the run-exit door (chapter_2's last room) ────────────────────────────

FINAL = "f1_room_1_5"


def exit_door(level):
    return next(d for d in room(level, FINAL)["doors"] if d.get("exitsRun"))


def test_a_run_exit_door_needs_no_target(ds):
    codes = {i["code"] for i in validate_level(ds, LEVEL)}
    assert not {c for c in codes if c.startswith("LEVEL_DOOR")}, codes


def test_a_run_exit_door_that_also_goes_somewhere_is_refused(ds, level):
    exit_door(level)["toRoomId"] = "f1_room_1_4"
    assert found(ds, "LEVEL_DOOR_EXIT_HAS_TARGET")


def test_a_locked_run_exit_warns(ds, level):
    exit_door(level).update(locked=True, keyId="key_gold")
    assert found(ds, "LEVEL_DOOR_EXIT_LOCKED")[0]["severity"] == "warning"


def test_a_run_exit_in_a_final_room_is_never_reached(ds, level):
    room(level, FINAL)["isFinalRoom"] = True
    assert found(ds, "LEVEL_DOOR_EXIT_WITH_FINAL_ROOM")
