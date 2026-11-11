"""Enemy production held to declared relations and to the floor's pool
(OLE-74 / OLE-76 / OLE-77 / OLE-82).

- OZX-1779 / OZX-1792: an emitter declares what it may produce —
  `spawnableFamilies` (any variant of a family), `spawnableEnemies` (one
  variant), or, for a rail factory, `spawnableRailUnits` (trains and rail oil
  tanks). `SpawnRelations.ValidateAll` holds the declarations at boot;
  `LevelPlanValidator.ValidateSpawnerPairs` a pool's minion and
  `SpawnRelationValidator` every encounter's viaSpawner launch to them.
- OZX-1780: a spawner entry may leave `minionId` out — the generator picks one
  per room from `SpawnerMinions.Candidates`, which may not be empty.
- OZX-1781 / OZX-1794: on a generated floor an egg hatches, and a host leaves
  as death heir, only what the pool lists.
- OZX-1792: an entry's variants share the representative's family.
- OZX-1798: a rail factory line's `railOilTanks`.
- OZX-1818: `alwaysElite` pool entries.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import (validate_all, validate_encounter,  # noqa: E402
                               validate_enemy, validate_plan)

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

CODES = {"LEVEL_SPAWNER_RELATION_FORBIDDEN", "LEVEL_SPAWNER_RAIL_UNIT_FORBIDDEN",
         "LEVEL_SPAWNER_PICK_NO_CANDIDATE", "LEVEL_SPAWNER_PICK_ON_FACTORY",
         "LEVEL_RAIL_OIL_TANK_NO_LINE", "LEVEL_RAIL_OIL_TANK_INVALID",
         "LEVEL_RAIL_OIL_TANK_NOT_FROM_FACTORY", "LEVEL_RAIL_OIL_TANK_FORBIDDEN",
         "LEVEL_ALWAYS_ELITE_ON_SPAWNER", "LEVEL_ALWAYS_ELITE_NO_ELITE_BLOCK",
         "LEVEL_HATCH_NOTHING_IN_POOL", "LEVEL_DEATH_HEIR_NOT_IN_POOL",
         "VARIANT_FAMILY_MISMATCH", "ENEMY_SPAWN_RELATION_INVALID",
         "ENCOUNTER_SPAWN_RELATION_FORBIDDEN", "LEVEL_SPAWNER_PAIR_INCOMPLETE",
         "LEVEL_RAIL_TRAIN_FIELD_UNREAD", "DATA_REFERENCE_MISSING"}


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues):
    return [(i["code"], i["pointer"]) for i in issues if i["code"] in CODES]


def pool(ds, plan_id, fi):
    return ds.get("LevelBasePlanData", plan_id).value["floors"][fi]["enemyPool"]


def index_of(entries, enemy_id):
    return next(i for i, e in enumerate(entries)
                if (e.get("enemyId") or e.get("railTrainId")) == enemy_id)


def enemy(ds, enemy_id):
    return ds.get("EnemyData", enemy_id).value


def test_the_shipped_data_is_clean(ds):
    assert found(validate_all(ds)) == []


# ── OZX-1779 / OZX-1792: who may spawn whom ────────────────────────────────


def test_a_named_minion_must_be_one_the_spawner_may_emit(ds):
    entries = pool(ds, "chapter_3", 0)
    fi = index_of(entries, "evil_ground_factory")
    entries[fi]["minionId"] = "blight_sac_crawler"   # in the pool, not a family it makes
    issues = [i for i in validate_plan(ds, "chapter_3")
              if i["code"] == "LEVEL_SPAWNER_RELATION_FORBIDDEN"]
    assert [i["pointer"] for i in issues] == [f"/floors/0/enemyPool/{fi}/minionId"]
    assert "family 'blight_sac_crawler'" in issues[0]["message"]
    # Either list passing is enough: an enemy-level entry lets it through.
    enemy(ds, "evil_ground_factory")["spawnableEnemies"] = ["blight_sac_crawler"]
    assert found(validate_plan(ds, "chapter_3")) == []


def test_a_rail_factory_puts_only_its_own_units_on_the_rail(ds):
    entries = pool(ds, "chapter_5", 2)
    fi = index_of(entries, "rail_evil_factory")
    entries[fi]["minionId"] = "rail_heavy_scout_train"   # a train entry, not in its list
    assert ("LEVEL_SPAWNER_RAIL_UNIT_FORBIDDEN", f"/floors/2/enemyPool/{fi}/minionId") \
        in found(validate_plan(ds, "chapter_5"))


def test_an_encounter_launch_is_held_to_the_relation(ds):
    doc = ds.get("EncounterData", "spawn_broodwing_moth_01")
    assert found(validate_encounter(ds, doc.id)) == []
    doc.value["steps"][1]["action"]["enemyId"] = "jammer"
    assert found(validate_encounter(ds, doc.id)) == [
        ("ENCOUNTER_SPAWN_RELATION_FORBIDDEN", "/steps/1/action/viaSpawner")]
    doc.value["steps"][1]["action"]["viaSpawner"] = "no_such_spawner"
    assert found(validate_encounter(ds, doc.id)) == [
        ("DATA_REFERENCE_MISSING", "/steps/1/action/viaSpawner")]


@pytest.mark.parametrize("enemy_id, change, pointer", [
    ("husk", {"spawnableEnemies": []}, "/spawnableFamilies"),            # declares nothing
    ("jammer", {"spawnableFamilies": ["moth"]}, "/spawnableFamilies"),   # emits nothing
    ("rail_evil_factory", {"spawnableFamilies": ["moth"]}, "/spawnableFamilies"),
    ("husk", {"spawnableRailUnits": ["rail_heavy_solo"]}, "/spawnableRailUnits"),
    ("broodwing", {"spawnableFamilies": ["moth", "moth"]}, "/spawnableFamilies"),
    ("broodwing", {"spawnableFamilies": ["no_such_family"]}, "/spawnableFamilies"),
    ("husk", {"spawnableEnemies": ["no_such_enemy"]}, "/spawnableEnemies"),
])
def test_a_malformed_declaration_refuses_to_boot(ds, enemy_id, change, pointer):
    enemy(ds, enemy_id).update(change)
    assert found(validate_enemy(ds, enemy_id)) == [("ENEMY_SPAWN_RELATION_INVALID", pointer)]


def test_a_rail_unit_must_exist(ds):
    enemy(ds, "rail_evil_factory")["spawnableRailUnits"].append("rail_nope")
    n = len(enemy(ds, "rail_evil_factory")["spawnableRailUnits"]) - 1
    assert found(validate_enemy(ds, "rail_evil_factory")) == [
        ("DATA_REFERENCE_MISSING", f"/spawnableRailUnits/{n}")]


# ── OZX-1780: the generator picks the minion ───────────────────────────────


def test_a_spawner_may_leave_its_minion_to_the_generator(ds):
    entries = pool(ds, "chapter_3", 0)
    fi = index_of(entries, "evil_ground_factory")
    del entries[fi]["minionId"]
    assert found(validate_plan(ds, "chapter_3")) == []
    row = ds.plan_view("chapter_3")["floors"][0]["enemyPool"][fi]
    assert row["isSpawner"] and row["minionId"] is None
    picked = {c["id"] for c in row["minionCandidates"]}
    # The families evil_ground_factory makes, as the pool lists them —
    # never a family it does not make, never itself.
    assert {"jammer", "scout", "demolition", "small_ground_patrol"} <= picked
    assert not picked & {"blight_sac_crawler", "ground_turret", "evil_ground_factory"}


def test_a_pick_needs_a_candidate(ds):
    entries = pool(ds, "chapter_3", 0)
    allowed = set(enemy(ds, "evil_ground_factory")["spawnableFamilies"])
    entries[:] = [e for e in entries if e["enemyId"] not in allowed]
    fi = index_of(entries, "evil_ground_factory")
    del entries[fi]["minionId"]
    issues = [i for i in validate_plan(ds, "chapter_3")
              if i["code"] == "LEVEL_SPAWNER_PICK_NO_CANDIDATE"]
    assert [i["pointer"] for i in issues] == [f"/floors/0/enemyPool/{fi}"]
    assert ds.plan_view("chapter_3")["floors"][0]["enemyPool"][fi]["minionCandidates"] == []


def test_a_rail_factory_may_not_pick(ds):
    entries = pool(ds, "chapter_5", 2)
    fi = index_of(entries, "rail_evil_factory")
    del entries[fi]["minionId"]
    assert ("LEVEL_SPAWNER_PICK_ON_FACTORY", f"/floors/2/enemyPool/{fi}") \
        in found(validate_plan(ds, "chapter_5"))


def test_a_minion_without_a_count_is_still_half_a_pair(ds):
    entries = pool(ds, "chapter_3", 0)
    fi = index_of(entries, "evil_ground_factory")
    del entries[fi]["minionsPerSpawner"]
    assert found(validate_plan(ds, "chapter_3")) == [
        ("LEVEL_SPAWNER_PAIR_INCOMPLETE", f"/floors/0/enemyPool/{fi}")]


# ── OZX-1781 / OZX-1794: products held to the pool ─────────────────────────


def test_an_egg_must_hatch_something_the_pool_lists(ds):
    entries = pool(ds, "chapter_3", 1)
    entries[:] = [e for e in entries if e["enemyId"] != "blight_sac_plant2"]
    egg = index_of(entries, "blight_sac_egg2")
    issues = [i for i in validate_plan(ds, "chapter_3")
              if i["code"] == "LEVEL_HATCH_NOTHING_IN_POOL"]
    assert [i["pointer"] for i in issues] == [f"/floors/1/enemyPool/{egg}/enemyId"]
    # One of its products is enough.
    entries.append({"enemyId": "blight_sac_plant4", "weight": 1.0})
    assert not [i for i in validate_plan(ds, "chapter_3")
                if i["code"] == "LEVEL_HATCH_NOTHING_IN_POOL"]


def test_a_static_floor_is_not_narrowed(ds):
    floor = ds.get("LevelBasePlanData", "chapter_3").value["floors"][1]
    floor["enemyPool"] = [e for e in floor["enemyPool"] if e["enemyId"] != "blight_sac_plant2"]
    floor["generatorType"] = "static"
    assert not [i for i in validate_plan(ds, "chapter_3")
                if i["code"] == "LEVEL_HATCH_NOTHING_IN_POOL"]


def test_a_death_heir_must_be_in_the_pool(ds):
    floor = ds.get("LevelBasePlanData", "chapter_3").value["floors"][2]
    floor["enemyPool"] = [e for e in floor["enemyPool"]
                          if e["enemyId"] != "blight_sac_gloomglow"]
    host = index_of(floor["enemyPool"], "long_broodmother_short")
    assert [p for c, p in found(validate_plan(ds, "chapter_3"))
            if c == "LEVEL_DEATH_HEIR_NOT_IN_POOL"] == [f"/floors/2/enemyPool/{host}/enemyId"]
    # What the boss summons can die here too.
    floor["bossId"] = "blight_sac_sovereign"
    issues = [i for i in validate_plan(ds, "chapter_3")
              if i["code"] == "LEVEL_DEATH_HEIR_NOT_IN_POOL"]
    assert {i["pointer"] for i in issues} == {f"/floors/2/enemyPool/{host}/enemyId",
                                              "/floors/2/bossId"}
    assert any("summoned by boss 'blight_sac_sovereign'" in i["message"] for i in issues)


# ── OZX-1792: variants are one creature ────────────────────────────────────


def test_a_variant_shares_the_family(ds):
    entries = pool(ds, "chapter_3", 1)
    group = index_of(entries, "blight_sac_plant2")
    entries[group]["variantIds"].append("blight_sac_stalker")
    vi = len(entries[group]["variantIds"]) - 1
    assert ("VARIANT_FAMILY_MISMATCH", f"/floors/1/enemyPool/{group}/variantIds/{vi}") \
        in found(validate_plan(ds, "chapter_3"))


# ── OZX-1798: rail oil tanks on a factory line ─────────────────────────────


def test_the_shipped_line_shows_its_tanks(ds):
    entries = pool(ds, "chapter_5", 2)
    fi = index_of(entries, "rail_evil_factory")
    row = ds.plan_view("chapter_5")["floors"][2]["enemyPool"][fi]
    assert row["railOilTanks"] == [{"id": "rail_oil_tank", "weight": 0.5}]


@pytest.mark.parametrize("tanks, code, suffix", [
    ([{"railOilTankId": "rail_oil_tank"}, {"railOilTankId": "rail_oil_tank"}],
     "LEVEL_RAIL_OIL_TANK_INVALID", "/railOilTanks/1/railOilTankId"),
    ([{"railOilTankId": "rail_oil_tank", "weight": -1}],
     "LEVEL_RAIL_OIL_TANK_INVALID", "/railOilTanks/0/weight"),
    ([{"weight": 1}], "LEVEL_RAIL_OIL_TANK_INVALID", "/railOilTanks/0"),
    ([{"railOilTankId": "oil_tank"}], "LEVEL_RAIL_OIL_TANK_FORBIDDEN",
     "/railOilTanks/0/railOilTankId"),
])
def test_a_tank_list_is_checked(ds, tanks, code, suffix):
    entries = pool(ds, "chapter_5", 2)
    fi = index_of(entries, "rail_evil_factory")
    entries[fi]["railOilTanks"] = tanks
    assert found(validate_plan(ds, "chapter_5")) == [(code, f"/floors/2/enemyPool/{fi}{suffix}")]


def test_tanks_belong_on_a_factory_line(ds):
    entries = pool(ds, "chapter_5", 2)
    tank = [{"railOilTankId": "rail_oil_tank", "weight": 1}]
    husk = index_of(entries, "husk_green")
    entries[husk]["railOilTanks"] = tank
    moth = index_of(entries, "moth")
    entries[moth]["railOilTanks"] = tank
    train = index_of(entries, "rail_heavy_solo")
    entries[train]["railOilTanks"] = tank
    assert sorted(found(validate_plan(ds, "chapter_5"))) == sorted([
        ("LEVEL_RAIL_OIL_TANK_NOT_FROM_FACTORY", f"/floors/2/enemyPool/{husk}/railOilTanks"),
        ("LEVEL_RAIL_OIL_TANK_NO_LINE", f"/floors/2/enemyPool/{moth}/railOilTanks"),
        ("LEVEL_RAIL_TRAIN_FIELD_UNREAD", f"/floors/2/enemyPool/{train}"),
    ])


# ── OZX-1818: alwaysElite ──────────────────────────────────────────────────


def test_always_elite_needs_an_elite_block_and_no_spawner(ds):
    entries = pool(ds, "chapter_5", 2)
    moth = index_of(entries, "moth")
    entries[moth]["alwaysElite"] = True
    assert found(validate_plan(ds, "chapter_5")) == []
    assert ds.plan_view("chapter_5")["floors"][2]["enemyPool"][moth]["alwaysElite"] is True

    entries[moth]["variantIds"] = ["husk"]   # no elite block (and another family)
    assert ("LEVEL_ALWAYS_ELITE_NO_ELITE_BLOCK", f"/floors/2/enemyPool/{moth}/variantIds/0") \
        in found(validate_plan(ds, "chapter_5"))
    del entries[moth]["variantIds"]

    husk = index_of(entries, "husk_green")
    entries[husk]["alwaysElite"] = True
    train = index_of(entries, "rail_heavy_solo")
    entries[train]["alwaysElite"] = True
    assert sorted(found(validate_plan(ds, "chapter_5"))) == sorted([
        ("LEVEL_ALWAYS_ELITE_ON_SPAWNER", f"/floors/2/enemyPool/{husk}/alwaysElite"),
        ("LEVEL_RAIL_TRAIN_FIELD_UNREAD", f"/floors/2/enemyPool/{train}"),
    ])
