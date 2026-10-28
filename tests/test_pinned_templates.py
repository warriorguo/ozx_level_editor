"""Story rooms, the `sound` verb, and what a pinned template skips (OLE-35).

OZX-1171 added `stageType: "story"` — a room that only moves the story on —
and played it through two shapes that already existed plus one new verb:
a `trigger` placement and an encounter action `{ "verb": "sound",
"soundId": ... }` (OZX-1173). Its second lesson was about pins: ch2's first
story room pinned a template whose lanes ran on Pipeline, which is solid,
and nothing said so, because a pinned `templateId` never goes through
`RoomTilemapQuery`. `PinnedTemplateDoorTests` guards shipped data upstream;
these rules make the same checks at edit time.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import tilemaps  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import (STAGE_TYPES, validate_all, validate_encounter,  # noqa: E402
                               validate_level)

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


def grid(w, h, value=0):
    return [[value] * h for _ in range(w)]


def template(w=10, h=16, opens=1 | 4):
    """A w×h room, floor everywhere, open Top+Bottom unless told otherwise."""
    return {"ground": grid(w, h, 1), "static": grid(w, h), "pipeline": grid(w, h),
            "bridge": grid(w, h), "rail": grid(w, h), "openDoors": opens}


UP, DOWN, LEFT, RIGHT = 0, 1, 2, 3


def room(*directions, **extra):
    return {"roomId": "r", "doors": [{"direction": d, "toRoomId": "x"} for d in directions],
            **extra}


def codes(room_, tilemap):
    return [c for c, _, _ in tilemaps.pin_door_findings(room_, tilemap)]


# ── geometry ─────────────────────────────────────────────────────────────


def test_door_cells_are_edge_midpoints_on_the_boundary():
    """10×16 → Top=(5,15), Bottom=(5,0), Left=(0,8), Right=(9,8)."""
    assert [tilemaps.door_cell(d, 10, 16) for d in (UP, DOWN, LEFT, RIGHT)] == \
        [(5, 15), (5, 0), (0, 8), (9, 8)]


def test_a_clean_pin_has_no_findings():
    assert codes(room(UP, DOWN), template()) == []


def test_a_door_on_a_side_the_template_walls_off():
    assert codes(room(UP, LEFT), template()) == ["LEVEL_PIN_DOOR_SIDE_CLOSED"]


def test_a_doorway_with_no_floor():
    t = template()
    t["ground"][5][15] = 0
    assert "LEVEL_PIN_DOORWAY_NO_FLOOR" in codes(room(UP, DOWN), t)


def test_a_bridge_is_floor():
    t = template()
    t["ground"][5][15] = 0
    t["bridge"][5][15] = 1
    assert codes(room(UP, DOWN), t) == []


def test_a_pipeline_run_seals_the_room():
    """ch2's first story room: walkable to the nav grid, solid to physics."""
    t = template()
    for x in range(10):
        t["pipeline"][x][8] = 1
    assert codes(room(UP, DOWN), t) == ["LEVEL_PIN_DOORS_UNCONNECTED"]


def test_static_across_the_room_seals_it_too():
    t = template()
    for x in range(10):
        t["static"][x][8] = 1
    assert codes(room(UP, DOWN), t) == ["LEVEL_PIN_DOORS_UNCONNECTED"]


def test_a_pinned_prop_in_a_doorway_seals_it():
    r = room(UP, DOWN, staticPlacements=[
        {"kind": "prop", "cells": [{"x": 4, "y": 14}], "footprintCols": 2, "footprintRows": 2}])
    assert codes(r, template()) == ["LEVEL_PIN_PLACEMENT_IN_DOORWAY"]


@pytest.mark.parametrize("kind", ["skill", "upgrade", "device", "oiltank"])
def test_a_two_by_two_kind_seals_a_doorway_with_no_footprint_authored(kind):
    """These adapters default to a 2×2 block; (4,14) covers the Top door (5,15)."""
    r = room(UP, DOWN, staticPlacements=[{"kind": kind, "cells": [{"x": 4, "y": 14}]}])
    assert codes(r, template()) == ["LEVEL_PIN_PLACEMENT_IN_DOORWAY"]


def test_a_one_by_one_kind_beside_a_doorway_leaves_it_open():
    r = room(UP, DOWN, staticPlacements=[{"kind": "cargo", "cells": [{"x": 4, "y": 14}]}])
    assert codes(r, template()) == []


def test_a_trigger_in_a_doorway_is_walked_over():
    r = room(UP, DOWN, staticPlacements=[{"kind": "trigger", "cells": [{"x": 5, "y": 15}]}])
    assert codes(r, template()) == []


def test_a_run_exit_gate_has_to_be_walkable_to():
    t = template()
    for x in range(10):
        t["pipeline"][x][8] = 1
    r = {"roomId": "r", "doors": [{"direction": DOWN, "toRoomId": "x"},
                                  {"direction": UP, "exitsRun": True}]}
    assert codes(r, t) == ["LEVEL_PIN_DOORS_UNCONNECTED"]


# ── against shipped data ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@needs_data
def test_shipped_pins_match_upstreams_guard(ds):
    """`TheOnlyPinsThatNameNoTilemap_AreTheLegacyCaveDemos`, and every other
    shipped pin passes the door rules — the same verdict upstream reaches."""
    found = [i for i in validate_all(ds) if i["code"].startswith("LEVEL_PIN_")]
    assert {(i["entity"], i["code"]) for i in found} == \
        {("level_cave_demo", "LEVEL_PIN_TEMPLATE_MISSING")}


@needs_data
def test_a_name_in_two_folders_is_two_ids(ds):
    """OZX-1571: `all_teaching_15_04` is in basement/ and normal/. The id
    carries the folder, so each resolves to its own file and the bare name
    to neither — upstream keeps no bare-name compatibility."""
    basement = ds.pinned_templates("basement/all_teaching_15_04")
    normal = ds.pinned_templates("normal/all_teaching_15_04")
    assert len(basement) == len(normal) == 1
    assert basement[0] != normal[0]
    assert ds.pinned_templates("all_teaching_15_04") == []


@needs_data
@pytest.mark.parametrize("bad", ["../normal/all_start_1_02", "normal/../normal/all_start_1_02",
                                 "/normal/all_start_1_02", "normal/"])
def test_an_id_does_not_walk_out_of_its_folder(ds, bad):
    assert ds.pinned_templates(bad) == []


def pin_issues(ds, template):
    doc = ds.get("LevelData", "level_test")
    before = copy.deepcopy(doc.value)
    try:
        target = doc.value["floors"][0]["rooms"][0]
        target["templateId"] = template
        target.pop("roomCategory", None)
        return [i for i in validate_level(ds, "level_test")
                if i["code"] == "LEVEL_PIN_TEMPLATE_MISSING"
                and i["pointer"].startswith("/floors/0/rooms/0")]
    finally:
        doc.value.clear()
        doc.value.update(before)


@needs_data
def test_a_bare_name_is_missing_and_offered_its_folder(ds):
    found = pin_issues(ds, "all_start_1_02")
    assert found and "'normal/all_start_1_02'" in found[0]["message"]
    assert found[0]["fix"]["value"] == "normal/all_start_1_02"


@needs_data
def test_a_bare_name_two_folders_hold_names_both_and_fixes_neither(ds):
    found = pin_issues(ds, "all_teaching_15_04")
    assert found and "basement/all_teaching_15_04" in found[0]["message"] \
        and "normal/all_teaching_15_04" in found[0]["message"]
    assert found[0]["fix"] is None


@needs_data
def test_an_unknown_id_is_missing(ds):
    found = pin_issues(ds, "normal/no_such_template")
    assert found and found[0]["severity"] == "error" and found[0]["fix"] is None


# ── story and sound ──────────────────────────────────────────────────────


def test_story_is_a_stage_type():
    assert "story" in STAGE_TYPES


@needs_data
def test_a_sound_step_needs_only_a_sound_id(ds):
    enc = ds.of_type("EncounterData")[0]
    before = copy.deepcopy(enc.value)
    try:
        enc.value["steps"] = [{"action": {"verb": "sound", "soundId": "sfx_door"}}]
        assert not [i for i in validate_encounter(ds, enc.id)
                    if i["code"].startswith("ENCOUNTER_")]
        enc.value["steps"] = [{"action": {"verb": "sound"}}]
        assert [i["code"] for i in validate_encounter(ds, enc.id)] == ["ENCOUNTER_SOUND_ID_MISSING"]
        enc.value["steps"] = [{"action": {"verb": "dance"}}]
        assert "ENCOUNTER_ACTION_VERB_INVALID" in [
            i["code"] for i in validate_encounter(ds, enc.id)]
    finally:
        enc.value.clear()
        enc.value.update(before)


@needs_data
def test_a_sound_step_gates_the_launches_after_it(ds):
    inline = {"steps": [
        {"condition": {"kind": "touch", "triggerId": "t"},
         "action": {"verb": "sound", "soundId": "sfx"}},
        {"action": {"verb": "launch", "enemyId": "jammer", "min": 1, "max": 1}}]}
    rows = ds.encounter_enemies(None, inline=inline)
    assert [r["id"] for r in rows] == ["jammer"]
    assert rows[0]["gatedByWait"] is True
