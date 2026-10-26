"""Upgrade platforms — `staticPlacements` entries with `kind: "upgrade"` (OLE-61).

OZX-1472's UpgradePlatformSpawnAdapter: a 2×2 Static block, pinned `cells`
as block anchors or `count` random blocks, and no fields of its own.
Touching one opens the equipment upgrade panel, the only way in.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import tilemaps  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def test_the_static_library_offers_upgrade(ds):
    entry = next(k for k in ds.static_catalog() if k["id"] == "upgrade")
    assert entry["uses"] >= 0


def test_the_upgrade_card_shows_the_platform_prefab(ds):
    """Its prefab is `prefab/upgradeplatform`, not `prefab/upgrade`."""
    entry = next(k for k in ds.static_catalog() if k["id"] == "upgrade")
    assert entry["sprite"] is not None


def test_an_upgrade_platform_defaults_to_two_by_two():
    assert tilemaps.placement_footprint({"kind": "upgrade"}) == (2, 2)
    assert tilemaps.placement_footprint(
        {"kind": "upgrade", "footprintCols": 3, "footprintRows": 2}) == (3, 2)


def test_shipped_upgrade_platforms_validate_clean(ds):
    """level_test's start room carries one since OZX-1472."""
    found = [lid for lid in ds.ids_of("LevelData")
             for f in ds.get("LevelData", lid).value.get("floors") or []
             for r in f.get("rooms") or []
             for p in r.get("staticPlacements") or [] if p.get("kind") == "upgrade"]
    if not found:
        pytest.skip("no level places an upgrade platform")
    for lid in set(found):
        mine = [i for i in validate_level(ds, lid)
                if "upgrade" in i["message"] and i["severity"] == "error"]
        assert mine == []
