"""Enemy families and the tier an area may deploy (OLE-56 / OZX-1427–1429).

A family is several variants of one creature, tier 1 the weakest. The run
opens with tier 1 of each and every further area unlocks one more: the area
reached k-th deploys tier ≤ k, k its earliest arrival on any route.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import _arrival_order, validate_enemy, validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


def test_arrival_follows_the_chain_without_links():
    assert _arrival_order([], 3) == [1, 2, 3]


def test_arrival_is_the_shortest_route_with_links():
    """chapter_5's shape: 0→1 door, 0→2 stair, 2→3 door, 3→1 stair. Area 1
    is next door to the entry, however the run is actually played."""
    links = [{"fromArea": 0, "toArea": 1}, {"fromArea": 0, "toArea": 2},
             {"fromArea": 2, "toArea": 3}, {"fromArea": 3, "toArea": 1}]
    assert _arrival_order(links, 4) == [1, 2, 2, 3]


def test_an_unreached_area_has_no_order():
    assert _arrival_order([{"fromArea": 0, "toArea": 1}], 3) == [1, 2, None]


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def restoring(ds, data_type, entity_id):
    doc = ds.get(data_type, entity_id)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


@pytest.fixture
def large(ds):
    yield from restoring(ds, "EnemyData", "alien_alpha_large")


@pytest.fixture
def plan(ds):
    yield from restoring(ds, "LevelBasePlanData", "chapter_3")


@needs_data
def test_the_shipped_families_and_pools_are_clean(ds):
    for d in ds.of_type("EnemyData"):
        assert not [i for i in validate_enemy(ds, d.id) if "FAMILY" in i["code"]], d.id
    for d in ds.of_type("LevelBasePlanData"):
        assert not [i for i in validate_plan(ds, d.id) if "TIER" in i["code"]], d.id


@needs_data
@pytest.mark.parametrize("change", [{"tier": 0}, {"family": None}, {"tier": -1}])
def test_family_and_tier_come_as_a_pair(ds, large, change):
    for k, v in change.items():
        if v is None:
            large.pop(k)
        else:
            large[k] = v
    assert any(c in ("ENEMY_FAMILY_HALF_DECLARED", "ENEMY_FAMILY_INCONSISTENT")
               for c in [i["code"] for i in validate_enemy(ds, "alien_alpha_large")])


@needs_data
def test_a_family_has_one_variant_per_tier(ds, large):
    large["tier"] = 2   # alien_alpha_medium is tier 2 already
    found = [i for i in validate_enemy(ds, "alien_alpha_medium")
             if i["code"] == "ENEMY_FAMILY_INCONSISTENT"]
    assert found and "1..3" in found[0]["message"]


@needs_data
def test_a_strong_variant_in_the_entry_area_is_refused(ds, plan):
    """The entry area deploys tier 1 only."""
    plan["floors"][0]["enemyPool"].append({"enemyId": "alien_alpha_large", "weight": 1})
    found = [i for i in validate_plan(ds, "chapter_3") if i["code"] == "LEVEL_VARIANT_TIER_TOO_EARLY"]
    assert found and found[0]["pointer"].startswith("/floors/0/enemyPool/")


@needs_data
def test_the_third_area_may_deploy_tier_three(ds, plan):
    plan["floors"][2]["enemyPool"].append({"enemyId": "alien_alpha_large", "weight": 1})
    assert not [i for i in validate_plan(ds, "chapter_3") if i["code"] == "LEVEL_VARIANT_TIER_TOO_EARLY"]


@needs_data
def test_every_variant_of_a_group_is_checked(ds, plan):
    plan["floors"][0]["enemyPool"].append(
        {"enemyId": "alien_alpha_small", "variantIds": ["alien_alpha_medium"], "weight": 1})
    found = [i for i in validate_plan(ds, "chapter_3") if i["code"] == "LEVEL_VARIANT_TIER_TOO_EARLY"]
    assert found and found[0]["pointer"].endswith("/variantIds/0")


@needs_data
def test_the_pool_row_says_which_tier(ds, plan):
    plan["floors"][2]["enemyPool"].append({"enemyId": "alien_alpha_large", "weight": 1})
    row = ds.plan_view("chapter_3")["floors"][2]["enemyPool"][-1]
    assert row["family"] == {"id": "alien_alpha", "tier": 3}
