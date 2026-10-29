"""`cycleShape`, a cycle floor's ring branches (OLE-71 / OZX-1579).

Side branches go straight out of a ring wall, top branches straight up out
of the top row (never off the middle room, which keeps the stairs), each
`branchLength` rooms long. All or nothing, declared by `branchLengthMax > 0`
and inherited whole. `CycleShapeFromData` refuses an unordered range;
`ValidateCycleBranches` refuses a negative count, a branch under one room,
and a ring whose worst case — `roomCountMin`, counts and length at their
max, every height it can roll — has too few rooms to hang them from.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import plandefaults  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import (cycle_derive_width, cycle_side_slots,  # noqa: E402
                               cycle_top_slots, validate_plan)

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

CYCLE_FLOOR = 2   # chapter_4 / chapter_5: area 2 is the cycle, roomCount 13–15 / 12–15


def branched(side_max, top_max, length_min, length_max=2):
    """CycleBranchShippedPlanTests.BranchedPlan."""
    return {"sideBranchesMin": 0, "sideBranchesMax": side_max,
            "topBranchesMin": 1, "topBranchesMax": top_max,
            "branchLengthMin": length_min, "branchLengthMax": length_max}


# ── the arithmetic, shared with the generator ────────────────────────────


def test_the_ring_takes_what_the_branches_leave():
    assert cycle_derive_width(12, 2, 0) == 6
    assert cycle_derive_width(12, 3, 8) == 3      # never under 3
    assert cycle_derive_width(14, 3, 4) == 6


@pytest.mark.parametrize("width,height,side,top", [
    (3, 2, 2, 2),   # both top corners; the middle keeps the stairs
    (6, 3, 4, 5),
    (2, 2, 1, 1),
    (5, 1, 0, 3),   # a flat row: no walls, the boss holds one end
])
def test_slots(width, height, side, top):
    assert cycle_side_slots(width, height) == side
    assert cycle_top_slots(width, height, 0) == top


def test_side_branches_may_take_the_corners_a_top_branch_wanted():
    assert cycle_top_slots(6, 3, 0) - cycle_top_slots(6, 3, 2) == 2
    assert cycle_top_slots(6, 3, 5) == cycle_top_slots(6, 3, 2)


def test_mixed_alternates_by_floor_index():
    kinds = [plandefaults.generator_kind("mixed", f) for f in range(4)]
    assert kinds == ["tree", "cycle", "tree", "cycle"]
    assert plandefaults.generator_kind("nonsense", 1) == "tree"


# ── the shipped plans ────────────────────────────────────────────────────


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def floor(ds, plan_id, fi=CYCLE_FLOOR):
    return ds.get("LevelBasePlanData", plan_id).value["floors"][fi]


def issues(ds, plan_id):
    return [i for i in validate_plan(ds, plan_id) if i["code"].startswith("LEVEL_CYCLE")]


@needs_data
@pytest.mark.parametrize("plan_id", ["chapter_4", "chapter_5"])
def test_unbranched_cycles_are_clean_and_show_nothing(ds, plan_id):
    assert issues(ds, plan_id) == []
    assert [f["cycleBranches"] for f in ds.plan_view(plan_id)["floors"]] == [None] * 3


@needs_data
@pytest.mark.parametrize("plan_id", ["chapter_4", "chapter_5"])
def test_the_upstream_acceptance_shape_fits_and_is_shown(ds, plan_id):
    floor(ds, plan_id)["cycleShape"] = branched(1, 1, 2)
    assert issues(ds, plan_id) == []
    view = ds.plan_view(plan_id)["floors"][CYCLE_FLOOR]["cycleBranches"]
    assert view == {"side": [0, 1], "top": [1, 1], "length": [2, 2]}


@needs_data
@pytest.mark.parametrize("plan_id", ["chapter_4", "chapter_5"])
def test_two_of_each_do_not_fit_the_smallest_ring(ds, plan_id):
    # ABranchCountTheSmallestRingCannotHold: a 3-wide ring with no top room.
    floor(ds, plan_id)["cycleShape"] = branched(2, 2, 1)
    found = [i for i in issues(ds, plan_id) if i["code"] == "LEVEL_CYCLE_BRANCHES_NO_ROOM"]
    assert len(found) == 1
    assert found[0]["pointer"] == f"/floors/{CYCLE_FLOOR}/cycleShape"
    assert "3 wide" in found[0]["message"] and "8 branch rooms" in found[0]["message"]


@needs_data
def test_a_written_width_is_the_ring_whatever_the_budget(ds):
    f = floor(ds, "chapter_4")
    f["cycleShape"] = branched(2, 2, 2)
    f.update({"floorWidthMin": 6, "floorWidthMax": 7})
    assert [i["code"] for i in issues(ds, "chapter_4")] == []


@needs_data
def test_one_room_branches_cannot_carry_the_tip_skill(ds):
    # chapter_5's floorDefaults pin a tip skill on every area; genlevel threw
    # on 64 of 200 seeds with this shape.
    floor(ds, "chapter_5")["cycleShape"] = branched(1, 1, 1)
    assert [(i["code"], i["severity"]) for i in issues(ds, "chapter_5")] == [
        ("LEVEL_CYCLE_BRANCH_TOO_SHORT", "error")]
    # Nothing pinned off the ring: nothing to lose.
    ds.get("LevelBasePlanData", "chapter_5").value["floorDefaults"]["floorLootPlan"].pop(
        "tipSkillTableId")
    assert issues(ds, "chapter_5") == []


@needs_data
@pytest.mark.parametrize("fields,code,pointer", [
    ({"sideBranchesMin": 2, "sideBranchesMax": 1}, "LEVEL_CYCLE_SHAPE_INVALID", "sideBranchesMax"),
    ({"branchLengthMin": 3}, "LEVEL_CYCLE_SHAPE_INVALID", "branchLengthMax"),
    ({"topBranchesMin": -1}, "LEVEL_CYCLE_SHAPE_INVALID", "topBranchesMin"),
    ({"branchLengthMin": 0}, "LEVEL_CYCLE_SHAPE_INVALID", "branchLengthMin"),
])
def test_a_malformed_shape_throws(ds, fields, code, pointer):
    floor(ds, "chapter_4")["cycleShape"] = dict(branched(1, 1, 2), **fields)
    assert [(i["code"], i["pointer"]) for i in issues(ds, "chapter_4")] == [
        (code, f"/floors/{CYCLE_FLOOR}/cycleShape/{pointer}")]


@needs_data
def test_inherited_whole_and_read_only_by_cycle_floors(ds):
    plan = ds.get("LevelBasePlanData", "chapter_4").value
    plan["floorDefaults"]["cycleShape"] = branched(2, 2, 2)
    found = issues(ds, "chapter_4")
    # The tree areas ignore it; the cycle area's finding lands on the default.
    assert [(i["code"], i["pointer"]) for i in found] == [
        ("LEVEL_CYCLE_BRANCHES_NO_ROOM", "/floorDefaults/cycleShape")]
    # A floor that declares its own replaces the default whole.
    floor(ds, "chapter_4")["cycleShape"] = branched(1, 1, 2)
    assert issues(ds, "chapter_4") == []
    view = ds.plan_view("chapter_4")["floors"]
    assert [f["cycleBranches"] is not None for f in view] == [False, False, True]


@needs_data
def test_an_unordered_range_throws_on_any_floor(ds):
    # CycleShapeFromData reads every floor's block, tree or cycle.
    floor(ds, "chapter_4", 0)["cycleShape"] = dict(branched(1, 1, 2), topBranchesMin=3)
    assert [(i["code"], i["pointer"]) for i in issues(ds, "chapter_4")] == [
        ("LEVEL_CYCLE_SHAPE_INVALID", "/floors/0/cycleShape/topBranchesMax")]
