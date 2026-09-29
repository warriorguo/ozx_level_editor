"""Variant groups and clumps on a pool entry or a wave (OZX-1063).

A variant group folds several forms of ONE creature into a single pool entry.
`enemyId` stays the representative; the entry is sampled, capped and counted
as one type, and each body it places draws its concrete id from
`{enemyId} + variantIds` — uniform, independent, with replacement.

The three Blight-Sac plants were three entries with three weights and three
caps, which made "2-3 sprouts in a patch" unsayable at this layer: the sampler
counts ENTRIES, so a patch had to be one entry before it could have a size.

Folding is not free, and the cost is what a reader of this tool most needs to
see. `PoolSampler`'s coverage tier deals one body per entry, so the group gets
one guaranteed body instead of three; chapter_3 floor 1 went from 9 entries to
7, which moved five of the twelve room slots from coverage to weighted draw and
raised whole-room enemy HP 7.4%. A row that shows only the representative
reads as one creature at one weight, which is the arithmetic that changed.

Clumping is the other half: the sampled bodies are placed in groups of
[clusterSizeMin, clusterSizeMax] around a shared anchor, overflow starting
another clump (5 = 3+2, never a wall of five). Inside a clump the executor's
minimum-spacing guard is deliberately waived, which is why the radius is not
decoration — it is the only thing left keeping the bodies off one point.
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

PLAN = "chapter_3"
# chapter_3 floor 1 is where the plants are folded.
FLOOR = 1


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def pool(ds):
    """chapter_3 floor 1's enemy pool, restored after each test."""
    doc = ds.get("LevelBasePlanData", PLAN)
    before = copy.deepcopy(doc.value["floors"][FLOOR]["enemyPool"])
    yield doc.value["floors"][FLOOR]["enemyPool"]
    doc.value["floors"][FLOOR]["enemyPool"] = before


def codes(ds):
    return [i["code"] for i in validate_plan(ds, PLAN)]


def group(pool):
    return next(e for e in pool if e.get("variantIds"))


def action_codes(ds, action):
    """Validate a throwaway one-step encounter carrying `action`."""
    doc = ds.get("EncounterData", "spawn_ch1_teach1_01")
    before = doc.value.get("steps")
    base = {"verb": "launch", "enemyId": "small_ground_patrol", "min": 1, "max": 1}
    doc.value["steps"] = [{"action": {**base, **action}}]
    try:
        return [i["code"] for i in validate_encounter(ds, doc.id)]
    finally:
        doc.value["steps"] = before


# ── the shipped shape ────────────────────────────────────────────────────


def test_the_shipped_plans_fold_variants(ds):
    groups = [e for pid in ("chapter_3", "chapter_4")
              for f in ds.plan_view(pid)["floors"] for e in f["enemyPool"]
              if e["isVariantGroup"]]
    assert groups, "no variant group in any shipped plan"
    assert all(e["enemyId"] not in e["variantIds"] for e in groups)


def test_a_folded_entry_reports_the_group_not_just_the_representative(ds):
    """The count on the row belongs to the group, so the row has to say so."""
    entry = next(e for e in ds.plan_view(PLAN)["floors"][FLOOR]["enemyPool"]
                 if e["isVariantGroup"])
    assert entry["enemyId"] == "blight_sac_plant2"
    assert entry["variantIds"] == ["blight_sac_plant3", "blight_sac_plant4"]
    # The fold concentrates three separate caps into one.
    assert entry["maxPerRoom"] == 8
    assert entry["cluster"] == {"declared": True, "sizeMin": 2,
                                "sizeMax": 3, "radius": 1.2}


def test_an_ordinary_entry_carries_neither(ds):
    entry = next(e for e in ds.plan_view(PLAN)["floors"][FLOOR]["enemyPool"]
                 if not e["isVariantGroup"])
    assert entry["variantIds"] == []
    assert entry["cluster"] is None


def test_the_shipped_plans_have_no_group_findings(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        found = [i["code"] for i in validate_plan(ds, doc.id)
                 if i["code"].startswith(("VARIANT_", "CLUSTER_"))]
        assert not found, (doc.id, found)


# ── variant rules ────────────────────────────────────────────────────────


def test_an_empty_variant_slot_is_refused(ds, pool):
    """The per-body draw is uniform over the list, so an empty slot takes its
    share and that share of the group fails to spawn."""
    group(pool)["variantIds"] = ["blight_sac_plant3", ""]
    assert "VARIANT_EMPTY_SLOT" in codes(ds)


def test_listing_the_representative_again_is_refused(ds, pool):
    """It is already in the draw; listing it doubles its share, which is a
    weight expressed by accident."""
    entry = group(pool)
    entry["variantIds"] = [entry["enemyId"], "blight_sac_plant3"]
    assert "VARIANT_REPEATS_REPRESENTATIVE" in codes(ds)


def test_a_variant_that_is_not_an_enemy_is_refused(ds, pool):
    group(pool)["variantIds"] = ["no_such_enemy"]
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_a_variant_with_a_different_role_is_refused(ds, pool):
    """The group takes ONE spawn layer, from the representative, so the odd
    variant stands on a layer never authored for it — silently, in whatever
    fraction of bodies the draw gave it."""
    entry = group(pool)
    rep_role = ds.get("EnemyData", entry["enemyId"]).value.get("role")
    other = next(d.id for d in ds.of_type("EnemyData")
                 if d.value.get("role") and d.value["role"] != rep_role)
    entry["variantIds"] = [other]
    assert "VARIANT_ROLE_MISMATCH" in codes(ds)


def test_the_role_rule_is_a_pool_rule_not_an_action_rule(ds):
    """An action has no pool to take a representative's layer from, and
    EncounterValidator does not check it either."""
    other = next(d.id for d in ds.of_type("EnemyData")
                 if d.value.get("role")
                 and d.value["role"] != ds.get("EnemyData",
                                               "small_ground_patrol").value.get("role"))
    assert "VARIANT_ROLE_MISMATCH" not in action_codes(ds, {"variantIds": [other]})


# ── clump rules ──────────────────────────────────────────────────────────


def test_a_clump_with_no_radius_is_refused(ds, pool):
    """The minimum-spacing guard is waived inside a clump on purpose, so the
    radius is the only thing keeping the bodies off one point."""
    group(pool)["clusterRadius"] = 0
    assert "CLUSTER_RADIUS_MISSING" in codes(ds)


def test_a_radius_with_no_clump_size_is_refused(ds, pool):
    entry = group(pool)
    entry["clusterSizeMin"] = 0
    entry["clusterSizeMax"] = 0
    assert "CLUSTER_RADIUS_UNUSED" in codes(ds)


def test_a_reversed_clump_range_is_refused(ds, pool):
    entry = group(pool)
    entry["clusterSizeMin"] = 5
    entry["clusterSizeMax"] = 2
    assert "CLUSTER_SIZE_RANGE_INVALID" in codes(ds)


def test_a_spawner_anchor_may_not_also_clump(ds, pool):
    """Minions go out through the viaSpawner channel, which places them at
    the spawner's own emit anchors and never reads these fields — so the
    clump would silently apply to the spawners themselves."""
    entry = group(pool)
    entry["minionId"] = next(e["enemyId"] for e in pool
                             if e["enemyId"] != entry["enemyId"])
    entry["minionsPerSpawner"] = 2
    assert "CLUSTER_ON_SPAWNER_ANCHOR" in codes(ds)


def test_neither_field_on_an_ordinary_entry_is_a_finding(ds, pool):
    """Every pool predating OZX-1063 writes none of the four."""
    for entry in pool:
        for f in ("variantIds", "clusterSizeMin", "clusterSizeMax", "clusterRadius"):
            entry.pop(f, None)
    assert not [c for c in codes(ds) if c.startswith(("VARIANT_", "CLUSTER_"))]


# ── the same four fields on an action ────────────────────────────────────


def test_a_wave_carries_the_group_too(ds):
    """The sampler copies an entry's values onto the step it synthesises."""
    waves = ds.encounter_enemies(None, inline={"steps": [{"action": {
        "verb": "launch", "enemyId": "blight_sac_plant2", "min": 3, "max": 3,
        "variantIds": ["blight_sac_plant3"],
        "clusterSizeMin": 2, "clusterSizeMax": 3, "clusterRadius": 1.2}}]})
    assert waves[0]["variantIds"] == ["blight_sac_plant3"]
    assert waves[0]["cluster"]["declared"] is True


def test_the_action_rules_mirror_the_pool_rules(ds):
    assert "VARIANT_EMPTY_SLOT" in action_codes(ds, {"variantIds": [""]})
    assert "VARIANT_REPEATS_REPRESENTATIVE" in action_codes(
        ds, {"variantIds": ["small_ground_patrol"]})
    assert "CLUSTER_RADIUS_MISSING" in action_codes(
        ds, {"clusterSizeMin": 2, "clusterSizeMax": 3})
    assert "CLUSTER_RADIUS_UNUSED" in action_codes(ds, {"clusterRadius": 1.2})


def test_the_spawner_channel_reads_none_of_them(ds):
    """A viaSpawner launch emits through the spawner's emit anchors, so both
    would be authored text that nothing acts on."""
    found = action_codes(ds, {
        "enemyId": "jammer", "viaSpawner": "evil_ground_factory",
        "variantIds": ["blight_sac_plant3"],
        "clusterSizeMin": 2, "clusterSizeMax": 3, "clusterRadius": 1.2})
    assert "VARIANT_ON_SPAWNER_CHANNEL" in found
    assert "CLUSTER_ON_SPAWNER_CHANNEL" in found
