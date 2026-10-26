"""Story rooms — `LevelBasePlanData.storyRooms` (OLE-62 / OZX-1483).

A per-run block: after generation, StoryRoomPlanner hangs countMin–countMax
one-door `story` rooms off branch tips and cycle corners, each with one
skill platform whose skill is drawn without replacement. The pass is the
generator's and runs inside genlevel; what this tool owns is editing the
block and catching, at edit time, what `StoryRoomRules`,
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


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def story(ds, **fields):
    """Replace chapter_3's block (in memory) and return its story findings."""
    plan = ds.get("LevelBasePlanData", PLAN).value
    if fields.pop("_absent", False):
        plan.pop("storyRooms", None)
    else:
        block = copy.deepcopy(plan.get("storyRooms") or {})
        block.update(fields)
        plan["storyRooms"] = block
    return [i for i in validate_plan(ds, PLAN)
            if i["code"].startswith("LEVEL_STORY") or (i["pointer"] or "").startswith("/storyRooms")]


def codes(issues):
    return sorted(i["code"] for i in issues)


def test_the_shipped_story_rooms_are_clean(ds):
    for pid in ds.ids_of("LevelBasePlanData"):
        block = ds.get("LevelBasePlanData", pid).value.get("storyRooms")
        if block:
            assert [i for i in validate_plan(ds, pid)
                    if (i["pointer"] or "").startswith("/storyRooms")
                    or i["code"].startswith("LEVEL_STORY")] == [], pid


def test_the_view_carries_the_block(ds):
    view = ds.plan_view(PLAN)["storyRooms"]
    assert view["declared"] and view["active"] and view["skillsDeclared"]
    assert view["countMax"] >= view["countMin"] >= 1
    assert all(not k["missing"] for k in view["skills"])


def test_no_block_is_no_story_rooms(ds):
    assert story(ds, _absent=True) == []
    plan = ds.get("LevelBasePlanData", PLAN).value
    assert ds.plan_view(PLAN)["storyRooms"] == {
        "declared": False, "active": False, "pointer": "/storyRooms"}
    assert "storyRooms" not in plan


def test_countmax_zero_with_other_fields_throws(ds):
    assert codes(story(ds, countMax=0)) == ["LEVEL_STORY_ROOMS_UNDECLARED"]


def test_an_all_zero_block_is_the_same_as_none(ds):
    assert story(ds, countMin=0, countMax=0, charges=0, activeSkillIds=[]) == []


@pytest.mark.parametrize("lo,hi", [(0, 3), (4, 3)])
def test_count_range_must_be_one_up_to_max(ds, lo, hi):
    assert "LEVEL_STORY_ROOMS_COUNT_INVALID" in codes(story(ds, countMin=lo, countMax=hi))


def test_a_platform_needs_charges(ds):
    assert codes(story(ds, charges=0)) == ["LEVEL_STORY_ROOMS_NO_CHARGES"]


def test_fewer_skills_than_countmax_throws(ds):
    assert codes(story(ds, activeSkillIds=["laser", "shockwave"], countMax=3)) == [
        "LEVEL_STORY_ROOMS_TOO_FEW_SKILLS"]


def test_duplicate_and_empty_ids_throw(ds):
    found = codes(story(ds, activeSkillIds=["laser", "laser", "", "shockwave"]))
    assert found == ["LEVEL_STORY_ROOMS_SKILL_DUPLICATE", "LEVEL_STORY_ROOMS_SKILL_EMPTY"]


def test_a_passive_skill_is_refused_by_the_platform(ds):
    passive = next(d.id for d in ds.of_type("SkillData") if d.value.get("type") == "passive")
    issues = story(ds, activeSkillIds=["laser", "shockwave", passive])
    assert codes(issues) == ["ROOM_PLACEMENT_SKILL_NOT_ACTIVE"]
    assert issues[0]["pointer"] == "/storyRooms/activeSkillIds/2"


def test_an_unknown_skill_is_a_reference_error(ds):
    assert codes(story(ds, activeSkillIds=["laser", "shockwave", "no_such_skill"])) == [
        "DATA_REFERENCE_MISSING"]


def test_a_theme_without_a_story_room_for_every_side(ds, monkeypatch):
    """Only a top-door story template: bottom, left and right are unserved."""
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    top_only = {"stageType": "story", "category": "", "openDoors": 1}
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others + [top_only])
    issues = story(ds)
    assert {i["code"] for i in issues} == {"LEVEL_STORY_TILEMAP_MISSING"}
    assert "right (2), bottom (4), left (8)" in issues[0]["message"]


def test_a_superset_template_serves_every_single_door(ds, monkeypatch):
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    four = {"stageType": "story", "category": "", "openDoors": 15}
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others + [four])
    assert story(ds) == []


def test_a_sample_counts_its_story_rooms():
    doc = {"floors": [{"rooms": [{"stageType": "start"}, {"stageType": "story"},
                                 {"stageType": "story"}]}]}
    assert generate.summarize(doc)["storyRooms"] == 2


def test_the_block_can_be_written_edited_and_taken_back(tmp_path):
    """`put` adds the block, a skill goes in and out one element at a time,
    and removing the block gives back the file byte for byte."""
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/levels",
                    root / "Assets/StreamingAssets/GameData/levels")
    api = Api(root)
    plan = next(d for d in api.dataset.of_type("LevelBasePlanData")
                if "storyRooms" not in d.value)
    before = plan.path.read_bytes()
    api.edit("LevelBasePlanData", plan.id, [{"op": "put", "path": "/storyRooms", "value":
             {"countMin": 2, "countMax": 3, "charges": 3, "activeSkillIds": []}}])
    api.edit("LevelBasePlanData", plan.id,
             [{"op": "append", "path": "/storyRooms/activeSkillIds", "value": "laser"},
              {"op": "put", "path": "/storyRooms/charges", "value": 5}])
    block = api.dataset.get("LevelBasePlanData", plan.id).value["storyRooms"]
    assert block == {"countMin": 2, "countMax": 3, "charges": 5, "activeSkillIds": ["laser"]}
    api.edit("LevelBasePlanData", plan.id, [{"op": "remove", "path": "/storyRooms"}])
    assert plan.path.read_bytes() == before
