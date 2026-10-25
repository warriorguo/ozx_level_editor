"""Shared-root tufts: `sharedRoot` / `fanArcDegrees` (OLE-37 / OZX-1191).

A tentacle clump grows from ONE root: every member stands on the anchor
and they fan over `fanArcDegrees` along the anchor → room-centre axis. The
pair can be written on a pool entry (the sampler copies it onto the launch)
or on an encounter launch, and `EncounterValidator.SharedRootProblem` is the
one rule set for both. `spawn_tentacles` is the shipped launch.
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


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def launch(ds):
    doc = ds.get("EncounterData", "spawn_tentacles")
    before = copy.deepcopy(doc.value)
    yield next(s["action"] for s in doc.value["steps"]
               if (s.get("action") or {}).get("sharedRoot"))
    doc.value.clear()
    doc.value.update(before)


@pytest.fixture
def entry(ds):
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value)
    e = doc.value["floors"][0]["enemyPool"][0]
    for k in ("clusterSizeMin", "clusterSizeMax", "clusterRadius", "variantIds"):
        e.pop(k, None)
    yield e
    doc.value.clear()
    doc.value.update(before)


def enc_codes(ds):
    return [i["code"] for i in validate_encounter(ds, "spawn_tentacles")]


def plan_codes(ds):
    return [i["code"] for i in validate_plan(ds, "chapter_3")]


def test_the_shipped_tuft_is_clean_and_needs_no_radius(ds, launch):
    assert not launch.get("clusterRadius")
    assert not [c for c in enc_codes(ds) if c.startswith("CLUSTER")]


@pytest.mark.parametrize("arc", [0, -10, 361])
def test_the_arc_lives_in_zero_to_three_sixty(ds, launch, arc):
    launch["fanArcDegrees"] = arc
    assert "CLUSTER_SHARED_ROOT_INVALID" in enc_codes(ds)


def test_a_tuft_of_one_shares_nothing(ds, launch):
    launch.update(clusterSizeMin=1, clusterSizeMax=1)
    assert "CLUSTER_SHARED_ROOT_INVALID" in enc_codes(ds)


def test_a_ring_radius_contradicts_a_shared_root(ds, launch):
    launch["clusterRadius"] = 1.5
    assert "CLUSTER_SHARED_ROOT_INVALID" in enc_codes(ds)


def test_the_spawner_channel_never_reads_it(ds, launch):
    launch["viaSpawner"] = "evil_ground_factory"
    assert "CLUSTER_SHARED_ROOT_INVALID" in enc_codes(ds)


def test_an_arc_without_the_flag_is_a_typo(ds, launch):
    launch["sharedRoot"] = False
    found = [i for i in validate_encounter(ds, "spawn_tentacles")
             if i["code"] == "CLUSTER_SHARED_ROOT_INVALID"]
    assert found and found[0]["pointer"].endswith("/fanArcDegrees")


def test_the_same_rules_hold_on_a_pool_entry(ds, entry):
    entry.update(sharedRoot=True, fanArcDegrees=90, clusterSizeMin=2, clusterSizeMax=3)
    assert not [c for c in plan_codes(ds) if c.startswith("CLUSTER")]
    entry["fanArcDegrees"] = 0
    assert "CLUSTER_SHARED_ROOT_INVALID" in plan_codes(ds)


def test_the_view_says_it_is_a_tuft(ds, entry):
    entry.update(sharedRoot=True, fanArcDegrees=120, clusterSizeMin=2, clusterSizeMax=3)
    cluster = ds.plan_view("chapter_3")["floors"][0]["enemyPool"][0]["cluster"]
    assert (cluster["sharedRoot"], cluster["fanArcDegrees"]) == (True, 120)
