"""`stageTypes[].secondWave` (OLE-51 / OZX-1354, OZX-1353).

A wave on top of the opening one, drawn only from types that can arrive
late. `encounter_waves_test` is the shipped plan that declares one.
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

PLAN = "encounter_waves_test"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def floor(ds):
    doc = ds.get("LevelBasePlanData", PLAN)
    before = copy.deepcopy(doc.value)
    yield doc.value["floors"][0]
    doc.value.clear()
    doc.value.update(before)


def wave(floor):
    return floor["stageTypes"][0]["secondWave"]


def codes(ds):
    return [i["code"] for i in validate_plan(ds, PLAN)]


def test_the_shipped_wave_is_clean(ds):
    assert not [c for c in codes(ds) if "SECOND_WAVE" in c]


@pytest.mark.parametrize("field,value", [("chance", 0), ("chance", 1.5), ("share", -0.1)])
def test_chance_and_share_live_in_zero_to_one(ds, floor, field, value):
    wave(floor)[field] = value
    assert "LEVEL_SECOND_WAVE_INVALID" in codes(ds)


def test_a_wave_says_how_it_arrives(ds, floor):
    wave(floor)["triggers"] = []
    assert "LEVEL_SECOND_WAVE_INVALID" in codes(ds)


@pytest.mark.parametrize("trigger", [
    {"kind": "advance", "weight": 1},
    {"kind": "cleared", "weight": 1, "distance": 4},
    {"kind": "touch", "weight": 1},
    {"kind": "cleared", "weight": 0},
    {"kind": "advance", "weight": 1, "depth": 0.8},
])
def test_a_malformed_trigger_is_refused(ds, floor, trigger):
    wave(floor)["triggers"] = [trigger]
    assert "LEVEL_SECOND_WAVE_INVALID" in codes(ds)


def test_a_stage_with_no_fight_has_no_wave_to_follow(ds, floor):
    floor["stageTypes"][0]["enemyBudgetCut"] = 1
    assert "LEVEL_SECOND_WAVE_NO_FIGHT" in codes(ds)


def test_the_pool_must_be_able_to_fill_it(ds, floor):
    """Replace every pool type with one that cannot arrive late (no emerge
    window); the wave then has nothing to draw from."""
    grounded = next(d.id for d in ds.of_type("EnemyData")
                    if not (d.value.get("emergeDuration") or 0) > 0
                    and not (d.value.get("railConfig") or {}).get("enabled"))
    for e in floor["enemyPool"]:
        e["enemyId"] = grounded
        e.pop("variantIds", None)
    found = [i for i in validate_plan(ds, PLAN) if i["code"] == "LEVEL_SECOND_WAVE_CAPACITY"]
    assert found and "late-arrivable types: none" in found[0]["message"]


def test_a_capped_pool_too_small_for_the_share_is_refused(ds, floor):
    """Capacity is the sum of the late types' maxPerRoom, up to the stage's
    distinct cap; a cap of 1 each cannot hold half of a big budget."""
    floor["enemiesPerRoom"] = 40
    for e in floor["enemyPool"]:
        e["maxPerRoom"] = 1
    assert "LEVEL_SECOND_WAVE_CAPACITY" in codes(ds)


def test_the_stage_row_shows_the_wave(ds):
    st = ds.plan_view(PLAN)["floors"][0]["stageTypes"][0]
    assert st["secondWave"] == {"chance": 1.0, "share": 0.5, "triggers": ["cleared", "advance"]}
