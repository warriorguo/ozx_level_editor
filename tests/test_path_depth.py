"""What the generator now reads off `pathDepth` (OLE-67).

- OZX-1558: `pathDepthEnemyScale` — d(depth), the share of a room's enemy
  budget it keeps at its depth. `StageEnemyBudget.ValidateDepthScale`.
- OZX-1560: `floorLootPlan.depthBoxTableId` + `depthRarityBumps` — the
  depth box. `LevelBasePlan.DepthRarityBumpsFromData`.

The room choices themselves (key room, depth-box room, spine-first stage
dealing) are the generator's, and previews get them from genlevel (OLE-1).
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
def plan(ds):
    doc = ds.get("LevelBasePlanData", PLAN)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def codes(ds):
    return [i["code"] for i in validate_plan(ds, PLAN)]


def floor0(plan):
    return plan["floors"][0]


# ── OZX-1558 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scale", [
    [1.0, 0.8],                # one per depth, so three
    [0.9, 0.8, 0.7],           # d(0) is the spine's budget, so 1
    [1.0, 0.0, 0.0],           # each depth keeps a share in (0, 1]
    [1.0, 1.2, 1.0],
    [1.0, 0.6, 0.8],           # never rising with depth
])
def test_a_malformed_depth_scale_is_refused(ds, plan, scale):
    floor0(plan)["pathDepthEnemyScale"] = scale
    assert "LEVEL_DEPTH_SCALE_INVALID" in codes(ds)


@pytest.mark.parametrize("scale", [[], [1.0, 1.0, 1.0], [1.0, 0.5, 0.5]])
def test_a_well_formed_depth_scale_passes(ds, plan, scale):
    floor0(plan)["pathDepthEnemyScale"] = scale
    assert "LEVEL_DEPTH_SCALE_INVALID" not in codes(ds)


def test_an_inherited_scale_is_checked_where_it_is_written(ds, plan):
    for f in plan["floors"]:
        f.pop("pathDepthEnemyScale", None)
    plan.setdefault("floorDefaults", {})["pathDepthEnemyScale"] = [1.0, 0.9, 0.95]
    found = [i for i in validate_plan(ds, PLAN) if i["code"] == "LEVEL_DEPTH_SCALE_INVALID"]
    assert found and all(i["pointer"].startswith("/floorDefaults/") for i in found)


def test_the_view_carries_the_scale(ds, plan):
    floor0(plan)["pathDepthEnemyScale"] = [1.0, 0.8, 0.6]
    assert ds.plan_view(PLAN)["floors"][0]["pathDepthEnemyScale"] == [1.0, 0.8, 0.6]


# ── OZX-1560 ─────────────────────────────────────────────────────────────


def loot0(plan):
    return floor0(plan).setdefault("floorLootPlan", {})


def bumps(plan):
    """The merged bumps floor 0 reads, written onto the floor to edit."""
    merged = (plan.get("floorDefaults", {}).get("floorLootPlan", {})
                              .get("depthRarityBumps"))
    loot0(plan)["depthRarityBumps"] = copy.deepcopy(merged or [
        {"pathDepth": 1, "weights": [1, 1]}, {"pathDepth": 2, "weights": [1, 1]}])
    return loot0(plan)["depthRarityBumps"]


def test_the_shipped_depth_boxes_are_clean(ds):
    for plan_id in ("chapter_3", "chapter_4", "chapter_5", "chapter_6", "chapter_7"):
        found = [c for c in (i["code"] for i in validate_plan(ds, plan_id))
                 if c in ("LOOT_DEPTH_BUMPS_INVALID", "LEVEL_DEPTH_SCALE_INVALID")]
        assert not found, plan_id


def test_the_view_shows_the_box(ds):
    box = ds.plan_view(PLAN)["floors"][0]["depthBox"]
    assert box and box["tableId"] and not box["missing"]
    assert sorted(b["pathDepth"] for b in box["rarityBumps"]) == [1, 2]


def test_the_box_table_has_to_exist_and_pick_one(ds, plan):
    loot0(plan)["depthBoxTableId"] = "no_such_table"
    found = [i for i in validate_plan(ds, PLAN) if i["code"] == "LOOT_TABLE_MISSING"]
    assert found and found[0]["pointer"].endswith("/depthBoxTableId")
    loot0(plan)["depthBoxTableId"] = next(d.id for d in ds.of_type("LootTableData")
                                          if not d.value.get("pickOne"))
    assert "LOOT_SLOT_TABLE_NOT_PICKONE" in codes(ds)


def test_a_box_needs_its_bumps(ds, plan):
    plan.get("floorDefaults", {}).get("floorLootPlan", {}).pop("depthRarityBumps", None)
    for f in plan["floors"]:
        (f.get("floorLootPlan") or {}).pop("depthRarityBumps", None)
    assert "LOOT_DEPTH_BUMPS_INVALID" in codes(ds)


@pytest.mark.parametrize("edit", [
    lambda rows: rows.pop(),                                   # depth 2 missing
    lambda rows: rows.append({"pathDepth": 0, "weights": [1]}),  # never on the spine
    lambda rows: rows.append(copy.deepcopy(rows[0])),           # named twice
    lambda rows: rows[0].update(weights=[]),
    lambda rows: rows[0].update(weights=[1, -1]),
    lambda rows: rows[0].update(weights=[0, 0]),
])
def test_malformed_bumps_are_refused(ds, plan, edit):
    edit(bumps(plan))
    assert "LOOT_DEPTH_BUMPS_INVALID" in codes(ds)


def test_bumps_without_a_box_are_not_a_fault(ds, plan):
    """Chapter-wide in floorDefaults while the table is per floor."""
    for f in plan["floors"]:
        (f.get("floorLootPlan") or {}).pop("depthBoxTableId", None)
    assert "LOOT_DEPTH_BUMPS_INVALID" not in codes(ds)
