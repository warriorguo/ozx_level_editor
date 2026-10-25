"""Rail launches and rail train pool entries (OLE-45 / OZX-1309, -1315, -1317, -1341).

A rail launch lays `min..max` whole trains (`railTrainId`, a RailTrainData)
or rail oil tanks (`railOilTankId`) along the room's rail. One launch, one
kind of thing; and none of the spawn-layer fields, which it never reads.
OZX-1315 let a train's cars roll the launch's `dropTableId`, and OZX-1341
let a train ride a rail factory's spawner channel — the first ticket
comment's "refuse dropTableId / viaSpawner" predates both.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_encounter, validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

ENC = "spawn_rail_test_01"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def steps(ds):
    doc = ds.get("EncounterData", ENC)
    before = copy.deepcopy(doc.value)
    yield doc.value["steps"]
    doc.value.clear()
    doc.value.update(before)


@pytest.fixture
def pool(ds):
    doc = ds.get("LevelBasePlanData", "chapter_5")
    before = copy.deepcopy(doc.value)
    yield doc.value["floors"][2]["enemyPool"]
    doc.value.clear()
    doc.value.update(before)


def codes(ds):
    return [i["code"] for i in validate_encounter(ds, ENC)]


def launch(**action):
    return {"action": {"verb": "launch", "min": 1, "max": 1, **action}}


@pytest.mark.parametrize("enc", ["spawn_rail_test_01", "spawn_rail_evil_factory_01"])
def test_the_shipped_rail_launches_are_clean(ds, enc):
    assert not [i for i in validate_encounter(ds, enc) if i["code"].startswith("ENCOUNTER")]


def test_a_train_may_carry_a_drop_table_and_ride_a_rail_factory(ds, steps):
    steps.append(launch(railTrainId="rail_heavy_solo", dropTableId="loot_common",
                        viaSpawner="rail_evil_factory"))
    assert "ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD" not in codes(ds)


@pytest.mark.parametrize("extra", [{"at": "dps"}, {"eliteCount": 1}, {"variantIds": ["x"]},
                                   {"clusterSizeMax": 2}, {"sharedRoot": True}])
def test_a_spawn_layer_field_on_a_train_is_unread(ds, steps, extra):
    steps.append(launch(railTrainId="rail_heavy_solo", **extra))
    found = codes(ds)
    assert "ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD" in found
    assert not [c for c in found if c.startswith(("CLUSTER", "VARIANT"))]


@pytest.mark.parametrize("extra", [{"dropTableId": "loot_common"},
                                   {"viaSpawner": "rail_evil_factory"}])
def test_a_tank_drops_nothing_and_rides_no_channel(ds, steps, extra):
    steps.append(launch(railOilTankId="rail_oil_tank", **extra))
    assert "ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD" in codes(ds)


@pytest.mark.parametrize("action", [
    {"railTrainId": "rail_heavy_solo", "railOilTankId": "rail_oil_tank"},
    {"railTrainId": "rail_heavy_solo", "enemyId": "jammer"},
])
def test_one_launch_places_one_kind_of_thing(ds, steps, action):
    steps.append(launch(**action))
    assert "ENCOUNTER_LAUNCH_TARGET_AMBIGUOUS" in codes(ds)


def test_a_tank_that_cannot_ride_the_rail(ds, steps):
    steps.append(launch(railOilTankId="oil_tank"))
    assert "ENCOUNTER_RAIL_TANK_NOT_ON_RAIL" in codes(ds)


def test_a_missing_train_is_a_reference_error(ds, steps):
    steps.append(launch(railTrainId="rail_nope"))
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_a_rail_bound_enemy_cannot_be_placed_as_a_body(ds, steps):
    steps.append(launch(enemyId="rail_heavy"))
    assert "ENCOUNTER_RAIL_ENEMY_AS_BODY" in codes(ds)


def test_the_boss_flag_does_not_ride_a_spawner_channel(ds, steps):
    steps.append(launch(enemyId="jammer", boss=True, viaSpawner="evil_ground_factory"))
    assert "ENCOUNTER_BOSS_ON_SPAWNER_CHANNEL" in codes(ds)


def test_a_train_row_counts_trains_and_names_its_cars(ds):
    rows = ds.encounter_enemies(ENC)
    train = next(r for r in rows if r["id"] == "rail_mixed_tank_train")
    assert (train["rail"], train["count"], len(train["cars"])) == ("train", 1, 6)


# ── pool entries ─────────────────────────────────────────────────────────


def plan_codes(ds):
    return [i["code"] for i in validate_plan(ds, "chapter_5")]


def test_the_shipped_train_entries_are_clean(ds):
    assert not [c for c in plan_codes(ds) if "RAIL" in c]


def test_a_train_belongs_on_a_rail_floor(ds, pool):
    ds.get("LevelBasePlanData", "chapter_5").value["floors"][2]["railCart"] = False
    assert "LEVEL_RAIL_TRAIN_OFF_RAIL" in plan_codes(ds)


def test_a_train_entry_is_not_also_an_enemy(ds, pool):
    next(e for e in pool if e.get("railTrainId"))["enemyId"] = "jammer"
    assert "LEVEL_RAIL_TRAIN_ENTRY_AMBIGUOUS" in plan_codes(ds)


def test_a_train_entry_takes_no_spawn_layer_fields(ds, pool):
    next(e for e in pool if e.get("railTrainId"))["minionId"] = "jammer"
    assert "LEVEL_RAIL_TRAIN_FIELD_UNREAD" in plan_codes(ds)


def test_the_pool_row_is_the_train(ds):
    rows = ds.plan_view("chapter_5")["floors"][2]["enemyPool"]
    train = next(r for r in rows if r["railTrainId"])
    assert train["railTrainId"] == "rail_heavy_solo" and train["missing"] is False
