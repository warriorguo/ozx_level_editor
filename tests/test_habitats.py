"""Habitats: what an enemy dwells in, and what a floor can offer (OLE-75 / OLE-78).

OZX-1788 gave enemies `habitats`, props a habitat mapping
(`prop_habitats.json`) and themes a ground (`theme_habitats.json`).
OZX-1802 settled the meaning: an enemy's habitats never restrict where it
goes — a room offering an element weights its dwellers up — but a floor that
can offer an element some enemy dwells in must list one of them
(`LevelPlanValidator.ValidateHabitats`: water calls for its creatures). Every
environment prop needs a mapping (`Habitats.ValidateLevelProps`), every theme
an entry, and every element an enemy names must be one something provides
(`Habitats.ValidateAll`, at boot).
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import (validate_all, validate_enemy,  # noqa: E402
                               validate_habitat_config, validate_level, validate_plan)

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

HABITAT_CODES = {"ROOM_PROP_HABITAT_UNMAPPED", "LEVEL_THEME_HABITAT_MISSING",
                 "LEVEL_HABITAT_DWELLER_MISSING", "ENEMY_HABITAT_UNPROVIDED",
                 "HABITAT_CONFIG_INVALID", "DATA_CONFIG_MISSING"}
WATER_DWELLERS = {"blight_sac_toad", "blight_sac_wombvine1", "blight_sac_wombvine2"}
BASEMENT = 2   # chapter_4 area 2: themes/basement, a 50% WaterPool in its defaults


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues):
    return [(i["code"], i["pointer"]) for i in issues if i["code"] in HABITAT_CODES]


def plan(ds):
    return ds.get("LevelBasePlanData", "chapter_4").value


@needs_data
def test_the_shipped_data_is_clean(ds):
    assert [i for i in validate_all(ds) if i["code"] in HABITAT_CODES] == []


@needs_data
def test_water_on_the_floor_calls_for_a_dweller(ds):
    floor = plan(ds)["floors"][BASEMENT]
    floor["enemyPool"] = [e for e in floor["enemyPool"] if e["enemyId"] not in WATER_DWELLERS]
    issues = [i for i in validate_plan(ds, "chapter_4") if i["code"] in HABITAT_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("LEVEL_HABITAT_DWELLER_MISSING", f"/floors/{BASEMENT}/enemyPool")]
    assert "can offer 'water'" in issues[0]["message"]
    assert "themes/basement" in issues[0]["message"]
    # A variant counts as a pool body.
    floor["enemyPool"][0].setdefault("variantIds", []).append("blight_sac_toad")
    assert found(validate_plan(ds, "chapter_4")) == []


@needs_data
def test_no_water_asks_for_nothing(ds):
    # The element comes from the pool prop; with it gone nothing calls for a
    # dweller — and an element no enemy dwells in (industrial, oil) never does.
    floor = plan(ds)["floors"][BASEMENT]
    floor["enemyPool"] = [e for e in floor["enemyPool"] if e["enemyId"] not in WATER_DWELLERS]
    floor["defaultStaticPlacements"] = [
        p for p in floor["defaultStaticPlacements"] if p.get("prefabKey") != "prefab/waterpool"]
    assert found(validate_plan(ds, "chapter_4")) == []


@needs_data
def test_a_themes_ground_offers_its_element(ds):
    # Battlefield sand: quick_sand_worm dwells there, and chapter_4 lists none.
    plan(ds)["floors"][0]["themeId"] = "themes/battlefield"
    issues = [i for i in validate_plan(ds, "chapter_4") if i["code"] in HABITAT_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("LEVEL_HABITAT_DWELLER_MISSING", "/floors/0/enemyPool")]
    assert "'sand'" in issues[0]["message"] and "quick_sand_worm" in issues[0]["message"]


@needs_data
def test_an_unlisted_theme_is_refused_where_it_is_written(ds):
    # Areas 0 and 1 take their theme from floorDefaults.
    plan(ds)["floorDefaults"]["themeId"] = "themes/nowhere"
    issues = [i for i in validate_plan(ds, "chapter_4") if i["code"] in HABITAT_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("LEVEL_THEME_HABITAT_MISSING", "/floorDefaults/themeId")]
    assert "floors 0, 1" in issues[0]["message"]


@needs_data
def test_every_environment_prop_is_mapped(ds):
    room = ds.get("LevelData", "level_test").value["floors"][0]["rooms"][1]
    room["staticPlacements"].append({"kind": "prop", "prefabKey": "prefab/mystery", "count": 1})
    # Pickups and logic are not environment.
    room["staticPlacements"].append({"kind": "equipment", "count": 1})
    pi = len(room["staticPlacements"]) - 2
    assert found(validate_level(ds, "level_test")) == [
        ("ROOM_PROP_HABITAT_UNMAPPED", f"/floors/0/rooms/1/staticPlacements/{pi}")]


@needs_data
def test_a_plan_prop_is_mapped_too(ds):
    plan(ds)["floorDefaults"].setdefault("defaultStaticPlacements", []).append(
        {"kind": "device", "count": 1})
    props = ds.get("PropHabitatsData", "prop_habitats").value["props"]
    props[:] = [p for p in props if p["prop"] != "device"]
    floors = plan(ds)["floors"]
    for fi, f in enumerate(floors):
        if "defaultStaticPlacements" not in f:
            continue
        f["defaultStaticPlacements"].append({"kind": "device", "count": 1})
        pi = len(f["defaultStaticPlacements"]) - 1
        assert ("ROOM_PROP_HABITAT_UNMAPPED",
                f"/floors/{fi}/defaultStaticPlacements/{pi}") in found(validate_plan(ds, "chapter_4"))


@needs_data
def test_an_element_nothing_provides_is_a_typo(ds):
    ds.get("EnemyData", "tripodonte").value["habitats"] = ["water", "swamp"]
    issues = [i for i in validate_enemy(ds, "tripodonte") if i["code"] in HABITAT_CODES]
    assert [(i["code"], i["pointer"]) for i in issues] == [
        ("ENEMY_HABITAT_UNPROVIDED", "/habitats/1")]


@needs_data
def test_the_libraries_are_checked(ds):
    ds.get("ThemeHabitatsData", "theme_habitats").value["dwellerWeightScale"] = 0.5
    props = ds.get("PropHabitatsData", "prop_habitats").value["props"]
    props.append(dict(props[0]))
    assert found(validate_habitat_config(ds)) == [
        ("HABITAT_CONFIG_INVALID", "/dwellerWeightScale"),
        ("HABITAT_CONFIG_INVALID", f"/props/{len(props) - 1}/prop")]


@needs_data
def test_a_missing_library_is_reported(ds):
    del ds.docs[("ThemeHabitatsData", "theme_habitats")]
    assert ("DATA_CONFIG_MISSING", None) in found(validate_habitat_config(ds))


@needs_data
def test_the_plan_view_shows_what_a_floor_can_offer(ds):
    floors = ds.plan_view("chapter_4")["floors"]
    assert floors[0]["habitats"]["themeId"] == "themes/factory"
    assert "water" not in floors[0]["habitats"]["possible"]
    assert floors[BASEMENT]["habitats"] == {
        "themeId": "themes/basement", "possible": ["industrial", "oil", "water"]}
    pool = {e["enemyId"]: e["habitats"] for e in floors[BASEMENT]["enemyPool"]}
    assert pool["blight_sac_toad"] == ["water"] and pool["blight_sac_ant"] == []
    gives = {p["prefabKey"] or p["kind"]: p["provides"]
             for p in floors[BASEMENT]["defaultStaticPlacements"]}
    assert gives["prefab/waterpool"] == ["water"] and gives["cargo"] == []
    meta = {e["id"]: e["meta"] for e in ds.enemy_catalog()}
    assert "dwells in water" in meta["blight_sac_toad"]
