"""Two chapter-level knobs that decide how much a room gets.

**`skillDropLevelCap`** (OZX-1069, top level on `LevelBasePlanData`). Once a
skill reaches this level the chapter stops DROPPING it — four channels at
once, per skill. It is not a level clamp: `UpgradeSkill` is unchanged and the
ceiling is still `SkillData.maxLevel`. Top level on purpose, because skill
level is carried across the whole run, and a cap that changed on the stairs
would seal a skill on f1 and unseal it on f2.

**`enemyBudgetCut`** (OZX-1089, per `stageTypes` entry). The SHARE of the
floor's `enemiesPerRoom` a stage gives back. The direction is the point:
`JsonUtility` reads an omitted float as 0, so a "0 means ×1" factor would
read an authored `0` — "no enemies in this stage" — as "take the full
budget", which is the bug the field exists to fix. As a cut, omitted = 0 =
nothing changes, and the dangerous value has to be typed.

The cap carries a finding upstream does not: a cap above every maxLevel the
chapter can drop is silently inert — written, tuned-looking, and unable to
bite anything. The ticket cited chapter_3 as exactly that case, but OZX-1072
raised flute_companion to maxLevel 10 afterwards, so the shipped default of 5
now does fire there.
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


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def plan(ds):
    """chapter_3, restored after each test."""
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def codes(ds, plan_id="chapter_3"):
    return [i["code"] for i in validate_plan(ds, plan_id)]


# ── skillDropLevelCap ────────────────────────────────────────────────────


def test_an_unwritten_cap_reports_the_default_and_says_so(ds):
    """0 is "unauthored", which resolves to 5. A view that showed 0 would
    read as "every skill is sealed from the start"."""
    view = ds.plan_view("chapter_3")
    assert view["skillDropLevelCap"] == 0
    assert view["skillDropLevelCapDeclared"] is False
    assert view["skillDropLevelCapEffective"] == 5


def test_a_written_cap_is_reported_as_written(ds, plan):
    plan["skillDropLevelCap"] = 3
    view = ds.plan_view("chapter_3")
    assert view["skillDropLevelCapDeclared"] is True
    assert view["skillDropLevelCapEffective"] == 3


def test_a_negative_cap_is_refused(ds, plan):
    """There is no "no cap" value: a plan that wants the filter out of the
    way writes a number above every maxLevel it can drop. A negative would
    seal every skill from the first room instead."""
    plan["skillDropLevelCap"] = -1
    assert "LEVEL_SKILL_DROP_CAP_NEGATIVE" in codes(ds)


def test_an_unwritten_cap_is_never_called_inert(ds):
    """Every plan leaves it at 0 today; saying "this does nothing" on all of
    them would be noise rather than a finding."""
    for doc in ds.of_type("LevelBasePlanData"):
        assert "LEVEL_SKILL_DROP_CAP_UNREACHABLE" not in codes(ds, doc.id)


def test_the_droppable_skills_are_read_off_the_plan(ds):
    """Which skills a cap could bite is not a property of the cap; it is the
    set of skill items this chapter's floors can actually drop, resolved to
    the SkillData each one installs."""
    from ozxlevel.validate import _droppable_skill_levels
    levels = _droppable_skill_levels(
        ds, ds.get("LevelBasePlanData", "chapter_3").value)
    assert levels == {"flute_companion": 10, "shield_blue": 10}


def test_a_cap_above_every_droppable_skill_is_inert(ds, plan):
    """The finding upstream does not make: the number is written, looks
    tuned, and nothing it could bite can reach it."""
    plan["skillDropLevelCap"] = 11
    issues = validate_plan(ds, "chapter_3")
    inert = [i for i in issues if i["code"] == "LEVEL_SKILL_DROP_CAP_UNREACHABLE"]
    assert inert, "nothing chapter_3 drops reaches level 11"
    assert inert[0]["severity"] == "warning"
    assert "maxLevel" in inert[0]["message"]


def test_a_cap_a_skill_can_reach_is_not_flagged(ds, plan):
    """OZX-1072 raised flute_companion to maxLevel 10, so the shipped default
    of 5 now bites on chapter_3 — the ticket's "never fires, maxLevel 2 / 3"
    measurement predates that and no longer holds."""
    for reachable in (2, 5, 10):
        plan["skillDropLevelCap"] = reachable
        assert "LEVEL_SKILL_DROP_CAP_UNREACHABLE" not in codes(ds), reachable


# ── enemyBudgetCut ───────────────────────────────────────────────────────


def test_the_shipped_plans_cut_release_to_nothing(ds):
    """OZX-1079 makes release the breather in front of the exit; this is how
    a procedural floor says that."""
    cuts = [(p.id, f["index"], s["stageType"], s["enemyBudgetCut"])
            for p in ds.of_type("LevelBasePlanData")
            for f in ds.plan_view(p.id)["floors"]
            for s in f["stageTypes"] if s["enemyBudgetCut"]]
    assert cuts, "no shipped plan cuts a stage's budget"
    assert all(stage == "release" and cut == 1.0 for _, _, stage, cut in cuts)


def test_the_shipped_plans_have_no_budget_findings(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        found = [c for c in codes(ds, doc.id) if "STAGE_BUDGET" in c]
        assert not found, (doc.id, found)


def test_a_partial_cut_never_rounds_down_to_nothing(ds):
    """0.9 off a budget of 4 is 0.4 of an enemy. Answering "none" would make
    an empty room reachable by a number that does not say so — a room with no
    fight in it has to be asked for with a full cut."""
    from ozxlevel.dataset import _stage_budget
    assert _stage_budget(4, 0.9) == 1
    assert _stage_budget(12, 0.99) == 1
    assert _stage_budget(4, 1.0) == 0, "only a full cut empties the room"


def test_an_omitted_cut_is_the_full_budget(ds):
    from ozxlevel.dataset import _stage_budget
    assert _stage_budget(12, 0) == 12


def test_rounding_goes_away_from_zero_as_it_does_upstream(ds):
    """C# rounds half away from zero; Python's round() is half-even, so 0.5
    would land on a different integer without saying anything."""
    from ozxlevel.dataset import _stage_budget
    # 5 × (1 - 0.5) = 2.5 → 3 upstream, 2 under banker's rounding.
    assert _stage_budget(5, 0.5) == 3


def test_a_cut_outside_the_unit_range_is_refused(ds, plan):
    stage = plan["floors"][0]["stageTypes"][0]
    for bad in (-0.1, 1.5):
        stage["enemyBudgetCut"] = bad
        assert "LEVEL_STAGE_BUDGET_CUT_INVALID" in codes(ds), bad


def test_a_full_cut_beside_an_encounter_id_is_refused(ds, plan):
    """On a floor with an enemyPool the authored encounterId is dead text —
    the generator overwrites it with a sampled inlineEncounter. A full cut
    makes it skip the room, which WAKES that text up and staffs the very
    stage that asked for no enemies."""
    stage = next(s for s in plan["floors"][0]["stageTypes"]
                 if (s.get("enemyBudgetCut") or 0) >= 1)
    stage["encounterId"] = "spawn_ch1_teach1_01"
    assert "LEVEL_STAGE_BUDGET_CUT_CONTRADICTS_ENCOUNTER" in codes(ds)


def test_an_encounter_id_without_a_full_cut_is_fine(ds, plan):
    """That is the ordinary dead-text case, which predates this field and is
    not what the rule is about."""
    stage = plan["floors"][0]["stageTypes"][0]
    stage["encounterId"] = "spawn_ch1_teach1_01"
    assert "LEVEL_STAGE_BUDGET_CUT_CONTRADICTS_ENCOUNTER" not in codes(ds)


def test_an_inherited_budget_resolves_through_the_defaults(ds):
    """chapter_3 keeps `enemiesPerRoom` in floorDefaults (OZX-1011). Before
    the merge this read as absent; it is the chapter's 12."""
    view = ds.plan_view("chapter_3")
    floor = view["floors"][0]
    assert floor["enemiesPerRoom"] == 12
    assert "enemiesPerRoom" in floor["inherited"]
    assert all(s["enemyBudget"] == 12
               for s in floor["stageTypes"] if not s["enemyBudgetCut"])


def test_the_resolved_budget_is_absent_rather_than_zero_when_unknown(ds):
    """With no budget written anywhere, reporting 0 would read as "no
    enemies", which is the one thing a full cut means."""
    plan = ds.get("LevelBasePlanData", "chapter_3").value
    defaults = plan.pop("floorDefaults")
    try:
        view = ds.plan_view("chapter_3")
        assert view["floors"][0]["enemiesPerRoom"] is None
        assert all(s["enemyBudget"] is None for s in view["floors"][0]["stageTypes"])
    finally:
        plan["floorDefaults"] = defaults

    # A plan that writes it per floor resolves for real.
    ten = ds.plan_view("level_10")
    floor = next(f for f in ten["floors"] if f["enemiesPerRoom"])
    assert all(s["enemyBudget"] == floor["enemiesPerRoom"]
               for s in floor["stageTypes"] if not s["enemyBudgetCut"])