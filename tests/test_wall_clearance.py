"""`EnemyData.spawnMinWallDistance` / `wallRepelDistance` (OLE-39 / OZX-1263).

broodwing is born at least 3 from every room edge and veers off one closer
than 2. Placement admits only spots that clear the walls and clamps jitter
into the inner rect, which throws when there is none.
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


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def broodwing(ds):
    doc = ds.get("EnemyData", "broodwing")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def codes(ds):
    return [i["code"] for i in validate_enemy(ds, "broodwing")]


def test_the_shipped_flyer_is_clean(ds, broodwing):
    assert (broodwing["spawnMinWallDistance"], broodwing["wallRepelDistance"]) == (3.0, 2.0)
    assert not [c for c in codes(ds) if "WALL" in c]


@pytest.mark.parametrize("field", ["spawnMinWallDistance", "wallRepelDistance"])
def test_a_negative_distance_is_refused(ds, broodwing, field):
    broodwing[field] = -1
    assert "ENEMY_WALL_DISTANCE_NEGATIVE" in codes(ds)


def test_a_clearance_past_the_smallest_room_places_nothing_there(ds, broodwing):
    broodwing["spawnMinWallDistance"] = 4.9
    assert "ENEMY_WALL_CLEARANCE_UNPLACEABLE" not in codes(ds)
    broodwing["spawnMinWallDistance"] = 5
    assert "ENEMY_WALL_CLEARANCE_UNPLACEABLE" in codes(ds)


def test_the_enemy_row_says_it_keeps_off_the_walls(ds):
    inline = {"steps": [{"action": {"verb": "launch", "enemyId": "broodwing",
                                    "min": 1, "max": 1}}]}
    assert ds.encounter_enemies(None, inline=inline)[0]["wallClearance"] == 3.0
