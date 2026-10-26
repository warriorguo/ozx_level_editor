"""Cargo crates: each entry's own table, and where it falls back to (OLE-50 / OZX-1348).

Every crate rolls its own entry's `lootTableId`, then the room's
`floorLootTableId`, and `CargoSpawnAdapter` throws with neither. Several
count-mode entries per room are fine now, each with its own mount.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level, validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def restoring(ds, data_type, entity_id):
    doc = ds.get(data_type, entity_id)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


@pytest.fixture
def level(ds):
    yield from restoring(ds, "LevelData", "chapter_1")


@pytest.fixture
def plan(ds):
    yield from restoring(ds, "LevelBasePlanData", "chapter_3")


def room(level):
    return level["floors"][0]["rooms"][1]


def codes(ds):
    return [i["code"] for i in validate_level(ds, "chapter_1")]


def test_several_count_entries_each_with_a_table_and_a_mount_are_fine(ds, level):
    room(level).setdefault("staticPlacements", []).extend([
        {"kind": "cargo", "count": 2, "lootTableId": "loot_common",
         "mountedEnemyId": "tentacle_short"},
        {"kind": "cargo", "count": 3, "lootTableId": "loot_common",
         "mountedEnemyId": "tentacle_short", "mountedCount": 1}])
    assert not [c for c in codes(ds) if "CARGO" in c]


def test_a_hand_written_crate_with_no_table_has_nothing_to_roll(ds, level):
    room(level).setdefault("staticPlacements", []).append({"kind": "cargo", "count": 2})
    assert "ROOM_CARGO_LOOT_SOURCE_MISSING" in codes(ds)


def test_a_generated_room_falls_back_to_the_floor_table(ds, level):
    r = room(level)
    r.setdefault("staticPlacements", []).append({"kind": "cargo", "count": 2})
    r["floorLootTableId"] = "loot_floor_f0"
    assert "ROOM_CARGO_LOOT_SOURCE_MISSING" not in codes(ds)


def test_a_crate_is_one_cell(ds, level):
    room(level).setdefault("staticPlacements", []).append(
        {"kind": "cargo", "count": 1, "lootTableId": "loot_common", "footprintCols": 2})
    assert "ROOM_CARGO_FOOTPRINT_INVALID" in codes(ds)


def test_a_plan_crate_needs_probabilistic_loot_or_its_own_table(ds, plan):
    floor = plan["floors"][0]
    floor["defaultStaticPlacements"].append({"kind": "cargo", "count": 2})
    assert "ROOM_CARGO_LOOT_SOURCE_MISSING" not in [
        i["code"] for i in validate_plan(ds, "chapter_3")]
    floor["floorLootPlan"].pop("probabilistic", None)
    floor["floorLootPlan"].pop("probabilisticPresets", None)
    assert "ROOM_CARGO_LOOT_SOURCE_MISSING" in [
        i["code"] for i in validate_plan(ds, "chapter_3")]


# ── OZX-1468: cargoId names the crate (OLE-60) ───────────────────────────

PREMIUM = {"kind": "cargo", "cargoId": "cargo_premium",
           "lootTableId": "loot_cargo_premium", "count": 1}


def test_a_named_crate_is_fine(ds, level):
    room(level).setdefault("staticPlacements", []).append(dict(PREMIUM))
    found = codes(ds)
    assert "ROOM_PLACEMENT_CARGO_ID_MISPLACED" not in found
    assert not [i for i in validate_level(ds, "chapter_1") if i["pointer"].endswith("/cargoId")]


def test_a_crate_no_cargo_data_has_throws(ds, level):
    room(level).setdefault("staticPlacements", []).append(dict(PREMIUM, cargoId="cargo_nope"))
    found = [i for i in validate_level(ds, "chapter_1") if i["pointer"].endswith("/cargoId")]
    assert [i["code"] for i in found] == ["DATA_REFERENCE_MISSING"]


def test_cargo_id_on_another_kind_throws(ds, level):
    room(level).setdefault("staticPlacements", []).append(
        {"kind": "oiltank", "count": 1, "cargoId": "cargo_premium"})
    assert "ROOM_PLACEMENT_CARGO_ID_MISPLACED" in codes(ds)


def test_a_plan_default_is_checked_the_same_way(ds, plan):
    plan["floors"][0]["defaultStaticPlacements"].append(
        {"kind": "prop", "prefabKey": "prefab/groundbomb", "count": 1, "cargoId": "cargo_premium"})
    assert "ROOM_PLACEMENT_CARGO_ID_MISPLACED" in [i["code"] for i in validate_plan(ds, "chapter_3")]


def test_the_view_and_the_catalog_carry_it(ds, level):
    room(level).setdefault("staticPlacements", []).append(dict(PREMIUM))
    view = ds.level_view("chapter_1")
    placed = view["floors"][0]["rooms"][1]["staticPlacements"][-1]
    assert placed["cargoId"] == "cargo_premium"
    catalog = {c["id"]: c for c in ds.cargo_catalog()}
    assert catalog["cargo_premium"]["authoredOnly"] is True
    assert catalog["cargo_box_2"]["authoredOnly"] is False
