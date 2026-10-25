"""Pinned drops — a room's `guaranteedDrops`, `guaranteedCargo`, and the
launch pin they replaced.

OZX-1041 split one string into two fields. `EncounterAction.dropTableIdOverride`
used to mean both "what this group rolls" and "what is pinned here", and the
two kept overwriting each other: pinning a prize replaced the launch's table,
which handed the prize to EVERY enemy the launch spawned (one key room, four
keys) and cost the carriers the loot they would otherwise have dropped.

What that leaves for this side:

- the action's table field is `dropTableId`. The old name is not an alias —
  JsonUtility discards an undeclared field in silence, so a file still
  carrying it falls back to the enemy's own table with no error at all.
- a pin is paid ON TOP of the table, to the first `count` enemies.
- keys are pins now. `loot_story_f{n}_0` is not synthesised any more, so
  anything that finds keys by looking inside a table finds none.
- a pin names an item OR a `pickOne` table (OZX-1047), never both.

The pin object is materialised by JsonUtility whether or not it was authored,
so "is there a pin" is never `guaranteedDrop != null` — it is "does it name
something to drop". That is the shape most of these tests are about.

OZX-1380 then moved the pin off the launch onto the ROOM: `guaranteedDrops`,
paid by the last-killed body of the opening wave, which is the one batch a
player cannot walk away from. A launch pin is now a removed field; the shape
rules below are the room's.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_encounter, validate_level, validate_loot  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def codes(ds, steps):
    """Validate a throwaway encounter built from `steps`."""
    doc = ds.get("EncounterData", "spawn_ch1_teach1_01")
    before = doc.value.get("steps")
    doc.value["steps"] = steps
    try:
        return [i["code"] for i in validate_encounter(ds, doc.id)]
    finally:
        doc.value["steps"] = before


def launch(**action):
    action.setdefault("verb", "launch")
    action.setdefault("enemyId", "small_ground_patrol")
    action.setdefault("min", 1)
    action.setdefault("max", 1)
    return [{"action": action}]


# ── the rename ───────────────────────────────────────────────────────────


def test_the_action_table_is_read_from_the_new_name(ds):
    drop = ds.wave_drop({"enemyId": "blight_sac_stalker",
                         "dropTableId": "loot_ch1_build3_skill"})
    assert drop["source"] == "override"
    assert drop["tableId"] == "loot_ch1_build3_skill"


def test_the_old_name_is_reported_rather_than_honoured(ds):
    """A silent fallback is the whole hazard: the field still reads as set."""
    drop = ds.wave_drop({"enemyId": "blight_sac_stalker",
                         "dropTableIdOverride": "loot_ch1_build3_skill"})
    assert drop["source"] == "enemy", "the stale name must not resolve a table"
    assert "ENCOUNTER_DROP_FIELD_RENAMED" in codes(
        ds, launch(dropTableIdOverride="loot_ch1_build3_skill"))


def test_no_shipped_encounter_still_carries_the_old_name(ds):
    stale = [d.id for d in ds.of_type("EncounterData")
             for s in d.value.get("steps") or []
             if "dropTableIdOverride" in (s.get("action") or {})]
    assert not stale, stale


# ── a pin exists iff it names something ──────────────────────────────────


def test_an_unauthored_pin_object_is_not_a_pin(ds):
    """JsonUtility materialises the field; `!= null` proves nothing."""
    assert ds.wave_pin({"guaranteedDrop": {}}) is None
    assert ds.wave_pin({"guaranteedDrop": {"itemId": "", "count": 0}}) is None
    assert ds.wave_drop({"enemyId": "small_ground_patrol",
                         "guaranteedDrop": {}})["pin"] is None
    assert codes(ds, launch(guaranteedDrop={})) == codes(ds, launch())


def room_codes(ds, *pins, encounter=None):
    """Validate chapter_1's key room with `pins` as its guaranteedDrops."""
    doc = ds.get("LevelData", "chapter_1")
    room = next(r for f in doc.value["floors"] for r in f["rooms"] if r.get("guaranteedDrops"))
    before = (list(room["guaranteedDrops"]), room.get("encounterId"))
    room["guaranteedDrops"] = list(pins)
    if encounter is not None:
        room["encounterId"] = encounter
    try:
        return [i["code"] for i in validate_level(ds, "chapter_1")
                if i["code"].startswith(("ROOM_PIN", "LOOT_TABLE"))]
    finally:
        room["guaranteedDrops"], room["encounterId"] = before


def test_a_count_with_nothing_to_drop_is_an_error(ds):
    assert "ROOM_PIN_EMPTY" in room_codes(ds, {"count": 2})


# ── what a pin resolves to ───────────────────────────────────────────────


def test_an_item_pin_resolves_the_item(ds):
    pin = ds.wave_pin({"guaranteedDrop": {"itemId": "key_silver", "count": 1}})
    assert pin["itemId"] == "key_silver"
    assert pin["missing"] is False
    assert pin["itemType"] == "key"
    assert pin["count"] == 1


def test_a_table_pin_carries_the_roster_it_draws_from(ds):
    """The count is the pin's; the identity is the table's. Naming one member
    of the family would be a lie, so the row names the family."""
    pin = ds.wave_pin({"guaranteedDrop": {"tableId": "loot_equipment_f0",
                                          "count": 1}})
    assert pin["tableId"] == "loot_equipment_f0"
    assert pin["missing"] is False
    assert pin["itemType"] is None
    # loot_equipment_f0 is half table-valued itself, so a one-level read would
    # report two of these four and miss the cores and drives entirely.
    assert len(pin["drawsFrom"]) > 4
    nested = next(e["tableId"] for e in ds.loot_table_view("loot_equipment_f0")["entries"]
                  if e["tableId"])
    assert set(ds.table_items(nested)) <= set(pin["drawsFrom"])


def test_an_unauthored_chance_is_always_not_never(ds):
    """0 means the field was never written. Rendering it as 0% would read as
    a pin that can never fire — the opposite of what it means."""
    always = ds.wave_pin({"guaranteedDrop": {"itemId": "key_silver", "count": 1}})
    assert always["chance"] == 0
    assert always["chancePercent"] == 100.0

    rare = ds.wave_pin({"guaranteedDrop": {"itemId": "key_silver", "count": 1,
                                           "chance": 0.07}})
    assert rare["chancePercent"] == 7.0


def test_a_pin_is_paid_on_top_of_the_table_not_instead_of_it(ds):
    drop = ds.wave_drop({"enemyId": "blight_sac_stalker",
                         "dropTableId": "loot_ch1_build3_skill",
                         "guaranteedDrop": {"itemId": "key_silver", "count": 1}})
    assert drop["tableId"] == "loot_ch1_build3_skill"
    assert drop["items"], "the table still rolls"
    assert drop["pin"]["itemId"] == "key_silver"


def test_a_pinned_skill_counts_as_a_skill_on_the_wave(ds):
    """The find-me flag exists because nothing else in the app shows this
    channel. A pin is exactly as invisible as a table entry."""
    skill = next(d.id for d in ds.of_type("ItemData")
                 if d.value.get("type") == "skill")
    drop = ds.wave_drop({"enemyId": "blight_sac_stalker",
                         "guaranteedDrop": {"itemId": skill, "count": 1}})
    assert drop["hasSkill"] is True


# ── the rules, moved forward from EncounterValidator ─────────────────────


def test_the_shipped_key_rooms_are_clean(ds):
    """ch1 / ch2 / the test levels carry key_gold in guaranteedDrops."""
    for level_id in ("chapter_1", "chapter_2", "level_test", "factory2_test"):
        assert not [i for i in validate_level(ds, level_id)
                    if i["code"].startswith("ROOM_PIN")], level_id


def test_a_pin_on_a_launch_is_a_removed_field(ds):
    """OZX-1380: JsonUtility drops it, so the prize would pay nothing."""
    assert "ENCOUNTER_PIN_REMOVED" in codes(ds, launch(
        guaranteedDrop={"itemId": "key_silver", "count": 1}))


def test_a_pin_naming_both_an_item_and_a_table_is_refused(ds):
    assert "ROOM_PIN_AMBIGUOUS" in room_codes(
        ds, {"itemId": "key_silver", "tableId": "loot_equipment_f0", "count": 1})


def test_a_pin_whose_item_is_really_a_table_is_caught(ds):
    """The failure it prevents: the pin drops nothing, silently."""
    assert "ROOM_PIN_ITEM_MISSING" in room_codes(ds, {"itemId": "loot_equipment_f0", "count": 1})


def test_a_pin_may_only_draw_from_a_pickone_table(ds):
    """An ordinary table rolls each entry independently, so it yields anywhere
    from nothing to everything and the count stops meaning anything."""
    ordinary = next(d.id for d in ds.of_type("LootTableData")
                    if not d.value.get("pickOne") and d.value.get("entries"))
    assert "ROOM_PIN_TABLE_NOT_PICKONE" in room_codes(ds, {"tableId": ordinary, "count": 1})
    assert "LOOT_TABLE_MISSING" in room_codes(ds, {"tableId": "no_such_table", "count": 1})


def test_a_pin_that_pays_no_copy_is_refused(ds):
    assert "ROOM_PIN_COUNT_INVALID" in room_codes(ds, {"itemId": "key_silver", "count": 0})


def test_a_chance_outside_the_open_unit_range_is_refused(ds):
    assert "ROOM_PIN_CHANCE_INVALID" in room_codes(
        ds, {"itemId": "key_silver", "count": 1, "chance": 1.5})
    assert "ROOM_PIN_CHANCE_INVALID" not in room_codes(
        ds, {"itemId": "key_silver", "count": 1, "chance": 1.0})


def test_a_room_with_no_opening_body_cannot_carry_a_pin(ds):
    """No fallback channel: the last kill of the opening wave pays, and an
    encounter whose first step waits on something has no opening wave."""
    gated = next(d.id for d in ds.of_type("EncounterData")
                 if (d.value.get("steps") or [{}])[0].get("condition"))
    assert "ROOM_PIN_NO_CARRIER" in room_codes(
        ds, {"itemId": "key_silver", "count": 1}, encounter=gated)
    assert "ROOM_PIN_NO_CARRIER" in room_codes(
        ds, {"itemId": "key_silver", "count": 1}, encounter="")


def test_the_room_view_carries_what_it_owes(ds):
    rooms = [r for f in ds.level_view("chapter_1")["floors"] for r in f["rooms"]]
    owed = [p for r in rooms for p in r["guaranteedDrops"]]
    assert any(p["itemId"] == "key_gold" for p in owed)
    assert all(p["pointer"].startswith("/floors/") for p in owed)


# ── table-valued loot entries (OZX-1047), which a pin's table is full of ──


def test_a_table_valued_entry_is_not_a_missing_item(ds):
    """loot_equipment_f0 draws its cores and drives from nested tables. Read
    as items they are three dangling entries per floor."""
    assert not [i for i in validate_loot(ds, "loot_equipment_f0")
                if i["code"] == "LOOT_ITEM_MISSING"]
    view = ds.loot_table_view("loot_equipment_f0")
    nested = [e for e in view["entries"] if e["tableId"]]
    assert nested
    assert all(not e["missing"] for e in nested)
    assert all(e["drawsFrom"] for e in nested)


def test_a_nested_table_must_be_a_usable_pickone(ds):
    doc = ds.get("LootTableData", "loot_equipment_f0")
    before = doc.value["entries"]
    ordinary = next(d.id for d in ds.of_type("LootTableData")
                    if not d.value.get("pickOne") and d.value.get("entries"))
    try:
        doc.value["entries"] = [{"tableId": ordinary, "weight": 1,
                                 "minCount": 1, "maxCount": 1}]
        assert "LOOT_ENTRY_TABLE_NOT_PICKONE" in [
            i["code"] for i in validate_loot(ds, doc.id)]

        doc.value["entries"] = [{"tableId": doc.id, "weight": 1,
                                 "minCount": 1, "maxCount": 1}]
        assert "LOOT_TABLE_CYCLE" in [i["code"] for i in validate_loot(ds, doc.id)]
        # And the flattener must not spin on it.
        assert ds.table_items(doc.id) == []

        doc.value["entries"] = [{"itemId": "key_silver", "tableId": ordinary,
                                 "weight": 1, "minCount": 1, "maxCount": 1}]
        assert "LOOT_ENTRY_AMBIGUOUS" in [
            i["code"] for i in validate_loot(ds, doc.id)]
    finally:
        doc.value["entries"] = before
