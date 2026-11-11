"""Doors as encounter spawners: viaDoor launches and door_close (OLE-88).

OZX-1931: a launch with `viaDoor` "up" / "down" / "left" / "right" brings its
bodies in one by one through that door, which stays open (red) until a
`door_close` step on the same side — the third legal verb, after `launch` and
`sound`, reading `viaDoor` only. `EncounterValidator` refuses a bad side, the
fields a door never reads (`DoorLaunchProblem`), and an unpaired open / close
in playhead order (`ValidateDoorPairing`). OZX-1932 adds `viaDoor: "other"`: a
door besides the entry door and the sides the script names, drawn at
RoomReady. `EncounterRunner.ValidateSpawnerDoors` throws there when a named
side is not a door of the room, or when "other" has no candidate.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_all, validate_encounter, validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

CODES = {"ENCOUNTER_DOOR_SIDE_INVALID", "ENCOUNTER_DOOR_LAUNCH_FIELD_UNREAD",
         "ENCOUNTER_DOOR_UNPAIRED", "ENCOUNTER_ACTION_VERB_INVALID",
         "ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD", "ROOM_ENCOUNTER_DOOR_MISSING",
         "ROOM_ENCOUNTER_DOOR_ONLY_ENTRANCE", "ROOM_ENCOUNTER_DOOR_OTHER_NO_CANDIDATE",
         "ENCOUNTER_ENEMY_MISSING"}
# chapter_1 floor 0: room 4 has doors down / up / right, room 5 only left,
# room 11 down and an up door that leaves the run. Each encounter is used once.
THREE, ONE, EXIT = "spawn_ch1_build2_01", "spawn_ch1_side_01", "spawn_ch1_peak_01"
ROOM = {THREE: "/floors/0/rooms/4", ONE: "/floors/0/rooms/5", EXIT: "/floors/0/rooms/11"}


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues):
    return [(i["code"], i["pointer"]) for i in issues if i["code"] in CODES]


def door(side, enemy="demolition", **extra):
    return {"action": {"verb": "launch", "enemyId": enemy, "min": 3, "max": 3,
                       "viaDoor": side, **extra}}


def close(side, **step):
    return {**step, "action": {"verb": "door_close", "viaDoor": side}}


def script(ds, encounter_id, steps):
    ds.get("EncounterData", encounter_id).value["steps"] = steps


def test_nothing_shipped_trips_a_door_rule(ds):
    assert found(validate_all(ds)) == []


def test_door_close_is_a_verb(ds):
    script(ds, THREE, [door("up"), close("up", condition={"kind": "cleared"})])
    assert found(validate_encounter(ds, THREE)) == []
    assert found(validate_level(ds, "chapter_1")) == []


def test_a_side_must_be_spelled_right(ds):
    script(ds, THREE, [door("north"), close("north"), close("")])
    assert found(validate_encounter(ds, THREE)) == [
        ("ENCOUNTER_DOOR_SIDE_INVALID", "/steps/0/action/viaDoor"),
        ("ENCOUNTER_DOOR_SIDE_INVALID", "/steps/1/action/viaDoor"),
        ("ENCOUNTER_DOOR_SIDE_INVALID", "/steps/2/action/viaDoor"),
        ("ENCOUNTER_DOOR_UNPAIRED", "/steps/2/action")]


def test_every_open_door_is_closed_later_on_the_same_side(ds):
    script(ds, THREE, [close("up"), door("up"), door("up"), door("right"), close("right")])
    assert found(validate_encounter(ds, THREE)) == [
        ("ENCOUNTER_DOOR_UNPAIRED", "/steps/0/action"),
        ("ENCOUNTER_DOOR_UNPAIRED", "/steps/2/action/viaDoor")]


def test_other_pairs_with_other_not_with_a_side(ds):
    script(ds, THREE, [door("other"), close("up")])
    assert sorted(found(validate_encounter(ds, THREE))) == [
        ("ENCOUNTER_DOOR_UNPAIRED", "/steps/0/action/viaDoor"),
        ("ENCOUNTER_DOOR_UNPAIRED", "/steps/1/action")]


def test_a_door_refuses_what_it_never_reads(ds):
    script(ds, THREE, [door("up", viaSpawner="evil_ground_factory", at="zoner",
                            variantIds=["demolition"], clusterSizeMax=3),
                       close("up")])
    assert found(validate_encounter(ds, THREE)) == [
        ("ENCOUNTER_DOOR_LAUNCH_FIELD_UNREAD", f"/steps/0/action/{k}")
        for k in ("viaSpawner", "at", "variantIds", "clusterSizeMax")]


def test_a_train_does_not_walk_in(ds):
    action = {"verb": "launch", "railTrainId": "rail_worm", "min": 1, "max": 1, "viaDoor": "up"}
    script(ds, THREE, [{"action": action}, close("up")])
    assert ("ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD", "/steps/0/action") in found(
        validate_encounter(ds, THREE))


def test_other_verbs_are_still_refused(ds):
    script(ds, THREE, [{"action": {"verb": "door_open", "viaDoor": "up"}}])
    assert ("ENCOUNTER_ACTION_VERB_INVALID", "/steps/0/action/verb") in found(
        validate_encounter(ds, THREE))


def test_a_named_side_must_be_a_door_of_the_room(ds):
    script(ds, THREE, [door("left"), close("left")])
    assert found(validate_level(ds, "chapter_1")) == [
        ("ROOM_ENCOUNTER_DOOR_MISSING", f"{ROOM[THREE]}/encounterId")]


def test_the_only_door_named_outright_is_a_warning(ds):
    script(ds, ONE, [door("left", enemy="small_ground_patrol"), close("left")])
    issues = [i for i in validate_level(ds, "chapter_1") if i["code"] in CODES]
    assert [(i["code"], i["severity"]) for i in issues] == [
        ("ROOM_ENCOUNTER_DOOR_ONLY_ENTRANCE", "warning")]


def test_other_needs_a_door_besides_the_entry_and_the_named(ds):
    # One door: "other" never has a candidate when the player comes in by it.
    script(ds, ONE, [door("other", enemy="small_ground_patrol"), close("other")])
    assert found(validate_level(ds, "chapter_1")) == [
        ("ROOM_ENCOUNTER_DOOR_OTHER_NO_CANDIDATE", f"{ROOM[ONE]}/encounterId")]
    # Three doors, two named: the third is the only candidate, and the player
    # may come in by it.
    script(ds, THREE, [door("up"), door("down"), door("other"),
                       close("up"), close("down"), close("other")])
    assert ("ROOM_ENCOUNTER_DOOR_OTHER_NO_CANDIDATE", f"{ROOM[THREE]}/encounterId") in found(
        validate_level(ds, "chapter_1"))
    # One named: two left, so whichever the player used, one remains.
    script(ds, THREE, [door("up"), door("other"), close("up"), close("other")])
    assert found(validate_level(ds, "chapter_1")) == [
        ("ROOM_ENCOUNTER_DOOR_OTHER_NO_CANDIDATE", f"{ROOM[ONE]}/encounterId")]


def test_a_run_exit_is_never_the_entry(ds):
    script(ds, EXIT, [door("down", enemy="blight_sac_stalker"),
                      door("other", enemy="blight_sac_stalker"),
                      close("down"), close("other")])
    assert found(validate_level(ds, "chapter_1")) == []


def test_a_cave_has_no_doors(ds):
    script(ds, "enc_cave_test_plants", [door("other"), close("other")])
    assert ("ROOM_ENCOUNTER_DOOR_MISSING", "/floors/0/caveLinks/0/encounterId") in found(
        validate_level(ds, "cave_test"))


def test_the_room_view_shows_the_door_and_the_close_gates(ds):
    script(ds, THREE, [door("up"), close("up", condition={"kind": "cleared"}),
                       {"action": {"verb": "launch", "enemyId": "demolition", "min": 1, "max": 1}}])
    rows = ds.encounter_enemies(THREE)
    assert [r["viaDoor"] for r in rows] == ["up", None]
    assert rows[1]["gatedByWait"] is True
