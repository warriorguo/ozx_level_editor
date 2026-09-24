"""The spawner channel on a plan's enemy pool (OZX-1032).

A `category: "spawner"` enemy produces NOTHING by itself. `EnemyEmitController`
only drains what an encounter queued into it through `EncounterAction.viaSpawner`;
`produceIntervalSeconds` merely paces that drain. A hand-authored encounter
always writes the spawner and its minion step as a pair, but a procedural floor
had no way to say the second half — so a spawner dropped into an `enemyPool`
came out as an inert body with the HP of a wall.

`minionId` + `minionsPerSpawner` are that second half. Two things follow for
this tool, and the first is why the ticket is P1:

- the fields have to SURVIVE a save. The writer patches spans in the original
  bytes, so it cannot drop a field it does not know — but that is a property
  worth pinning rather than assuming, because a typed round trip would erase
  both and leave chapter_3's factory back where it started, with no error.
- the four `LevelPlanValidator.ValidateSpawnerPairs` rules belong at editing
  time, since each of them otherwise fails in silence or mid-room.

Counting is the other thing to get right: these minions are on TOP of
`enemiesPerRoom`, and they do not count against an encounter's `maxAlive`
either, so a room with a factory holds more bodies than its budget says.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel import jsonspan  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def plan(ds):
    """chapter_3, restored after each test.

    The Dataset is module-scoped and its parsed values are shared, so a test
    that edits one has to put it back.
    """
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value.get("floors"))
    yield doc
    doc.value["floors"] = before


def codes(ds, plan_id="chapter_3"):
    return [i["code"] for i in validate_plan(ds, plan_id)]


def pool_of(plan, floor=0):
    return plan.value["floors"][floor]["enemyPool"]


# ── the fields reach the editor at all ───────────────────────────────────


def test_the_shipped_plan_declares_a_spawner(ds):
    """chapter_3 is the first plan to use the channel; if this stops being
    true the rest of this file is testing a shape nothing produces."""
    view = ds.plan_view("chapter_3")
    spawners = [e for f in view["floors"] for e in f["enemyPool"] if e["isSpawner"]]
    assert spawners, "no spawner entry in chapter_3"
    assert all(e["minionId"] and e["minionsPerSpawner"] > 0 for e in spawners)
    assert {e["enemyId"] for e in spawners} == {"evil_ground_factory"}


def test_a_half_declared_pair_is_not_a_spawner(ds, plan):
    """It reads as one to a designer and as an ordinary enemy to the sampler,
    which is the whole failure — so the view must not call it a spawner."""
    pool = pool_of(plan)
    entry = next(e for e in pool if e.get("minionId"))
    del entry["minionsPerSpawner"]
    view = ds.plan_view("chapter_3")
    shown = next(e for e in view["floors"][0]["enemyPool"]
                 if e["enemyId"] == entry["enemyId"])
    assert shown["isSpawner"] is False
    assert shown["minionId"] == entry["minionId"], "still shown, still wrong"


def test_the_fields_survive_a_write_untouched(ds):
    """A typed round trip would erase both and say nothing. The span writer
    patches the original bytes, so everything it was not asked about stays."""
    doc = ds.get("LevelBasePlanData", "chapter_3")
    raw = doc.path.read_bytes()
    before = jsonspan.parse(raw.decode("utf-8"))

    pool = before.value["floors"][0]["enemyPool"]
    spawner_index = next(i for i, e in enumerate(pool) if e.get("minionId"))
    patched = jsonspan.patch(before, [{
        "op": "set",
        "path": f"/floors/0/enemyPool/{spawner_index}/weight",
        "value": 0.5,
    }])

    after = jsonspan.parse(patched)
    entry = after.value["floors"][0]["enemyPool"][spawner_index]
    assert entry["weight"] == 0.5
    assert entry["minionId"] == pool[spawner_index]["minionId"]
    assert entry["minionsPerSpawner"] == pool[spawner_index]["minionsPerSpawner"]
    # And nothing else in the file moved.
    assert len(patched) - len(raw.decode("utf-8")) == len("0.5") - len("0.4")


# ── ValidateSpawnerPairs, moved forward to editing time ──────────────────


def test_the_shipped_plan_has_no_spawner_findings(ds):
    assert not [c for c in codes(ds) if c.startswith("LEVEL_SPAWNER_")]


def test_half_a_pair_is_an_error(ds, plan):
    entry = next(e for e in pool_of(plan) if e.get("minionId"))
    keep = entry["minionsPerSpawner"]

    del entry["minionsPerSpawner"]
    assert "LEVEL_SPAWNER_PAIR_INCOMPLETE" in codes(ds)

    entry["minionsPerSpawner"] = keep
    del entry["minionId"]
    assert "LEVEL_SPAWNER_PAIR_INCOMPLETE" in codes(ds)


def test_a_spawner_may_not_produce_itself(ds, plan):
    entry = next(e for e in pool_of(plan) if e.get("minionId"))
    entry["minionId"] = entry["enemyId"]
    assert "LEVEL_SPAWNER_SELF_MINION" in codes(ds)


def test_a_spawner_may_not_produce_another_spawner(ds, plan):
    """Chained production has no population bound — maxAlive gates whole
    encounter steps and cannot reach inside one."""
    pool = pool_of(plan)
    first = next(e for e in pool if e.get("minionId"))
    second = next(e for e in pool if e is not first and not e.get("minionId"))
    # Make `second` a spawner too, then point `first` at it.
    second["minionId"] = next(e["enemyId"] for e in pool
                              if e is not first and e is not second)
    second["minionsPerSpawner"] = 2
    first["minionId"] = second["enemyId"]
    assert "LEVEL_SPAWNER_CHAINED" in codes(ds)


def test_the_minion_has_to_be_a_pool_member(ds, plan):
    """Outside the pool it bypasses coverage, the role/spawn-cell check and
    the elite audit, and surfaces as EnemyService.Spawn throwing mid-room."""
    entry = next(e for e in pool_of(plan) if e.get("minionId"))
    outside = next(d.id for d in ds.of_type("EnemyData")
                   if d.id not in {e["enemyId"] for e in pool_of(plan)})
    entry["minionId"] = outside
    found = codes(ds)
    assert "LEVEL_SPAWNER_MINION_NOT_IN_POOL" in found
    # A real enemy that is merely absent from the pool is that one finding,
    # not also a dangling reference.
    assert "DATA_REFERENCE_MISSING" not in found


def test_a_minion_that_is_not_an_enemy_at_all_is_still_caught(ds, plan):
    pool = pool_of(plan)
    entry = next(e for e in pool if e.get("minionId"))
    pool.append({"enemyId": "no_such_enemy", "weight": 1.0})
    entry["minionId"] = "no_such_enemy"
    found = codes(ds)
    assert "DATA_REFERENCE_MISSING" in found
    assert "LEVEL_SPAWNER_MINION_NOT_IN_POOL" not in found


def test_an_entry_with_no_channel_is_not_a_finding(ds, plan):
    """Every pool predating OZX-1032 writes neither field."""
    for entry in pool_of(plan):
        entry.pop("minionId", None)
        entry.pop("minionsPerSpawner", None)
    assert not [c for c in codes(ds) if c.startswith("LEVEL_SPAWNER_")]
