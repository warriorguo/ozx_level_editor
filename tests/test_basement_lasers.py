"""No ground laser on a basement floor (OLE-87 / OZX-1919).

`StaticPlacementPolicy` now judges by the floor's theme as well as the
room's stage: a `themes/basement` floor takes no `groundlaser` (ground bombs
stay). As with the stage bans, a floor default is filtered silently — it
names no room — while a room that authors one makes the level refuse to load
(`LevelValidator.ValidateStaticPlacementPolicy`). The generator judges a plan
floor by `GeneratedThemeIdOf`, since `floor.themeId` is stamped later.

The rest of OZX-1919 / OZX-1920 (bombs never on Rail cells, a WaterPool
whose rim touches rail retrying and then giving up) is placement inside a
room's tilemap — genlevel and the game, not data the editor can check.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

BANS = {"ROOM_PLACEMENT_THEME_BANNED", "ROOM_PLACEMENT_STAGE_BANNED"}
# level_test area 3 is a themes/basement area of two plain rooms.
BASEMENT_ROOM = "/floors/3/rooms/0"


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def basement_room(ds):
    return ds.get("LevelData", "level_test").value["floors"][3]["rooms"][0]


def bans(ds):
    return [(i["code"], i["pointer"]) for i in validate_level(ds, "level_test")
            if i["code"] in BANS]


@needs_data
def test_a_basement_room_may_not_author_a_laser(ds):
    placements = basement_room(ds)["staticPlacements"]
    placements.append({"kind": "groundlaser", "count": 2})
    assert bans(ds) == [("ROOM_PLACEMENT_THEME_BANNED",
                         f"{BASEMENT_ROOM}/staticPlacements/{len(placements) - 1}")]


@needs_data
def test_ground_bombs_stay_allowed_there(ds):
    basement_room(ds)["staticPlacements"].append(
        {"kind": "prop", "prefabKey": "prefab/groundbomb", "count": 3})
    assert bans(ds) == []


@needs_data
def test_the_stage_reason_comes_first(ds):
    room = basement_room(ds)
    room["stageType"] = "release"
    room["staticPlacements"].append({"kind": "groundlaser", "count": 2})
    assert [c for c, _ in bans(ds)] == ["ROOM_PLACEMENT_STAGE_BANNED"]


@needs_data
def test_a_laser_above_ground_is_fine(ds):
    room = ds.get("LevelData", "level_test").value["floors"][1]["rooms"][1]
    room["staticPlacements"] = list(room.get("staticPlacements") or []) + [
        {"kind": "groundlaser", "count": 2}]
    assert bans(ds) == []


@needs_data
def test_the_plan_view_says_the_default_is_dropped(ds):
    floors = ds.plan_view("chapter_4")["floors"]
    lasers = [[p["skipsFloor"] for p in f["defaultStaticPlacements"] if p["kind"] == "groundlaser"]
              for f in floors]
    assert lasers == [[False], [False], [True]]
