"""`LevelBasePlanData.floorDefaults` (OLE-9 / OZX-1011).

The merge is `FloorPlanDefaults.Merge`'s, rule for rule — per field, one
level into `floorLootPlan`, arrays as leaves, "written" read off the value —
and the point of having it here is that every finding and every pointer
lands on the node that holds the value. A default that is wrong is wrong
once, in `/floorDefaults`, however many floors inherit it.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import plandefaults  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.plandefaults import DEFAULTS, FLOOR, merge_floor  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"


# ── the merge, on plain dicts ────────────────────────────────────────────


def test_a_floor_that_writes_nothing_is_the_defaults():
    merged, source = merge_floor({"themeId": "t", "enemiesPerRoom": 12}, {})
    assert merged == {"themeId": "t", "enemiesPerRoom": 12}
    assert source == {"themeId": DEFAULTS, "enemiesPerRoom": DEFAULTS}


def test_a_written_field_overrides():
    merged, source = merge_floor({"enemiesPerRoom": 12}, {"enemiesPerRoom": 8})
    assert merged["enemiesPerRoom"] == 8 and source["enemiesPerRoom"] == FLOOR


@pytest.mark.parametrize("blank", [0, "", [], None])
def test_zero_and_empty_mean_not_written(blank):
    """JsonUtility cannot tell omitted from 0 / "" / [], so neither can the
    merge — a floor cannot override a default back to blank."""
    merged, source = merge_floor({"x": 5}, {"x": blank})
    assert merged["x"] == 5 and source["x"] == DEFAULTS


def test_arrays_replace_and_never_append():
    merged, _ = merge_floor({"enemyPool": [{"enemyId": "a"}, {"enemyId": "b"}]},
                            {"enemyPool": [{"enemyId": "c"}]})
    assert merged["enemyPool"] == [{"enemyId": "c"}]


def test_whole_blocks_are_inherited_by_their_discriminator():
    """`treeShape` is declared iff trunkLengthMax > 0 — so a floor's block
    with a meant zero elsewhere but no trunk is NOT declared, and the whole
    chapter block wins rather than being merged into it field by field."""
    chapter = {"treeShape": {"trunkLengthMax": 6, "branchChance": 0.5}}
    merged, source = merge_floor(chapter, {"treeShape": {"branchChance": 0}})
    assert merged["treeShape"] == chapter["treeShape"]
    assert source["treeShape"] == DEFAULTS
    merged, _ = merge_floor(chapter, {"treeShape": {"trunkLengthMax": 3}})
    assert merged["treeShape"] == {"trunkLengthMax": 3}


def test_the_loot_plan_is_entered_one_level():
    chapter = {"floorLootPlan": {"stageWeights": [{"stageType": "peak"}],
                                 "categoryCaps": [{"category": "weapon"}]}}
    merged, source = merge_floor(chapter, {"floorLootPlan": {
        "probabilistic": [{"itemId": "x"}], "categoryCaps": [{"category": "skill"}]}})
    loot = merged["floorLootPlan"]
    assert loot["stageWeights"] == [{"stageType": "peak"}]
    assert loot["categoryCaps"] == [{"category": "skill"}]
    assert source["floorLootPlan/stageWeights"] == DEFAULTS
    assert source["floorLootPlan/categoryCaps"] == FLOOR


def test_presets_travel_with_the_items_they_explain():
    """A floor that lists its own items does not also inherit the chapter's
    preset tier — upstream expands presets before merging, so the pair moves
    as one."""
    chapter = {"floorLootPlan": {"probabilisticPresets": ["consumables_small"]}}
    merged, _ = merge_floor(chapter, {"floorLootPlan": {"probabilistic": [{"itemId": "x"}]}})
    assert "probabilisticPresets" not in merged["floorLootPlan"]
    merged, source = merge_floor(chapter, {"floorLootPlan": {}})
    assert merged["floorLootPlan"]["probabilisticPresets"] == ["consumables_small"]
    assert source["floorLootPlan/probabilisticPresets"] == DEFAULTS


def test_bools_can_be_turned_on_never_off():
    assert merge_floor({"railCart": True}, {"railCart": False})[0]["railCart"] is True
    assert merge_floor({}, {"railCart": True})[1]["railCart"] == FLOOR


@pytest.mark.parametrize("key,value", [("endsRunOnClear", True), ("elevation", -1),
                                       ("rooms", [{"roomId": "r"}])])
def test_the_never_inherited_three(key, value):
    merged, _ = merge_floor({key: value}, {})
    assert key not in merged
    assert [k for k, _ in plandefaults.refused_defaults({key: value})] == [key]


def test_a_pointer_moves_to_the_node_that_holds_it():
    _, source = merge_floor({"enemyPool": [{}], "floorLootPlan": {"stageWeights": [{}]}},
                            {"stageTypes": [{}]})
    assert plandefaults.reroute("/floors/1/enemyPool/0/enemyId", 1, source) \
        == "/floorDefaults/enemyPool/0/enemyId"
    assert plandefaults.reroute("/floors/1/floorLootPlan/stageWeights/0", 1, source) \
        == "/floorDefaults/floorLootPlan/stageWeights/0"
    assert plandefaults.reroute("/floors/1/stageTypes/0", 1, source) == "/floors/1/stageTypes/0"
    # Another floor's pointer is not this floor's to move.
    assert plandefaults.reroute("/floors/2/enemyPool/0", 1, source) == "/floors/2/enemyPool/0"


def test_fan_out_counts_the_floors_that_did_not_write_it():
    plan = {"floorDefaults": {"themeId": "t", "enemiesPerRoom": 12,
                              "floorLootPlan": {"stageWeights": [{}]}},
            "floors": [{"enemiesPerRoom": 8}, {}, {"floorLootPlan": {"stageWeights": [{}]}}]}
    assert plandefaults.fan_out(plan) == {
        "themeId": 3, "enemiesPerRoom": 2, "floorLootPlan/stageWeights": 2}


# ── against shipped data ─────────────────────────────────────────────────

needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def plan(ds):
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


@needs_data
def test_chapter_3_inherits_what_it_does_not_repeat(ds):
    floor = ds.plan_view("chapter_3")["floors"][0]
    assert floor["themeId"] == "themes/factory"
    assert {"themeId", "enemiesPerRoom", "appearance",
            "floorLootPlan/stageWeights"} <= set(floor["inherited"])
    assert "enemyPool" not in floor["inherited"]
    assert floor["stageWeights"][0]["pointer"].startswith("/floorDefaults/")


@needs_data
def test_a_bad_default_is_reported_once_at_the_default(ds, plan):
    plan["floorDefaults"]["floorLootPlan"]["categoryCaps"].append(
        {"category": "nope_category", "maxPerFloor": 1})
    found = [i for i in validate_plan(ds, "chapter_3")
             if i["code"] == "LOOT_CATEGORY_CAP_UNKNOWN"]
    assert len(found) == 1
    assert found[0]["pointer"].startswith("/floorDefaults/floorLootPlan/categoryCaps/")
    assert "floors 0, 1, 2" in found[0]["message"]


@needs_data
def test_a_floor_that_overrides_a_bad_default_is_not_charged_with_it(ds, plan):
    plan["floorDefaults"]["generatorType"] = "treee"
    plan["floors"][1]["generatorType"] = "tree"
    found = [i for i in validate_plan(ds, "chapter_3")
             if i["code"] == "TYPE_VALUE_UNKNOWN" and "generatorType" in i["pointer"]]
    assert [i["pointer"] for i in found] == ["/floorDefaults/generatorType"]
    assert "floors 0, 2" in found[0]["message"]


@needs_data
def test_a_floor_written_value_is_reported_on_the_floor(ds, plan):
    plan["floors"][2]["enemyPool"][0]["enemyId"] = "no_such_enemy"
    found = [i for i in validate_plan(ds, "chapter_3")
             if i["code"] == "DATA_REFERENCE_MISSING" and "enemyPool" in i["pointer"]]
    assert [i["pointer"] for i in found] == ["/floors/2/enemyPool/0/enemyId"]


@needs_data
def test_a_default_no_floor_can_turn_off_is_refused(ds, plan):
    plan["floorDefaults"]["endsRunOnClear"] = True
    found = [i for i in validate_plan(ds, "chapter_3")
             if i["code"] == "LEVEL_FLOOR_DEFAULT_REFUSED"]
    assert found and found[0]["pointer"] == "/floorDefaults/endsRunOnClear"


@needs_data
def test_the_view_says_how_far_a_default_reaches(ds):
    d = ds.plan_view("chapter_3")["floorDefaults"]
    assert d["declared"] is True
    assert d["fanOut"]["themeId"] == 3
    # floor 2 writes its own roomCountMin.
    assert d["fanOut"]["roomCountMin"] == 2


@needs_data
def test_a_plan_without_defaults_is_unchanged(ds):
    view = ds.plan_view("level_10")
    assert view["floorDefaults"]["declared"] is False
    assert all(not f["inherited"] for f in view["floors"])


# ── OZX-1366: waivePoolCoverage ──────────────────────────────────────────


@needs_data
def test_a_waived_plan_needs_no_introducing_slice(ds, plan):
    plan["floors"][0]["stageTypes"] = [{"stageType": "peak", "portion": 1}]
    assert "LEVEL_STAGE_INTRO_MISSING" in [i["code"] for i in validate_plan(ds, "chapter_3")]
    plan["waivePoolCoverage"] = True
    assert "LEVEL_STAGE_INTRO_MISSING" not in [i["code"] for i in validate_plan(ds, "chapter_3")]
    assert ds.plan_view("chapter_3")["waivePoolCoverage"] is True


@needs_data
def test_a_floor_with_no_pool_has_nothing_to_introduce(ds, plan):
    plan["floors"][0]["stageTypes"] = [{"stageType": "peak", "portion": 1}]
    plan["floors"][0]["enemyPool"] = []
    plan["floorDefaults"].pop("enemyPool", None)
    assert "LEVEL_STAGE_INTRO_MISSING" not in [i["code"] for i in validate_plan(ds, "chapter_3")]
