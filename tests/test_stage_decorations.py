"""`FloorPlanData.stageDecorations` (OLE-43 / OZX-1291).

Dressing per stage: every generated non-cave room of the stage whose own
decorations come out empty gets the entry's list. chapter_3 keeps its
dressing in floorDefaults, so all three floors inherit it.
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
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def found(ds, code):
    return [i for i in validate_plan(ds, "chapter_3") if i["code"] == code]


def dressing(plan):
    return plan["floorDefaults"]["stageDecorations"]


@pytest.mark.parametrize("plan_id", ["chapter_3", "chapter_4"])
def test_the_shipped_dressing_is_clean(ds, plan_id):
    assert not [i for i in validate_plan(ds, plan_id)
                if "DECORATION" in i["code"] or "DECAL" in i["code"]]


def test_a_stage_dressed_twice_is_refused_once_at_the_default(ds, plan):
    dressing(plan).append({"stageType": "start", "decorations": []})
    bad = found(ds, "LEVEL_STAGE_DECORATION_DUPLICATE")
    assert len(bad) == 1 and bad[0]["pointer"].startswith("/floorDefaults/stageDecorations/")


def test_an_entry_without_a_stage_is_refused(ds, plan):
    dressing(plan).append({"decorations": [{"decalCategory": "ground_crack", "count": 1}]})
    assert found(ds, "LEVEL_STAGE_DECORATION_UNSCOPED")


def test_its_decals_face_the_room_rules(ds, plan):
    dressing(plan)[0]["decorations"].append({"decalCategory": "ground_moss", "count": 2})
    assert found(ds, "ROOM_DECAL_CATEGORY_UNKNOWN")


def test_its_decals_face_the_floors_theme(ds, plan):
    """A battlefield prop in a factory chapter's dressing draws nothing."""
    dressing(plan)[0]["decorations"].append({"decalCategory": "ground_crater", "count": 2})
    assert found(ds, "ROOM_DECAL_CATEGORY_NOT_IN_THEME")


def test_the_stage_row_says_how_it_is_dressed(ds):
    stages = ds.plan_view("chapter_3")["floors"][0]["stageTypes"]
    teaching = next(s for s in stages if s["stageType"] == "teaching")
    assert {"decalCategory": "ground_blood", "count": 3} in teaching["dressing"]
