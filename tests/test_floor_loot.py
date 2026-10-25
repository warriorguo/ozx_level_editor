"""`floorLootPlan`'s reference edges.

`level_referenced_data.md` §0 lists every one of these as a required
reference, and `validate_plan` checked none of them — it walked
`stageWeights` and stopped. Found while confirming that OZX-1092's ten new
hammer drops would not trip anything (OLE-25): they do not, but neither does
`hammer_typoo`.

Each of the three families fails quietly in its own way, which is why they
belong here rather than being left to the generator:

- a dangling `probabilistic[].itemId` goes straight into the synthesised
  table as a `LootEntryData` — `RegisterScaledTable` never looks at it — so
  the table keeps working and is simply missing one outcome forever;
- a `categoryCaps[].category` outside the taxonomy matches no item, so the
  cap reads as authored and does nothing. That is exactly what OZX-1044
  added a fail-fast for on `stageWeights.category`; `categoryCaps` never got
  one;
- the `guaranteed[]` rules do throw — at generation time, which is the moment
  this tool exists to get ahead of.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

PLAN = "chapter_3"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def loot(ds):
    """chapter_3 floor 0's floorLootPlan, restored after each test."""
    doc = ds.get("LevelBasePlanData", PLAN)
    plan = doc.value["floors"][0]["floorLootPlan"]
    before = copy.deepcopy(plan)
    yield plan
    plan.clear()
    plan.update(before)


def codes(ds, plan_id=PLAN):
    return [i["code"] for i in validate_plan(ds, plan_id)]


def guaranteed(loot, **entry):
    loot.setdefault("guaranteed", []).append(entry)


# ── the baseline ─────────────────────────────────────────────────────────


def test_the_shipped_plans_have_no_loot_findings(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        found = [c for c in codes(ds, doc.id)
                 if c.startswith(("LOOT_SLOT", "LOOT_GUARANTEED", "LOOT_CATEGORY"))]
        assert not found, (doc.id, found)


def test_a_floor_with_no_loot_plan_is_not_a_finding(ds, loot):
    doc = ds.get("LevelBasePlanData", PLAN)
    before = doc.value["floors"][0].pop("floorLootPlan")
    try:
        assert not [c for c in codes(ds) if c.startswith("LOOT_")]
    finally:
        doc.value["floors"][0]["floorLootPlan"] = before


# ── probabilistic ────────────────────────────────────────────────────────


def test_the_ten_hammers_validate_clean(ds, loot):
    """OZX-1092 adds these as plain data on an existing schema. The point of
    the check is that a real id passes while a typo does not — "nothing is
    reported" was previously true of both."""
    hammers = [i for i, c in ds.taxonomy()["categoryOfItem"].items()
               if c == "hammer"]
    assert len(hammers) == 10
    loot["probabilistic"].extend(
        {"itemId": h, "weight": 1, "minCount": 1, "maxCount": 1}
        for h in hammers)
    assert not [c for c in codes(ds) if c.startswith(("LOOT_", "DATA_REFERENCE"))]


def test_a_dangling_item_is_a_loot_row_that_can_never_pay(ds, loot):
    loot["probabilistic"].append({"itemId": "hammer_typoo", "weight": 1})
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_an_item_that_is_really_a_table_says_so(ds, loot):
    loot["probabilistic"].append({"itemId": "loot_equipment_f0", "weight": 1})
    issues = [i for i in validate_plan(ds, PLAN)
              if i["code"] == "DATA_REFERENCE_MISSING"
              and "probabilistic" in (i["pointer"] or "")]
    assert issues and "`tableId`" in issues[0]["message"]


def test_naming_both_an_item_and_a_table_is_refused(ds, loot):
    loot["probabilistic"].append(
        {"itemId": "key_silver", "tableId": "loot_equipment_f0", "weight": 1})
    assert "LOOT_SLOT_AMBIGUOUS" in codes(ds)


def test_a_table_valued_entry_must_be_a_usable_pickone(ds, loot):
    """An ordinary table rolls every entry independently, so it yields
    anywhere from nothing to everything and the slot's count stops meaning
    anything — `LootTableDraw.Require`."""
    ordinary = next(d.id for d in ds.of_type("LootTableData")
                    if not d.value.get("pickOne") and d.value.get("entries"))
    loot["probabilistic"].append({"tableId": ordinary, "weight": 1})
    assert "LOOT_SLOT_TABLE_NOT_PICKONE" in codes(ds)

    loot["probabilistic"][-1] = {"tableId": "no_such_table", "weight": 1}
    assert "LOOT_TABLE_MISSING" in codes(ds)


def test_a_probabilistic_entry_naming_nothing_is_not_an_error(ds, loot):
    """The assigner throws on a `guaranteed` entry naming neither; a
    probabilistic one just becomes a row that never pays. Quieter, and not
    a shape any shipped plan writes — reporting it would be inventing a rule
    upstream does not have."""
    loot["probabilistic"].append({"weight": 1})
    assert "LOOT_SLOT_EMPTY" not in codes(ds)


# ── guaranteed ───────────────────────────────────────────────────────────


def test_a_guaranteed_entry_must_name_exactly_one_target(ds, loot):
    guaranteed(loot, count=1, boundEnemyId="jammer")
    assert "LOOT_SLOT_EMPTY" in codes(ds)


def test_a_guaranteed_entry_must_have_exactly_one_binding(ds, loot):
    guaranteed(loot, itemId="key_silver", count=1)
    assert "LOOT_GUARANTEED_BINDING_COUNT" in codes(ds)

    loot["guaranteed"] = [{"itemId": "key_silver", "count": 1,
                           "boundEnemyId": "jammer", "boundStage": "peak"}]
    issues = [i for i in validate_plan(ds, PLAN)
              if i["code"] == "LOOT_GUARANTEED_BINDING_COUNT"]
    assert issues and "boundEnemyId, boundStage" in issues[0]["message"]


def test_the_named_wave_binding_is_dead_on_sight(ds, loot):
    """OZX-650 removed named waves; the assigner throws rather than trying to
    resolve one. Reported separately from the count rule, because "you used
    the one binding that no longer exists" is a different fix from "you used
    two"."""
    guaranteed(loot, itemId="key_silver", count=1, boundWaveLastEnemy="w1")
    found = codes(ds)
    assert "LOOT_GUARANTEED_BINDING_DEAD" in found
    assert "LOOT_GUARANTEED_BINDING_COUNT" not in found


def test_a_bound_enemy_must_exist(ds, loot):
    guaranteed(loot, itemId="key_silver", count=1, boundEnemyId="no_such_enemy")
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_a_bound_stage_must_be_one_this_floor_declares(ds, loot):
    """OZX-1043: ONE room of that stage carries it, so a stage the floor
    never slices has no room to put it in."""
    guaranteed(loot, itemId="key_silver", count=1, boundStage="nope")
    assert "LOOT_GUARANTEED_STAGE_UNKNOWN" in codes(ds)

    loot["guaranteed"] = [{"itemId": "key_silver", "count": 1,
                           "boundStage": "peak"}]
    assert "LOOT_GUARANTEED_STAGE_UNKNOWN" not in codes(ds)


def test_a_bound_room_is_only_checkable_on_an_authored_floor(ds, loot):
    """Procedural room ids are invented at generation and are not authorable,
    so on a generated floor there is no set to check against — claiming the
    id is wrong would be worse than saying nothing."""
    guaranteed(loot, itemId="key_silver", count=1, boundRoomId="f0_room_9_9")
    assert "DATA_REFERENCE_MISSING" not in codes(ds)

    doc = ds.get("LevelBasePlanData", PLAN)
    doc.value["floors"][0]["rooms"] = [{"roomId": "hand_written"}]
    try:
        assert "DATA_REFERENCE_MISSING" in codes(ds)
    finally:
        del doc.value["floors"][0]["rooms"]


def test_a_chance_outside_the_unit_range_is_refused(ds, loot):
    guaranteed(loot, itemId="key_silver", count=1, chance=1.5,
               boundEnemyId="jammer")
    assert "LOOT_GUARANTEED_CHANCE_INVALID" in codes(ds)


def test_an_unauthored_chance_is_always_not_never(ds, loot):
    """0 is the pre-OZX-1043 shape: every guaranteed entry paid out."""
    guaranteed(loot, itemId="key_silver", count=1, boundEnemyId="jammer")
    assert "LOOT_GUARANTEED_CHANCE_INVALID" not in codes(ds)


# ── categoryCaps and stageMustDrop ───────────────────────────────────────


def test_a_category_cap_outside_the_taxonomy_is_refused(ds, loot):
    """It matches no item, so the cap reads as authored and does nothing —
    the same failure OZX-1044 gave stageWeights a fail-fast for."""
    loot.setdefault("categoryCaps", []).append(
        {"category": "nope_category", "maxPerFloor": 1})
    assert "LOOT_CATEGORY_CAP_UNKNOWN" in codes(ds)


def test_an_interior_taxonomy_node_is_a_real_category(ds, loot):
    """Existence is read off the NODE list, not the item index: a node that
    holds no items of its own is still a node, which is why `HasCategory`
    exists upstream as a separate question from `GetItemsIn`."""
    tax = ds.taxonomy()
    interior = next(n for n in tax["parentOf"]
                    if n not in set(tax["categoryOfItem"].values()))
    loot.setdefault("categoryCaps", []).append(
        {"category": interior, "maxPerFloor": 1})
    assert "LOOT_CATEGORY_CAP_UNKNOWN" not in codes(ds)


def test_a_cap_with_no_category_applies_to_nothing(ds, loot):
    loot.setdefault("categoryCaps", []).append({"maxPerFloor": 1})
    assert "LOOT_CATEGORY_CAP_UNSCOPED" in codes(ds)


def test_a_must_drop_item_must_exist(ds, loot):
    loot.setdefault("stageMustDrop", []).append(
        {"stageType": "peak", "drops": [{"itemId": "no_such_item", "count": 1}]})
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_a_must_drop_on_an_unknown_stage_warns(ds, loot):
    loot.setdefault("stageMustDrop", []).append(
        {"stageType": "peek", "drops": [{"itemId": "key_silver", "count": 1}]})
    issues = [i for i in validate_plan(ds, PLAN)
              if i["code"] == "TYPE_VALUE_UNKNOWN"
              and "stageMustDrop" in (i["pointer"] or "")]
    assert issues and issues[0]["severity"] == "warning"


# ── OZX-1109: chance and tableId on a must-drop, and the stage with no fight ─


def must_drop(loot, stage="release", **drop):
    loot.setdefault("stageMustDrop", []).append({"stageType": stage, "drops": [drop]})


def test_the_shipped_release_skill_draws_from_a_table(ds):
    """chapter_3's release rooms promise "a skill" through `tableId` — the
    shape that used to read as a must-drop with no itemId."""
    drops = [d for e in ds.get("LevelBasePlanData", PLAN).value["floors"][0]
             ["floorLootPlan"]["stageMustDrop"] for d in e["drops"]]
    assert any(d.get("tableId") and 0 < d.get("chance", 0) < 1 for d in drops)
    assert "LOOT_SLOT_EMPTY" not in codes(ds)


def test_a_must_drop_names_an_item_or_a_table_never_both_or_neither(ds, loot):
    must_drop(loot, count=1)
    assert "LOOT_SLOT_EMPTY" in codes(ds)
    loot["stageMustDrop"][-1]["drops"][0].update(
        itemId="item_health_small", tableId="loot_skills")
    assert "LOOT_SLOT_AMBIGUOUS" in codes(ds)


def test_a_must_drop_table_must_be_a_usable_pickone(ds, loot):
    must_drop(loot, tableId="no_such_table")
    assert "LOOT_TABLE_MISSING" in codes(ds)


@pytest.mark.parametrize("chance", [-0.1, 1.5])
def test_a_must_drop_chance_outside_the_unit_range_is_refused(ds, loot, chance):
    must_drop(loot, itemId="item_health_small", chance=chance)
    assert "LOOT_MUST_DROP_CHANCE_INVALID" in codes(ds)


@pytest.mark.parametrize("chance", [None, 0, 0.3, 1])
def test_an_unauthored_must_drop_chance_is_always(ds, loot, chance):
    """0 = omitted = every room — the only reading JsonUtility leaves, since an
    omitted float in a [Serializable] sub-config materialises as 0."""
    drop = {"itemId": "item_health_small"}
    if chance is not None:
        drop["chance"] = chance
    must_drop(loot, **drop)
    assert "LOOT_MUST_DROP_CHANCE_INVALID" not in codes(ds)


def test_a_must_drop_with_no_stage_is_refused(ds, loot):
    loot.setdefault("stageMustDrop", []).append(
        {"drops": [{"itemId": "item_health_small"}]})
    assert "LOOT_MUST_DROP_UNSCOPED" in codes(ds)


def test_a_guarantee_bound_to_a_stage_with_no_enemies_is_refused(ds, loot):
    """chapter_3 floor 0's release cuts its whole budget; there is no opening
    enemy for the pin to ride, and `ResolveStageRoom` throws."""
    guaranteed(loot, itemId="item_health_small", count=1, boundStage="release")
    found = [i for i in validate_plan(ds, PLAN)
             if i["code"] == "LOOT_GUARANTEED_STAGE_NO_ENEMIES"]
    assert found and found[0]["severity"] == "error"
    assert found[0]["pointer"].endswith("/boundStage")


def test_a_guarantee_bound_to_a_fighting_stage_is_fine(ds, loot):
    guaranteed(loot, itemId="item_health_small", count=1, boundStage="peak")
    assert "LOOT_GUARANTEED_STAGE_NO_ENEMIES" not in codes(ds)


def test_tuning_the_probabilistic_share_of_a_stage_with_no_enemies_warns(ds, loot):
    loot.setdefault("stageWeights", []).append({"stageType": "release", "weight": 2})
    found = [i for i in validate_plan(ds, PLAN)
             if i["code"] == "LOOT_STAGE_WEIGHT_NO_ENEMIES"]
    assert found and found[0]["severity"] == "warning"


def test_zeroing_a_stage_with_no_enemies_says_nothing(ds, loot):
    """weight 0 agrees with what happens anyway."""
    loot.setdefault("stageWeights", []).append({"stageType": "release", "weight": 0})
    assert "LOOT_STAGE_WEIGHT_NO_ENEMIES" not in codes(ds)
