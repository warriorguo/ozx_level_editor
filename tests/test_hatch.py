"""An egg's clutch (OZX-1063).

`hatchEnemyId` used to be the whole feature: one egg, one occupant. It is now
one end of a pool — `hatchPool` is the general form and `hatchEnemyId` is a
single-entry one — and a clutch may hold several, drawn per occupant.

Two things make this the editor's business rather than trivia.

**The channels must not both be set.** `HatchBrood.Validate` refuses it rather
than picking a winner, and the reason is named in the source: this is
`dropTableIdOverride` again (OZX-1041), one field carrying two meanings and
overwriting itself. But the engine only checks it from `HatchState.Enter` —
three seconds of incubation into a fight the player is already in, with the
room's doors held shut by the egg.

**The clutch is charged to no budget.** `PoolSampler` samples bodies placed on
spawn points; hatched occupants never pass through it, so they count against
neither `FloorPlan.enemiesPerRoom` nor the encounter's `maxAlive`. A room that
draws three eggs holds six to nine more bodies than any number on it says —
measured upstream at 4.74 → 6.49 plants per room on chapter_3 floor 1. Any
view that reports "how big is this fight" and ignores the clutch is wrong in
one direction, always.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_enemy  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

EGG = "blight_sac_egg2"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def egg(ds):
    """The shipped egg, restored after each test."""
    doc = ds.get("EnemyData", EGG)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def codes(ds, enemy_id=EGG):
    return [i["code"] for i in validate_enemy(ds, enemy_id)]


# ── the shipped egg ──────────────────────────────────────────────────────


def test_the_shipped_corpus_has_no_hatch_findings(ds):
    for doc in ds.of_type("EnemyData"):
        found = codes(ds, doc.id)
        assert not found, (doc.id, found)


def test_the_egg_reports_its_clutch(ds):
    brief = ds.hatch_brief(ds.get("EnemyData", EGG).value)
    assert brief["pool"] == ["blight_sac_plant2", "blight_sac_plant3",
                             "blight_sac_plant4"]
    assert (brief["countMin"], brief["countMax"]) == (2, 3)
    assert brief["spreadRadius"] > 0
    assert brief["ambiguous"] is False


def test_an_enemy_that_is_not_an_egg_has_no_brief(ds):
    """67 of the 68 enemies. Reporting `None` rather than an empty clutch is
    what keeps the egg visible among them."""
    assert ds.hatch_brief(ds.get("EnemyData", "jammer").value) is None
    assert codes(ds, "jammer") == []


def test_an_unset_count_is_exactly_one(ds, egg):
    """The pre-clutch behaviour, which is what every other egg-like enemy
    written before this field would get."""
    del egg["hatchCountMin"]
    del egg["hatchCountMax"]
    brief = ds.hatch_brief(egg)
    assert (brief["countMin"], brief["countMax"]) == (1, 1)


# ── the clutch is outside every budget ───────────────────────────────────


def test_a_wave_of_eggs_reports_the_bodies_it_adds(ds):
    """Three eggs is three placed bodies and six to nine more behind them."""
    waves = ds.encounter_enemies(None, inline={"steps": [
        {"action": {"verb": "launch", "enemyId": EGG, "min": 3, "max": 3}}]})
    wave = waves[0]
    assert wave["count"] == 3, "the sampler's number is unchanged"
    assert (wave["hatchedMin"], wave["hatchedMax"]) == (6, 9)
    assert wave["hatch"]["pool"]


def test_an_ordinary_wave_adds_nothing(ds):
    waves = ds.encounter_enemies(None, inline={"steps": [
        {"action": {"verb": "launch", "enemyId": "jammer", "min": 4, "max": 4}}]})
    assert waves[0]["hatch"] is None
    assert waves[0]["hatchedMax"] == 0


# ── HatchBrood.Validate, moved to editing time ───────────────────────────


def test_both_channels_at_once_is_refused(ds, egg):
    """Not a precedence question — the same shape OZX-1041 had to split."""
    egg["hatchEnemyId"] = "blight_sac_plant2"
    assert "ENEMY_HATCH_CHANNEL_AMBIGUOUS" in codes(ds)
    # And the view must not quietly pick one either.
    assert ds.hatch_brief(egg)["ambiguous"] is True


def test_an_empty_pool_slot_is_refused(ds, egg):
    """The draw is uniform over the whole list, so an empty slot takes its
    share and that share of the clutch silently fails to spawn."""
    egg["hatchPool"] = ["blight_sac_plant2", ""]
    assert "ENEMY_HATCH_POOL_EMPTY_SLOT" in codes(ds)


def test_a_pool_entry_that_is_not_an_enemy_is_refused(ds, egg):
    """The cross-reference the engine cannot make: it holds one enemy's data,
    not the set of ids that exist."""
    egg["hatchPool"] = ["blight_sac_plant2", "no_such_enemy"]
    found = codes(ds)
    assert "DATA_REFERENCE_MISSING" in found


def test_a_single_channel_id_is_checked_too(ds, egg):
    del egg["hatchPool"]
    egg["hatchEnemyId"] = "no_such_enemy"
    assert "DATA_REFERENCE_MISSING" in codes(ds)


def test_a_clutch_with_nowhere_to_spread_is_refused(ds, egg):
    """These are rooted bodies: the physics shove that would separate two
    sprouts on one point never happens, so they stay there for the whole run."""
    egg["hatchSpreadRadius"] = 0
    assert "ENEMY_HATCH_NO_SPREAD" in codes(ds)

    # One occupant needs no radius — it takes the egg's own spot.
    egg["hatchCountMin"] = 1
    egg["hatchCountMax"] = 1
    assert "ENEMY_HATCH_NO_SPREAD" not in codes(ds)


def test_a_reversed_range_is_a_warning_not_an_error(ds, egg):
    """`RollCount` clamps instead of throwing — a reversed pair is designer
    noise, and the contradiction worth failing on is between the CHANNELS."""
    egg["hatchCountMin"] = 3
    egg["hatchCountMax"] = 2
    issues = validate_enemy(ds, EGG)
    reversed_ = [i for i in issues
                 if i["code"] == "ENEMY_HATCH_COUNT_RANGE_REVERSED"]
    assert reversed_ and reversed_[0]["severity"] == "warning"
    # And the view reports what the roll actually produces, not what was typed.
    assert ds.hatch_brief(egg)["countMax"] == 3
