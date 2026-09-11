"""Loot resolution and validation.

The load-bearing distinction here is what a `weight` means. With
`pickOne: false` — 24 of the 25 tables — each entry rolls independently and
its weight IS a 0-100 percentage. With `pickOne: true` exactly one entry is
chosen and the weight is a relative share. Normalising the first kind, or
treating the second as percentages, both give wrong numbers that look
plausible, so this is pinned.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_loot  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


# ── weight semantics ─────────────────────────────────────────────────────


def test_independent_weights_are_percentages_not_shares(ds):
    """loot_boss: weights 100/80/100. They must NOT be normalised to sum 100."""
    view = ds.loot_table_view("loot_boss")
    assert view["pickOne"] is False
    chances = {e["itemId"]: e["chance"] for e in view["entries"]}
    assert chances == {"item_exp_large": 100.0,
                       "item_health_large": 80.0,
                       "key_gold": 100.0}
    assert sum(chances.values()) > 100, "independent weights may exceed 100 in total"


def test_pick_one_weights_are_normalised_shares(ds):
    view = ds.loot_table_view("loot_ch1_build3_skill")
    assert view["pickOne"] is True
    total = sum(e["chance"] for e in view["entries"])
    assert round(total) == 100, "a pickOne table always drops exactly one entry"


def test_missing_table_is_reported_not_silently_empty(ds):
    view = ds.loot_table_view("does_not_exist")
    assert view["missing"] is True
    assert view["entries"] == []


def test_none_resolves_to_none(ds):
    assert ds.loot_table_view(None) is None


# ── the two room channels ────────────────────────────────────────────────


def test_room_clear_and_cargo_loot_are_separate_channels(ds):
    """They roll at different moments, so they are never merged into one list."""
    view = ds.level_view("chapter_1")
    rooms = {r["roomId"]: r for r in view["floors"][0]["rooms"]}

    # f0_room_1_4 has both: a room-clear plan AND a cargo box.
    both = rooms["f0_room_1_4"]
    assert both["roomClearLoot"]["id"] == "loot_ch1_key"
    assert [c["table"]["id"] for c in both["cargoLoot"]] == ["loot_ch1_laser"]

    # f0_room_0_0 has cargo only, and no room-clear plan.
    cargo_only = rooms["f0_room_0_0"]
    assert cargo_only["roomClearLoot"] is None
    assert cargo_only["cargoLoot"]


def test_cargo_loot_carries_the_pointer_its_picker_writes_to(ds):
    view = ds.level_view("chapter_1")
    room = view["floors"][0]["rooms"][0]
    entry = room["cargoLoot"][0]
    assert entry["pointer"] == (
        f"/floors/0/rooms/0/staticPlacements/{entry['placementIndex']}/lootTableId")


# ── validation ───────────────────────────────────────────────────────────


def test_empty_tables_referenced_by_cargo_are_flagged(ds):
    """chapter_1 points nine cargo boxes at three tables that drop nothing."""
    for table_id in ("loot_ch1_supply", "loot_ch1_shotgun", "loot_ch1_laser"):
        codes = [i["code"] for i in validate_loot(ds, table_id)]
        assert "LOOT_TABLE_EMPTY" in codes, table_id


def test_every_production_loot_item_exists(ds):
    """No dangling itemId anywhere in the loot tables."""
    dangling = [i for doc in ds.of_type("LootTableData")
                for i in validate_loot(ds, doc.id)
                if i["code"] == "LOOT_ITEM_MISSING"]
    assert not dangling, dangling


def test_count_ranges_are_valid_across_production(ds):
    bad = [i for doc in ds.of_type("LootTableData")
           for i in validate_loot(ds, doc.id)
           if i["code"] == "LOOT_COUNT_RANGE_INVALID"]
    assert not bad, bad


def test_independent_weight_outside_0_100_is_an_error():
    """A weight of 250 is meaningless when the weight is a percentage."""
    class FakeDoc:
        value = {"entries": [{"itemId": "x", "weight": 250,
                              "minCount": 1, "maxCount": 1}]}

    class FakeDs:
        def get(self, data_type, id_):
            return FakeDoc() if data_type == "LootTableData" else object()

    codes = [i["code"] for i in validate_loot(FakeDs(), "t")]
    assert "LOOT_WEIGHT_INVALID" in codes


def test_pick_one_allows_weights_above_100():
    """On a pickOne table the weight is a share, so 250 is perfectly legal."""
    class FakeDoc:
        value = {"pickOne": True,
                 "entries": [{"itemId": "x", "weight": 250, "minCount": 1, "maxCount": 1},
                             {"itemId": "y", "weight": 750, "minCount": 1, "maxCount": 1}]}

    class FakeDs:
        def get(self, data_type, id_):
            return FakeDoc() if data_type == "LootTableData" else object()

    assert [i["code"] for i in validate_loot(FakeDs(), "t")] == []


# ── item resolution ──────────────────────────────────────────────────────


def test_item_brief_resolves_by_id_not_filename(ds):
    """`item_exp_large` is stored in `exp_large.json`; the id is the key."""
    brief = ds.item_brief("item_exp_large")
    assert brief["missing"] is False
    assert brief["displayName"] == "Large Exp Crystal"
    assert brief["itemType"] == "exp"


def test_item_brief_reports_unknown_ids(ds):
    brief = ds.item_brief("no_such_item")
    assert brief["missing"] is True
    assert brief["displayName"] is None
    assert ds.item_brief(None)["missing"] is True


def test_loot_entries_carry_the_resolved_item(ds):
    view = ds.loot_table_view("loot_ch1_build3_skill")
    by_id = {e["itemId"]: e for e in view["entries"]}
    crimson = by_id["crimson_barrage"]
    assert crimson["displayName"] == "Crimson Barrage"
    assert crimson["itemType"] == "skill"
    assert crimson["rarity"] == "uncommon"


def test_rarity_palette_comes_from_the_game_not_a_guess(ds):
    """box_rarity.json has uncommon BLUE and rare GREEN, inverting the usual
    convention — so the palette must be read, never assumed."""
    palette = ds.rarity_palette()
    assert palette["uncommon"] == "#4E8FE0"   # blue
    assert palette["rare"] == "#4EC94E"       # green
    assert set(palette) >= {"common", "uncommon", "rare", "epic", "legendary"}


def test_every_rarity_used_by_items_has_a_colour(ds):
    palette = ds.rarity_palette()
    used = {d.value.get("rarity") for d in ds.of_type("ItemData") if d.value.get("rarity")}
    assert used <= set(palette), f"no colour for {used - set(palette)}"
