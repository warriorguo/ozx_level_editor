"""Skill platforms a plan generates — `activeSkillIds`, `storyRooms` and
`startSkillPlatform` (OLE-62 / OZX-1483, OLE-64 / OZX-1488).

After generation StoryRoomPlanner hangs countMin–countMax one-door `story`
rooms off branch tips and cycle corners, each with one skill platform, and
StartSkillPlatformPlanner appends a platform to the start room. Both draw
from the plan's one `activeSkillIds` list without replacement
(`SkillPlatformDeck`), so a run never offers a skill twice. The passes are
the generator's and run inside genlevel; what this tool owns is editing the
blocks and catching, at edit time, what `SkillPlatformPlanRules`,
`LevelPlanValidator.ValidateStoryTilemaps` and the platform adapter throw.
"""

import copy
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import generate  # noqa: E402
from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

PLAN = "chapter_3"
PLATFORM_POINTERS = ("/storyRooms", "/startSkillPlatform", "/activeSkillIds")


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def platform_issues(ds, pid=PLAN):
    return [i for i in validate_plan(ds, pid)
            if i["code"].startswith(("LEVEL_STORY", "LEVEL_ACTIVE_SKILLS", "LEVEL_START_SKILL"))
            or (i["pointer"] or "").startswith(PLATFORM_POINTERS)]


def plan_with(ds, story=None, start=None, skills=None, **absent):
    """Rewrite chapter_3's blocks (in memory) and return its platform findings.
    A block passed as a dict is merged into the shipped one; `no_story`,
    `no_start` and `no_skills` drop that key."""
    plan = ds.get("LevelBasePlanData", PLAN).value
    for key, fields, gone in (("storyRooms", story, absent.get("no_story")),
                              ("startSkillPlatform", start, absent.get("no_start"))):
        if gone:
            plan.pop(key, None)
        elif fields is not None:
            block = copy.deepcopy(plan.get(key) or {})
            block.update(fields)
            plan[key] = block
    if absent.get("no_skills"):
        plan.pop("activeSkillIds", None)
    elif skills is not None:
        plan["activeSkillIds"] = skills
    return platform_issues(ds)


def codes(issues):
    return sorted(i["code"] for i in issues)


def test_the_shipped_plans_are_clean(ds):
    for pid in ds.ids_of("LevelBasePlanData"):
        assert platform_issues(ds, pid) == [], pid


def test_the_view_carries_the_blocks(ds):
    view = ds.plan_view(PLAN)
    story, platforms = view["storyRooms"], view["skillPlatforms"]
    assert story["declared"] and story["active"]
    assert story["countMax"] >= story["countMin"] >= 1
    assert platforms["declared"] and platforms["skills"]
    assert all(not k["missing"] for k in platforms["skills"])
    assert platforms["start"] == {"pointer": "/startSkillPlatform", "declared": True,
                                  "active": True, "charges": 3}
    assert platforms["drawn"] == 1 + story["countMax"]


def test_a_start_platform_alone_draws_one(ds):
    """chapter_4 has no story rooms; its two skills feed the start platform."""
    view = ds.plan_view("chapter_4")
    assert not view["storyRooms"]["declared"]
    assert view["skillPlatforms"]["drawn"] == 1


def test_no_blocks_is_no_platforms(ds):
    assert plan_with(ds, no_story=True, no_start=True, no_skills=True) == []
    view = ds.plan_view(PLAN)
    assert view["storyRooms"] == {"declared": False, "active": False, "pointer": "/storyRooms"}
    assert view["skillPlatforms"]["start"]["declared"] is False
    assert view["skillPlatforms"]["drawn"] == 0


def test_a_list_nothing_draws_from_throws(ds):
    assert codes(plan_with(ds, no_story=True, no_start=True)) == ["LEVEL_ACTIVE_SKILLS_UNUSED"]


def test_the_old_story_rooms_list_is_ignored(ds):
    """OZX-1488 moved it to the plan; JsonUtility drops the old field."""
    issues = plan_with(ds, story={"activeSkillIds": ["laser"]})
    assert codes(issues) == ["LEVEL_PLAN_FIELD_MOVED"]
    assert issues[0]["pointer"] == "/storyRooms/activeSkillIds"


def test_countmax_zero_with_other_fields_throws(ds):
    assert codes(plan_with(ds, story={"countMax": 0})) == ["LEVEL_STORY_ROOMS_UNDECLARED"]


def test_an_all_zero_story_block_is_the_same_as_none(ds):
    assert plan_with(ds, story={"countMin": 0, "countMax": 0, "charges": 0}) == []


@pytest.mark.parametrize("lo,hi", [(0, 3), (4, 3)])
def test_count_range_must_be_one_up_to_max(ds, lo, hi):
    assert "LEVEL_STORY_ROOMS_COUNT_INVALID" in codes(
        plan_with(ds, story={"countMin": lo, "countMax": hi}))


def test_a_story_platform_needs_charges(ds):
    assert codes(plan_with(ds, story={"charges": 0})) == ["LEVEL_STORY_ROOMS_NO_CHARGES"]


def test_negative_start_charges_throw(ds):
    assert codes(plan_with(ds, start={"charges": -1})) == ["LEVEL_START_SKILL_PLATFORM_NO_CHARGES"]


def test_zero_start_charges_silently_declare_nothing(ds):
    issues = plan_with(ds, start={"charges": 0})
    assert [(i["code"], i["severity"]) for i in issues] == [
        ("LEVEL_START_SKILL_PLATFORM_UNDECLARED", "warning")]
    assert ds.plan_view(PLAN)["skillPlatforms"]["drawn"] == 3


def test_the_list_must_cover_start_plus_story_max(ds):
    """Four skills cover 1 + 3; three do not."""
    issues = plan_with(ds, skills=["laser", "shockwave", "bullet_storm"])
    assert codes(issues) == ["LEVEL_ACTIVE_SKILLS_TOO_FEW"]
    assert "draw 4 (startSkillPlatform 1 + storyRooms.countMax 3)" in issues[0]["message"]
    assert plan_with(ds, no_start=True) == []


def test_duplicate_and_empty_ids_throw(ds):
    found = codes(plan_with(ds, skills=["laser", "laser", "", "shockwave", "bullet_storm"]))
    assert found == ["LEVEL_ACTIVE_SKILLS_DUPLICATE", "LEVEL_ACTIVE_SKILLS_EMPTY"]


def test_a_passive_skill_is_refused_by_the_platform(ds):
    passive = next(d.id for d in ds.of_type("SkillData") if d.value.get("type") == "passive")
    issues = plan_with(ds, skills=["laser", "shockwave", "bullet_storm", passive])
    assert codes(issues) == ["ROOM_PLACEMENT_SKILL_NOT_ACTIVE"]
    assert issues[0]["pointer"] == "/activeSkillIds/3"


def test_an_unknown_skill_is_a_reference_error(ds):
    assert codes(plan_with(ds, skills=["laser", "shockwave", "bullet_storm", "no_such_skill"])) == [
        "DATA_REFERENCE_MISSING"]


def test_a_theme_without_a_story_room_for_every_side(ds, monkeypatch):
    """Only a top-door story template: bottom, left and right are unserved."""
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    top_only = {"stageType": "story", "category": "", "openDoors": 1}
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others + [top_only])
    issues = plan_with(ds)
    assert {i["code"] for i in issues} == {"LEVEL_STORY_TILEMAP_MISSING"}
    assert "right (2), bottom (4), left (8)" in issues[0]["message"]


def test_no_story_rooms_need_no_story_tilemap(ds, monkeypatch):
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others)
    assert plan_with(ds, no_story=True, skills=["laser"]) == []


def test_a_superset_template_serves_every_single_door(ds, monkeypatch):
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    four = {"stageType": "story", "category": "", "openDoors": 15}
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others + [four])
    assert plan_with(ds) == []


def test_a_sample_counts_its_story_rooms():
    doc = {"floors": [{"rooms": [{"stageType": "start"}, {"stageType": "story"},
                                 {"stageType": "story"}]}]}
    assert generate.summarize(doc)["storyRooms"] == 2


def test_the_blocks_can_be_written_edited_and_taken_back(tmp_path):
    """`put` adds each block, a skill goes in and out one element at a time,
    and removing the blocks gives back the file byte for byte."""
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/levels",
                    root / "Assets/StreamingAssets/GameData/levels")
    api = Api(root)
    plan = next(d for d in api.dataset.of_type("LevelBasePlanData")
                if not {"storyRooms", "activeSkillIds", "startSkillPlatform"} & d.value.keys())
    before = plan.path.read_bytes()
    # One block per write, as the cards send them: new keys share an
    # insertion point, so the writer refuses them as one batch.
    for path, value in (("/activeSkillIds", ["laser"]),
                        ("/startSkillPlatform", {"charges": 3}),
                        ("/storyRooms", {"countMin": 2, "countMax": 3, "charges": 3})):
        api.edit("LevelBasePlanData", plan.id, [{"op": "put", "path": path, "value": value}])
    api.edit("LevelBasePlanData", plan.id,
             [{"op": "append", "path": "/activeSkillIds", "value": "shockwave"},
              {"op": "put", "path": "/startSkillPlatform/charges", "value": 5}])
    value = api.dataset.get("LevelBasePlanData", plan.id).value
    assert value["activeSkillIds"] == ["laser", "shockwave"]
    assert value["startSkillPlatform"] == {"charges": 5}
    for path in ("/storyRooms", "/startSkillPlatform", "/activeSkillIds"):
        api.edit("LevelBasePlanData", plan.id, [{"op": "remove", "path": path}])
    assert plan.path.read_bytes() == before
