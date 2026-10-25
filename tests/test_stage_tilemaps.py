"""Every stage an area deals needs a tilemap (OLE-46: OZX-1340, plus the
long-standing `ValidateStageTilemaps`).

`RoomTilemapQuery` throws when a room's stage has nothing in its area's
library, as the player walks in. An area with `requireFullGround` needs a
full-ground four-door template per stage, because that is the superset its
fallback lands on. chapter_6's battlefield areas are the shipped users.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import tilemaps  # noqa: E402
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
    doc = ds.get("LevelBasePlanData", "chapter_6")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def found(ds, code, plan_id="chapter_6"):
    return [i for i in validate_plan(ds, plan_id) if i["code"] == code]


def test_every_shipped_plan_has_its_tilemaps(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        assert not [i for i in validate_plan(ds, doc.id) if "TILEMAP" in i["code"]], doc.id


def test_the_library_follows_the_theme(ds):
    facts = ds.tilemaps.facts()
    basement = tilemaps.for_theme(facts, "themes/basement")
    assert basement and all(t["category"] in ("", "basement") for t in basement)
    # A theme no tilemap claims draws the ordinary library.
    assert tilemaps.for_theme(facts, "themes/factory") == tilemaps.for_theme(facts, None)


def test_a_stage_with_no_tilemap_throws_on_entry(ds, plan):
    plan["floors"][1]["stageTypes"].append({"stageType": "exit", "portion": 0.1})
    bad = found(ds, "LEVEL_STAGE_TILEMAP_MISSING")
    assert bad and bad[0]["pointer"].startswith("/floors/1/stageTypes/")


def test_the_overflow_stage_needs_one_too(ds, plan):
    plan["floors"][1]["overflowStageType"] = "exit"
    assert found(ds, "LEVEL_STAGE_TILEMAP_MISSING")


def test_a_full_ground_area_needs_a_full_four_door_template_per_stage(ds, plan):
    """`story` has a template, but no full-ground four-door one."""
    area = next(f for f in plan["floors"] if f.get("requireFullGround"))
    area["stageTypes"].append({"stageType": "story", "portion": 0.1})
    assert not [i for i in found(ds, "LEVEL_STAGE_TILEMAP_MISSING") if "story" in i["message"]]
    bad = found(ds, "LEVEL_FULL_GROUND_TILEMAP_MISSING")
    assert bad and "story" in bad[0]["message"]


def test_a_changed_tilemap_is_reread(ds, tmp_path):
    root = tmp_path / "TilemapData" / "normal"
    root.mkdir(parents=True)
    path = root / "all_x_15_01.json"
    path.write_text('{"ground": [[1]], "stageType": "x", "openDoors": 15}')
    index = tilemaps.TilemapIndex(tmp_path / "TilemapData")
    assert index.facts()[0]["hasEmptyGround"] is False
    path.write_text('{"ground": [[0, 1]], "stageType": "x", "openDoors": 15}')
    assert index.facts()[0]["hasEmptyGround"] is True
    path.unlink()
    assert index.facts() == []
