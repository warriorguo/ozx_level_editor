"""The ally platform: staticPlacements kind "ally" (OLE-83 / OZX-1822).

`{ "kind": "ally", "allyId": "<AllyData id>", count | cells }` places a 2×2
AllyPlatform with that ally locked on it; standing still on it for a second
releases the ally. `AllyPlatformSpawnAdapter.RequireAlly` throws at room spawn
when the entry has no allyId or names no AllyData (GameData/allies/).
level_test's start room ships one (`medic`).
"""

import copy
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import habitats  # noqa: E402
from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.tilemaps import placement_footprint  # noqa: E402
from ozxlevel.validate import validate_all, validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

ROOM = "/floors/0/rooms/0"
ALLY = f"{ROOM}/staticPlacements/1"


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def entry(ds):
    return ds.get("LevelData", "level_test").value["floors"][0]["rooms"][0]["staticPlacements"][1]


def found(issues):
    return [(i["code"], i["pointer"]) for i in issues
            if i["code"] == "ROOM_PLACEMENT_ALLY_MISSING"
            or (i["code"] == "DATA_REFERENCE_MISSING" and "allyId" in (i.get("pointer") or ""))]


def test_the_shipped_ally_platform_is_clean(ds):
    assert entry(ds) == {"kind": "ally", "allyId": "medic", "count": 1}
    assert found(validate_all(ds)) == []


def test_an_ally_platform_needs_an_ally(ds):
    del entry(ds)["allyId"]
    assert found(validate_level(ds, "level_test")) == [("ROOM_PLACEMENT_ALLY_MISSING", ALLY)]
    entry(ds)["allyId"] = ""
    assert found(validate_level(ds, "level_test")) == [
        ("ROOM_PLACEMENT_ALLY_MISSING", f"{ALLY}/allyId")]


def test_the_ally_must_be_an_ally_data(ds):
    entry(ds)["allyId"] = "jammer"  # an EnemyData, not an AllyData
    assert found(validate_level(ds, "level_test")) == [
        ("DATA_REFERENCE_MISSING", f"{ALLY}/allyId")]


def test_it_is_two_by_two_and_not_environment():
    assert placement_footprint({"kind": "ally"}) == (2, 2)
    assert placement_footprint({"kind": "ally", "footprintCols": 3}) == (3, 2)
    assert "ally" in habitats.NON_ENVIRONMENT_KINDS


def test_the_room_view_and_the_library_show_it(ds):
    room = ds.level_view("level_test")["floors"][0]["rooms"][0]
    ally = room["staticPlacements"][1]
    assert ally["allyId"] == "medic" and ally["ally"] == {
        "missing": False, "displayName": "Medic", "flying": False}
    assert "ally" in {k["id"] for k in ds.static_catalog()}
    assert {a["id"] for a in ds.ally_catalog()} == {
        d.id for d in ds.of_type("AllyData")} >= {"medic"}
    entry(ds)["allyId"] = "nobody"
    room = ds.level_view("level_test")["floors"][0]["rooms"][0]
    assert room["staticPlacements"][1]["ally"]["missing"] is True


def test_the_field_round_trips(tmp_path):
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/levels",
                    root / "Assets/StreamingAssets/GameData/levels")
    api = Api(root)
    doc = api.dataset.get("LevelData", "level_test")
    before = doc.path.read_bytes()
    placements = copy.deepcopy(doc.value["floors"][0]["rooms"][0]["staticPlacements"])
    api.edit("LevelData", "level_test", [{"op": "put", "path": f"{ALLY}/allyId", "value": "warden"}])
    after = api.dataset.get("LevelData", "level_test").value["floors"][0]["rooms"][0]
    assert after["staticPlacements"][1]["allyId"] == "warden"
    assert after["staticPlacements"][0] == placements[0]
    api.edit("LevelData", "level_test", [{"op": "put", "path": f"{ALLY}/allyId", "value": "medic"}])
    assert doc.path.read_bytes() == before
