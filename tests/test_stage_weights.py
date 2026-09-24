"""`floorLootPlan.stageWeights`, once an entry may name a category (OZX-1044).

Before the field, `stageWeights` could say "peak drops more" and nothing else:
`RegisterScaledTable` took one scalar and multiplied every entry in the
synthesised table by it. An entry may now scope itself to a node of
`item_taxonomy.json`, matched down its subtree, so "peak ×1.4 of everything
and ×1.8 of gear" is two lines. The multiplier is resolved per ENTRY, which
means **weights inside one synthesised table are no longer in proportion to
each other** — anything here that recomputed them by scaling a whole table
would now be wrong.

The resolution order is the part that fails quietly, and it is what a preview
has to match: the item's own category, then each ancestor in turn, then the
stage's category-free entry, then 1.0. A rule that picks the wrong entry still
produces a number, so the table looks fine and only the loot feels wrong.

`StageWeightResolver.Build` stops generation on three shapes rather than
proceeding, all of which leave a plan reading as tuned and behaving as
untuned. Those are the ones moved forward here.
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

# level_10 writes its stageWeights per floor, so it is the one plan whose
# weights reach a floor view without the floorDefaults merge (OLE-9).
PLAN = "level_10"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def floor(ds):
    """level_10 floor 0's loot plan, restored after each test."""
    doc = ds.get("LevelBasePlanData", PLAN)
    before = copy.deepcopy(doc.value["floors"][0]["floorLootPlan"])
    yield doc.value["floors"][0]["floorLootPlan"]
    doc.value["floors"][0]["floorLootPlan"] = before


def codes(ds, plan_id=PLAN):
    return [i["code"] for i in validate_plan(ds, plan_id)]


# ── the taxonomy this is resolved against ────────────────────────────────


def test_the_taxonomy_is_indexed_like_any_other_gamedata(ds):
    tax = ds.taxonomy()
    assert tax["found"]
    assert "equipment" in tax["parentOf"]
    assert tax["parentOf"]["equipment"] == "", "equipment is a root"
    assert tax["parentOf"]["core"] == "equipment"


def test_an_items_chain_is_leaf_first(ds):
    """Most specific wins, so the order is the rule, not a detail."""
    assert ds.categories_of("core_cinder_reactor") == ["core", "equipment"]
    assert ds.categories_of("no_such_item") == []


def test_a_node_that_holds_no_items_of_its_own_still_exists(ds):
    """`GetItemsIn` returning empty cannot tell a typo from a real node —
    which is why HasCategory is a separate question upstream and why this
    reads the node list rather than the item index."""
    tax = ds.taxonomy()
    interior = [n for n in tax["parentOf"]
                if n not in set(tax["categoryOfItem"].values())]
    assert interior, "expected at least one node with no items directly under it"
    assert all(n in tax["parentOf"] for n in interior)


# ── the view ─────────────────────────────────────────────────────────────


def test_the_plan_view_carries_the_weights(ds):
    view = ds.plan_view(PLAN)
    rows = view["floors"][0]["stageWeights"]
    assert rows
    assert all(r["pointer"].endswith(f"/stageWeights/{r['index']}") for r in rows)
    assert {r["stageType"] for r in rows} >= {"start", "pressure"}


def test_authored_order_is_kept(ds, floor):
    """Sorting would hide the one shape the validator cares about."""
    floor["stageWeights"] = [
        {"stageType": "peak", "weight": 1.4},
        {"stageType": "start", "weight": 0.0},
    ]
    rows = ds.plan_view(PLAN)["floors"][0]["stageWeights"]
    assert [r["stageType"] for r in rows] == ["peak", "start"]


def test_a_category_free_entry_reports_no_category_not_an_empty_one(ds, floor):
    """It is the stage's fallback, not a blank field."""
    floor["stageWeights"] = [{"stageType": "peak", "category": "", "weight": 1.4}]
    row = ds.plan_view(PLAN)["floors"][0]["stageWeights"][0]
    assert row["category"] is None
    assert row["unknownCategory"] is False


def test_an_unknown_category_is_flagged_on_the_row(ds, floor):
    floor["stageWeights"] = [{"stageType": "peak", "category": "gear", "weight": 1.4}]
    row = ds.plan_view(PLAN)["floors"][0]["stageWeights"][0]
    assert row["category"] == "gear"
    assert row["unknownCategory"] is True


# ── StageWeightResolver.Build, moved forward ─────────────────────────────


def test_the_shipped_plans_have_no_stage_weight_findings(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        found = [c for c in codes(ds, doc.id) if "STAGE_WEIGHT" in c]
        assert not found, (doc.id, found)


def test_a_scoped_entry_is_accepted(ds, floor):
    """chapter_3 authors exactly this shape; it must not be a finding."""
    floor["stageWeights"] = [
        {"stageType": "peak", "weight": 1.4},
        {"stageType": "peak", "category": "equipment", "weight": 1.8},
        {"stageType": "peak", "category": "skill", "weight": 0.0},
    ]
    assert not [c for c in codes(ds) if "STAGE_WEIGHT" in c]


def test_a_misspelled_category_is_an_error_not_a_warning(ds, floor):
    """It matches no item and leaves the stage on its fallback without a word,
    so the plan reads as tuned and behaves as untuned."""
    floor["stageWeights"] = [{"stageType": "peak", "category": "equipmnet",
                              "weight": 1.8}]
    issues = validate_plan(ds, PLAN)
    bad = [i for i in issues if i["code"] == "LEVEL_STAGE_WEIGHT_CATEGORY_UNKNOWN"]
    assert bad
    assert bad[0]["severity"] == "error"
    assert bad[0]["pointer"].endswith("/stageWeights/0/category")


def test_two_entries_for_one_pair_are_refused(ds, floor):
    """The loser is unreachable, and which one wins would depend on order."""
    floor["stageWeights"] = [
        {"stageType": "peak", "category": "equipment", "weight": 1.8},
        {"stageType": "peak", "category": "equipment", "weight": 0.5},
    ]
    assert "LEVEL_STAGE_WEIGHT_DUPLICATE" in codes(ds)


def test_the_same_stage_with_different_categories_is_fine(ds, floor):
    """That is the whole point of the field."""
    floor["stageWeights"] = [
        {"stageType": "peak", "weight": 1.4},
        {"stageType": "peak", "category": "equipment", "weight": 1.8},
    ]
    assert "LEVEL_STAGE_WEIGHT_DUPLICATE" not in codes(ds)


def test_two_category_free_entries_for_one_stage_are_refused(ds, floor):
    floor["stageWeights"] = [
        {"stageType": "peak", "weight": 1.4},
        {"stageType": "peak", "weight": 0.9},
    ]
    assert "LEVEL_STAGE_WEIGHT_DUPLICATE" in codes(ds)


def test_a_negative_weight_is_refused_and_zero_is_not(ds, floor):
    floor["stageWeights"] = [{"stageType": "peak", "weight": -1}]
    assert "LEVEL_STAGE_WEIGHT_NEGATIVE" in codes(ds)

    # 0 is a real authoring choice: this stage drops nothing.
    floor["stageWeights"] = [{"stageType": "peak", "weight": 0}]
    assert "LEVEL_STAGE_WEIGHT_NEGATIVE" not in codes(ds)


def test_a_stage_no_room_can_be_is_a_warning(ds, floor):
    floor["stageWeights"] = [{"stageType": "peek", "weight": 1.4}]
    issues = validate_plan(ds, PLAN)
    bad = [i for i in issues
           if i["code"] == "TYPE_VALUE_UNKNOWN"
           and "stageWeights" in (i["pointer"] or "")]
    assert bad and bad[0]["severity"] == "warning"


def test_an_entry_with_no_stage_is_reported_rather_than_ignored(ds, floor):
    """The resolver skips it outright, so it is a dead line — silent either
    way, which is exactly when saying something is worth it."""
    floor["stageWeights"] = [{"weight": 1.4}]
    assert "LEVEL_STAGE_WEIGHT_UNSCOPED" in codes(ds)
