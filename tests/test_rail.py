"""Rail carts and rail floors (OLE-32 / OLE-33).

OZX-1159 turned the ride from a steam train into a rail cart and renamed
`RoomNodeData.steamTrain` to `railCart`. OZX-1164 then added the floor-wide
version: `FloorPlanData.railCart` lays rail through every room of an area
(`RoomNodeData.requireRail`, stamped per room by the assigner) and parks a
cart in its start room.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def level(ds):
    doc = ds.get("LevelData", "level_test")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def first_room(level):
    return level["floors"][0]["rooms"][0]


def found(ds, code):
    return [i for i in validate_level(ds, "level_test") if i["code"] == code]


def test_the_old_name_is_refused_not_read(ds, level):
    first_room(level)["steamTrain"] = True
    bad = found(ds, "LEVEL_ROOM_FIELD_RENAMED")
    assert bad and bad[0]["pointer"].endswith("/steamTrain")


def test_the_new_name_is_not_a_finding(ds):
    assert not found(ds, "LEVEL_ROOM_FIELD_RENAMED")
