"""WaterPool: the prop that carries enemy spawn points (OLE-72).

`prefab/waterpool` (OZX-1620) is ruled by `GameData/prop_spawn_rules.json`
(OZX-1622): which floor themes it may stand on, its footprint, and — since
OZX-1625 — a ring band that owns where it goes. An encounter launch
`at: "waterpool"` (OZX-1621) draws its points off the pool's rim, so it may
name only enemies that come out of water (OZX-1788: their `habitats` name an
element the prop provides) and runs only in a room certain to place one.
`PropSpawnRuleValidator` holds every level to that at load; generated levels
get the floor defaults, so a plan's placements are held to it too.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import (validate_all, validate_encounter,  # noqa: E402
                               validate_habitat_config, validate_level, validate_plan)

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

PROP_CODES = {"ROOM_PROP_THEME_REFUSED", "ROOM_PROP_FOOTPRINT_MISMATCH",
              "ROOM_PROP_PLACEMENT_REFUSED", "LEVEL_PROP_LAUNCH_UNCERTAIN",
              "LEVEL_CAVE_PROP_LAUNCH", "ENCOUNTER_PROP_CATEGORY_UNRULED",
              "ENCOUNTER_PROP_LAUNCH_NOT_DWELLER", "PROP_SPAWN_RULE_INVALID"}

# battlefield_test f0_room_1_0: a teaching room running spawn_waterpool_02
# beside a certain pool (placement 3).
ROOM = "/floors/0/rooms/1"
POOL = f"{ROOM}/staticPlacements/3"


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues):
    return [(i["code"], i["pointer"]) for i in issues if i["code"] in PROP_CODES]


def room(ds):
    return ds.get("LevelData", "battlefield_test").value["floors"][0]["rooms"][1]


@needs_data
def test_the_shipped_pools_are_clean(ds):
    assert [i for i in validate_all(ds) if i["code"] in PROP_CODES] == []


@needs_data
def test_a_pool_stands_only_on_a_listed_theme(ds):
    ds.get("LevelData", "battlefield_test").value["floors"][0]["themeId"] = "themes/laboratory"
    issues = [i for i in validate_level(ds, "battlefield_test") if i["code"] in PROP_CODES]
    # Area 0 holds two pools (f0_room_1_0 and f0_room_2_1); both are refused.
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("ROOM_PROP_THEME_REFUSED", POOL),
        ("ROOM_PROP_THEME_REFUSED", "/floors/0/rooms/5/staticPlacements/0")]
    assert "themes/battlefield, themes/factory, themes/basement" in issues[0]["message"]


@needs_data
def test_a_pool_declares_the_rules_footprint(ds):
    room(ds)["staticPlacements"][3].pop("footprintRows")
    assert found(validate_level(ds, "battlefield_test")) == [
        ("ROOM_PROP_FOOTPRINT_MISMATCH", f"{POOL}/footprintCols")]


@needs_data
def test_the_ring_band_owns_where_it_goes(ds):
    room(ds)["staticPlacements"][3]["placement"] = "sidewall"
    assert found(validate_level(ds, "battlefield_test")) == [
        ("ROOM_PROP_PLACEMENT_REFUSED", f"{POOL}/placement")]


@needs_data
@pytest.mark.parametrize("change", [
    {"chance": 0.5},       # rolled away in some runs
    {"count": 0},          # places nothing
])
def test_a_launch_needs_a_certain_pool(ds, change):
    room(ds)["staticPlacements"][3].update(change)
    issues = [i for i in validate_level(ds, "battlefield_test") if i["code"] in PROP_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("LEVEL_PROP_LAUNCH_UNCERTAIN", f"{ROOM}/encounterId")]
    assert "not certain to have a 'prefab/waterpool'" in issues[0]["message"]


@needs_data
def test_pinned_cells_or_a_sure_chance_count_as_certain(ds):
    pool = room(ds)["staticPlacements"][3]
    pool.update({"count": 0, "cells": [{"x": 3, "y": 3}]})
    assert found(validate_level(ds, "battlefield_test")) == []
    pool.update({"count": 1, "cells": [], "chance": 1.0})
    assert found(validate_level(ds, "battlefield_test")) == []


@needs_data
def test_a_cave_passage_holds_no_pool(ds):
    floor = ds.get("LevelData", "battlefield_test").value["floors"][0]
    floor["caveLinks"][0]["encounterId"] = "spawn_waterpool_01"
    assert found(validate_level(ds, "battlefield_test")) == [
        ("LEVEL_CAVE_PROP_LAUNCH", "/floors/0/caveLinks/0/encounterId")]


@needs_data
def test_only_a_water_dweller_comes_out(ds):
    steps = ds.get("EncounterData", "spawn_waterpool_01").value["steps"]
    steps[1]["action"]["enemyId"] = "blight_sac_ant"
    steps[3]["action"]["variantIds"] = ["blight_sac_toad", "jammer"]
    steps[3]["action"]["at"] = "WaterPool"     # read case-insensitively
    issues = [i for i in validate_encounter(ds, "spawn_waterpool_01") if i["code"] in PROP_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("ENCOUNTER_PROP_LAUNCH_NOT_DWELLER", "/steps/1/action/enemyId"),
        ("ENCOUNTER_PROP_LAUNCH_NOT_DWELLER", "/steps/3/action/variantIds/1")]
    assert "only an enemy whose habitats name [water]" in issues[0]["message"]


@needs_data
def test_a_category_with_no_rule_is_refused(ds):
    ds.get("PropSpawnRulesData", "prop_spawn_rules").value["rules"][0]["category"] = "lavapool"
    assert ("ENCOUNTER_PROP_CATEGORY_UNRULED", "/steps/0/action/at") in found(
        validate_encounter(ds, "spawn_waterpool_01"))
    assert ("PROP_SPAWN_RULE_INVALID", "/rules/0/category") in found(validate_habitat_config(ds))


@needs_data
@pytest.mark.parametrize("change,pointer", [
    ({"ringMin": 0.7, "ringMax": 0.3}, "/rules/0/ringMin"),
    ({"themeIds": []}, "/rules/0/themeIds"),
    ({"footprintCols": 0}, "/rules/0/footprintCols"),
])
def test_a_malformed_rule_is_refused(ds, change, pointer):
    ds.get("PropSpawnRulesData", "prop_spawn_rules").value["rules"][0].update(change)
    assert found(validate_habitat_config(ds)) == [("PROP_SPAWN_RULE_INVALID", pointer)]


@needs_data
def test_a_pool_that_provides_nothing_lets_nobody_out(ds):
    props = ds.get("PropHabitatsData", "prop_habitats").value["props"]
    next(p for p in props if p["prop"] == "prefab/waterpool")["provides"] = []
    assert found(validate_habitat_config(ds)) == [("PROP_SPAWN_RULE_INVALID", "/rules/0/prefabKey")]


@needs_data
def test_a_plan_pool_is_held_to_the_generated_theme(ds):
    # chapter_4 area 2 is a basement with a 50% pool; a laboratory may not hold one.
    plan = ds.get("LevelBasePlanData", "chapter_4").value
    plan["floors"][2]["themeId"] = "themes/laboratory"
    assert ("ROOM_PROP_THEME_REFUSED", "/floors/2/defaultStaticPlacements/4") in found(
        validate_plan(ds, "chapter_4"))
    # A floor naming no theme runs themes/factory (GeneratedThemeIdOf), which may.
    plan["floors"][2].pop("themeId")
    plan["floorDefaults"].pop("themeId")
    assert found(validate_plan(ds, "chapter_4")) == []


@needs_data
def test_the_room_row_says_where_the_wave_comes_from(ds):
    rows = ds.encounter_enemies("spawn_waterpool_01")
    assert {r["at"] for r in rows} == {"waterpool"}
    assert ds.encounter_enemies("spawn_bubble_01")[0]["at"] is None
