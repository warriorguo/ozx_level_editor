"""Floor layout tests.

The map has to agree with the in-game minimap, so these pin the same
behaviour ``MiniMapLayoutBuilder.BuildGridLayout`` has: BFS the door graph
from the start room, offset by direction, then anchor cave/basement rooms
beside the room you enter them from.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def layout_of(ds, level_id, floor_index=0):
    doc = ds.get("LevelData", level_id)
    return ds.floor_layout(doc.value["floors"][floor_index])


# ── direction offsets match Game.Contracts' DoorDirection ────────────────


def test_start_room_is_the_origin(ds):
    layout = layout_of(ds, "chapter_1")
    assert layout["startRoomId"] == "f0_room_0_0"
    assert layout["positions"]["f0_room_0_0"] == {"x": 0, "y": 0}


def test_up_increments_y_and_right_increments_x(ds):
    pos = layout_of(ds, "chapter_1")["positions"]
    # f0_room_0_0 --Up(0)--> f0_room_0_1
    assert pos["f0_room_0_1"] == {"x": 0, "y": 1}
    # f0_room_0_4 --Right(3)--> f0_room_1_4
    assert pos["f0_room_1_4"] == {"x": 1, "y": 4}


def test_derived_grid_agrees_with_the_room_naming_convention(ds):
    """These ids encode their own grid position; the BFS must reproduce it.

    That is a coincidence of this data rather than a rule, but where the
    convention holds it is a free correctness check on the traversal.
    """
    pos = layout_of(ds, "chapter_1")["positions"]
    for room_id, p in pos.items():
        _, _, sx, sy = room_id.split("_")
        assert (int(sx), int(sy)) == (p["x"], p["y"]), room_id


# ── every room gets placed ───────────────────────────────────────────────


@pytest.mark.parametrize("level_id", [
    "chapter_1", "chapter_2", "level_cave_demo", "level_test",
    "level_generated_tree", "level_generated_cycle",
])
def test_every_room_on_every_floor_is_placed(ds, level_id):
    doc = ds.get("LevelData", level_id)
    for index, floor in enumerate(doc.value["floors"]):
        layout = ds.floor_layout(floor)
        ids = {r["roomId"] for r in floor["rooms"]}
        missing = ids - set(layout["positions"])
        assert not missing, f"{level_id} floor {index}: unplaced {missing}"


@pytest.mark.parametrize("level_id", [
    "chapter_1", "chapter_2", "level_test", "level_generated_cycle",
])
def test_positions_are_unique_per_floor(ds, level_id):
    """Two rooms on one cell would overlap on the map and hide one of them."""
    doc = ds.get("LevelData", level_id)
    for index, floor in enumerate(doc.value["floors"]):
        layout = ds.floor_layout(floor)
        seen = {}
        for room_id, p in layout["positions"].items():
            cell = (p["x"], p["y"])
            assert cell not in seen, \
                f"{level_id} floor {index}: {room_id} and {seen[cell]} both at {cell}"
            seen[cell] = room_id


# ── connectors ───────────────────────────────────────────────────────────


def test_connectors_are_deduplicated(ds):
    """A door pair is one line, not two — production emits from the smaller id."""
    layout = layout_of(ds, "chapter_1")
    pairs = [tuple(sorted((c["from"], c["to"]))) for c in layout["connectors"]
             if not c["crossFloor"]]
    assert len(pairs) == len(set(pairs))


def test_locked_state_is_the_union_of_both_sides(ds):
    """chapter_1 locks f0_room_0_4 -> f0_room_0_5 with key_gold."""
    layout = layout_of(ds, "chapter_1")
    locked = [c for c in layout["connectors"] if c["locked"]]
    assert any({c["from"], c["to"]} == {"f0_room_0_4", "f0_room_0_5"} for c in locked)


def test_cross_floor_doors_are_flagged(ds):
    doc = ds.get("LevelData", "level_test")
    layout = ds.floor_layout(doc.value["floors"][0])
    cross = [c for c in layout["connectors"] if c["crossFloor"]]
    assert cross, "level_test floor 0 has a door to floor 1"
    assert all(c["to"].startswith("f1_") for c in cross)


def test_cycle_level_has_more_links_than_a_tree_would(ds):
    """A tree over n rooms has n-1 edges; a cycle level must exceed that."""
    doc = ds.get("LevelData", "level_generated_cycle")
    floor = doc.value["floors"][0]
    layout = ds.floor_layout(floor)
    same_floor = [c for c in layout["connectors"] if not c["crossFloor"]]
    assert len(same_floor) > len(floor["rooms"]) - 1


# ── sub-room anchoring ───────────────────────────────────────────────────


def test_sub_rooms_anchor_down_and_right_of_their_parent():
    """A cave reached only by a caveLink still gets a position beside its parent."""
    from ozxlevel.dataset import Dataset as D
    floor = {
        "startRoomId": "main",
        "rooms": [
            {"roomId": "main", "doors": [{"direction": 0, "toRoomId": "next"}]},
            {"roomId": "next", "doors": [{"direction": 1, "toRoomId": "main"}],
             "caveLinks": [{"roomIdA": "next", "roomIdB": "cave"}]},
            {"roomId": "cave", "doors": []},
        ],
    }
    layout = D.floor_layout(object.__new__(D), floor)
    assert layout["positions"]["main"] == {"x": 0, "y": 0}
    assert layout["positions"]["next"] == {"x": 0, "y": 1}
    # satellite slot is (1, -1) relative to the parent
    assert layout["positions"]["cave"] == {"x": 1, "y": 0}
    assert layout["subRoomParents"]["cave"] == "next"
