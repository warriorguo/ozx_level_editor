"""Where a linked plan ends, and what every run walks through (OLE-69 / OZX-1518,
OLE-70 / OZX-1568).

OZX-1518: `finalArea` names the area the run ends in. Omitted (0 — area 0
always leads on in a linked plan), it is the one area no link leaves; with
several such dead ends it must be written, and the others become optional
side areas. Without `areaLinks` it may not be written. `AreaLinkPlan.FinalArea`
throws on each of these, from `FromData` and `LevelPlanValidator` alike.

OZX-1568: `upgradeStoryRooms { count }` hangs that many upgrade-platform story
rooms on every run, only in "must pass" areas — area 0, the end, and any area
whose removal from the link graph cuts 0 off from the end (keys ignored). At
least one of those has to be able to host a story room.
"""

import copy
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import arealinks, generate  # noqa: E402
from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

# AreaLinkGenerationTests.SideAreaFixture: A1 → M → A2, and M down to a
# basement side area S; the run ends in A2.
A1, M, A2, S = range(4)
SIDE_FIXTURE = {"areaLinks": [{"fromArea": A1, "toArea": M, "kind": "door"},
                              {"fromArea": M, "toArea": A2, "kind": "door"},
                              {"fromArea": M, "toArea": S, "kind": "stair"}],
                "finalArea": A2}


# ── the rules, on bare data ──────────────────────────────────────────────


def test_without_links_the_last_area_ends_the_run():
    assert arealinks.final_area({}, 3) == (2, None)
    assert arealinks.must_pass([], 3, 2) == [True, True, True]


def test_without_links_final_area_may_not_be_written():
    final, problem = arealinks.final_area({"finalArea": 1}, 3)
    assert problem[0] == "LEVEL_FINAL_AREA_WITHOUT_LINKS"


def test_omitted_it_is_the_one_dead_end():
    plan = {"areaLinks": [{"fromArea": 0, "toArea": 2}, {"fromArea": 2, "toArea": 1}]}
    assert arealinks.final_area(plan, 3) == (1, None)


def test_two_dead_ends_must_be_told_apart():
    plan = dict(SIDE_FIXTURE, finalArea=0)
    final, problem = arealinks.final_area(plan, 4)
    assert final is None and problem[0] == "LEVEL_FINAL_AREA_AMBIGUOUS"
    assert "[2, 3]" in problem[1]


def test_the_side_fixture_ends_in_a2_and_s_is_optional():
    assert arealinks.final_area(SIDE_FIXTURE, 4) == (A2, None)
    assert arealinks.must_pass(SIDE_FIXTURE["areaLinks"], 4, A2) == [True, True, True, False]


@pytest.mark.parametrize("final,code", [(M, "LEVEL_FINAL_AREA_LEADS_ON"),
                                        (7, "LEVEL_FINAL_AREA_OUT_OF_RANGE"),
                                        (-1, "LEVEL_FINAL_AREA_OUT_OF_RANGE")])
def test_a_named_end_must_be_a_dead_end_in_range(final, code):
    assert arealinks.final_area(dict(SIDE_FIXTURE, finalArea=final), 4)[1][0] == code


def test_an_area_on_a_parallel_route_is_optional_even_behind_a_key():
    # chapter_5: 0 → 1 keyed door, 0 → 2 stair, 2 → 1 stair. The key is
    # ignored — it can only make more areas compulsory.
    links = [{"fromArea": 0, "toArea": 1, "kind": "door", "keyId": "key_gold"},
             {"fromArea": 0, "toArea": 2, "kind": "stair"},
             {"fromArea": 2, "toArea": 1, "kind": "stair"}]
    assert arealinks.must_pass(links, 3, 1) == [True, True, False]


def test_a_sample_counts_its_upgrade_rooms_and_where_they_landed():
    upgrade = {"stageType": "story", "staticPlacements": [{"kind": "upgrade", "count": 1}]}
    skill = {"stageType": "story", "staticPlacements": [{"kind": "skill", "count": 1}]}
    summary = generate.summarize({"floors": [{"rooms": [skill]}, {"rooms": [upgrade, skill]}]})
    assert summary["storyRooms"] == 3
    assert summary["upgradeRooms"] == 1 and summary["upgradeAreas"] == [1]


# ── the shipped plans ────────────────────────────────────────────────────


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def codes(ds, plan_id, prefix=("LEVEL_FINAL_AREA", "LEVEL_UPGRADE", "LEVEL_ENDS_RUN",
                               "LEVEL_STORY_TILEMAP")):
    return sorted(i["code"] for i in validate_plan(ds, plan_id) if i["code"].startswith(prefix))


def plan(ds, plan_id):
    return ds.get("LevelBasePlanData", plan_id).value


@needs_data
@pytest.mark.parametrize("plan_id", ["chapter_3", "chapter_4", "chapter_5", "chapter_6"])
def test_the_shipped_plans_are_clean(ds, plan_id):
    assert codes(ds, plan_id) == []


@needs_data
def test_the_view_names_the_end_and_the_compulsory_areas(ds):
    ch4, ch5 = ds.plan_view("chapter_4")["areas"], ds.plan_view("chapter_5")["areas"]
    assert (ch4["final"], ch4["mustPass"], ch4["side"]) == (1, [True, True, True], [])
    assert (ch5["final"], ch5["mustPass"]) == (1, [True, True, False])
    assert ds.plan_view("chapter_4")["upgradeStoryRooms"] == {
        "declared": True, "active": True, "pointer": "/upgradeStoryRooms", "count": 1}


@needs_data
def test_a_basement_left_as_a_dead_end_asks_which_area_ends_the_run(ds):
    value = plan(ds, "chapter_5")
    value["areaLinks"] = [l for l in value["areaLinks"]
                          if not (l["fromArea"] == 2 and l["toArea"] == 1)]
    assert codes(ds, "chapter_5") == ["LEVEL_FINAL_AREA_AMBIGUOUS"]
    value["finalArea"] = 1
    assert codes(ds, "chapter_5") == []
    view = ds.plan_view("chapter_5")["areas"]
    assert (view["final"], view["side"], view["deadEnds"]) == (1, [2], [1, 2])


@needs_data
def test_a_side_area_does_not_end_the_run(ds):
    value = plan(ds, "chapter_5")
    value["areaLinks"] = [l for l in value["areaLinks"]
                          if not (l["fromArea"] == 2 and l["toArea"] == 1)]
    value["finalArea"] = 1
    value["floors"][2]["endsRunOnClear"] = True
    issue = next(i for i in validate_plan(ds, "chapter_5")
                 if i["code"] == "LEVEL_ENDS_RUN_NOT_FINAL_AREA")
    assert "optional side area" in issue["message"]


@needs_data
def test_a_chained_plan_may_not_name_an_end(ds):
    plan(ds, "chapter_3")["finalArea"] = 1
    assert codes(ds, "chapter_3") == ["LEVEL_FINAL_AREA_WITHOUT_LINKS"]


@needs_data
def test_a_negative_upgrade_count_throws(ds):
    plan(ds, "chapter_4")["upgradeStoryRooms"] = {"count": -1}
    assert codes(ds, "chapter_4") == ["LEVEL_UPGRADE_STORY_ROOMS_COUNT_INVALID"]


@needs_data
def test_a_zero_upgrade_count_silently_declares_none(ds):
    plan(ds, "chapter_4")["upgradeStoryRooms"] = {"count": 0}
    issues = [i for i in validate_plan(ds, "chapter_4")
              if i["code"] == "LEVEL_UPGRADE_STORY_ROOMS_UNDECLARED"]
    assert [i["severity"] for i in issues] == ["warning"]


@needs_data
def test_an_upgrade_room_needs_a_compulsory_area_that_can_host_it(ds):
    # chapter_5's basement (area 2) can host a story room, but a run can
    # skip it through the keyed door, so it does not count.
    value = plan(ds, "chapter_5")
    value["upgradeStoryRooms"] = {"count": 1}
    assert codes(ds, "chapter_5") == []
    for f in (0, 1):
        value["floors"][f]["requireFullGround"] = True
    issue = next(i for i in validate_plan(ds, "chapter_5")
                 if i["code"] == "LEVEL_UPGRADE_STORY_ROOMS_NO_HOST")
    assert "[0, 1]" in issue["message"] and issue["pointer"] == "/upgradeStoryRooms"


@needs_data
def test_upgrade_rooms_alone_still_need_story_tilemaps(ds, monkeypatch):
    value = plan(ds, "chapter_4")
    value.pop("storyRooms")
    others = [dict(t) for t in ds.tilemaps.facts() if t["stageType"] != "story"]
    monkeypatch.setattr(ds.tilemaps, "facts", lambda: others)
    found = [i for i in validate_plan(ds, "chapter_4") if i["code"] == "LEVEL_STORY_TILEMAP_MISSING"]
    assert found and all(i["message"].startswith("upgradeStoryRooms declared") for i in found)


@needs_data
def test_the_new_fields_can_be_written_and_taken_back(tmp_path):
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/levels",
                    root / "Assets/StreamingAssets/GameData/levels")
    api = Api(root)
    doc = api.dataset.get("LevelBasePlanData", "chapter_5")
    before = doc.path.read_bytes()
    links = copy.deepcopy(doc.value["areaLinks"])
    api.edit("LevelBasePlanData", "chapter_5",
             [{"op": "put", "path": "/upgradeStoryRooms", "value": {"count": 1}}])
    api.edit("LevelBasePlanData", "chapter_5",
             [{"op": "put", "path": "/upgradeStoryRooms/count", "value": 2}])
    api.edit("LevelBasePlanData", "chapter_5",
             [{"op": "put", "path": "/finalArea", "value": 1}])
    value = api.dataset.get("LevelBasePlanData", "chapter_5").value
    assert value["upgradeStoryRooms"] == {"count": 2} and value["finalArea"] == 1
    assert value["areaLinks"] == links
    for path in ("/finalArea", "/upgradeStoryRooms"):
        api.edit("LevelBasePlanData", "chapter_5", [{"op": "remove", "path": path}])
    assert doc.path.read_bytes() == before
