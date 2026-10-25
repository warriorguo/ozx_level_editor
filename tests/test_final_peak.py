"""A chapter may end on a peak room instead of a boss (OLE-57 / OZX-1442).

The final area, with `endsRunOnClear` and no `bossId`, ends on its last
peak room in BFS order. chapter_3's last floor is the shipped user.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.plandefaults import resolve_portions  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


def test_portions_are_dealt_by_largest_remainder():
    """`PortionResolver.Resolve`: floor each share, then hand the leftover
    rooms to the largest fractions, earlier stage first on a tie."""
    assert resolve_portions([0.15, 0.35, 0.25, 0.15, 0.10], 11) == [2, 4, 3, 1, 1]
    assert resolve_portions([1, 1, 1], 4) == [2, 1, 1]
    assert resolve_portions([1, 1], 0) == [0, 0]
    assert resolve_portions([0, 0], 5) == [0, 0]


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


def found(ds, code):
    return [i for i in validate_plan(ds, "chapter_3") if i["code"] == code]


def last(plan):
    return plan["floors"][-1]


@needs_data
def test_the_shipped_boss_less_finale_is_clean(ds, plan):
    assert last(plan).get("endsRunOnClear") and not last(plan).get("bossId")
    assert not found(ds, "LEVEL_FINAL_PEAK_INVALID")
    assert ds.plan_view("chapter_3")["floors"][-1]["endsRunOnClear"] is True


@needs_data
def test_only_the_final_area_ends_the_run(ds, plan):
    plan["floors"][0]["endsRunOnClear"] = True
    assert found(ds, "LEVEL_ENDS_RUN_NOT_FINAL_AREA")


@needs_data
def test_it_needs_a_peak_stage(ds, plan):
    last(plan)["stageTypes"] = [s for s in last(plan)["stageTypes"] if s["stageType"] != "peak"]
    assert "lists no peak" in found(ds, "LEVEL_FINAL_PEAK_INVALID")[0]["message"]


@needs_data
def test_the_peak_must_get_a_room_at_the_smallest_size(ds, plan):
    for s in last(plan)["stageTypes"]:
        if s["stageType"] == "peak":
            s["portion"] = 0.0001
    assert "buy no peak room" in found(ds, "LEVEL_FINAL_PEAK_INVALID")[0]["message"]


@needs_data
def test_an_empty_final_room_is_won_on_entry(ds, plan):
    next(s for s in last(plan)["stageTypes"] if s["stageType"] == "peak")["enemyBudgetCut"] = 1
    assert any("on entry" in i["message"] for i in found(ds, "LEVEL_FINAL_PEAK_INVALID"))


@needs_data
def test_a_final_peak_wave_arrives_on_cleared(ds, plan):
    next(s for s in last(plan)["stageTypes"] if s["stageType"] == "peak")["secondWave"] = {
        "chance": 1, "share": 0.5, "triggers": [{"kind": "advance", "weight": 1, "distance": 6}]}
    assert any("advance" in i["message"] for i in found(ds, "LEVEL_FINAL_PEAK_INVALID"))


@needs_data
def test_a_boss_floor_is_left_alone(ds, plan):
    last(plan)["bossId"] = "blight_sac_sovereign"
    last(plan)["stageTypes"] = [s for s in last(plan)["stageTypes"] if s["stageType"] != "peak"]
    assert not found(ds, "LEVEL_FINAL_PEAK_INVALID")


@needs_data
def test_with_area_links_the_final_area_is_the_one_no_link_leaves(ds):
    """chapter_4 ends on floor 1: its links run 0 → 2 → 1."""
    assert not [i for i in validate_plan(ds, "chapter_4")
                if i["code"] == "LEVEL_ENDS_RUN_NOT_FINAL_AREA"]
