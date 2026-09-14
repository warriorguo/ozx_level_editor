"""Skill platforms — `staticPlacements` entries with `kind: "skill"`.

These name their grant in `skillId`, a **SkillData** id. The id space overlaps
ItemData (`shield_blue` is both an item and a skill) and only the SkillData one
is read, so resolving one through the item index would quietly show the wrong
thing.

Unlike most of what this tool guards, none of these fail silently:
`SkillPlatformSpawnAdapter` throws on a missing skillId, an unknown one, a
non-passive one, and one with no `iconKeys`. A throw at room-spawn time takes
the room down, so all four are errors here rather than warnings.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level, validate_loot  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


def skill_placements(ds):
    """Every kind="skill" placement in the data, with its room id."""
    out = []
    for level_id in ds.ids_of("LevelData"):
        view = ds.level_view(level_id)
        for floor in view["floors"]:
            for room in floor["rooms"]:
                for p in room["staticPlacements"]:
                    if p["kind"] == "skill":
                        out.append((level_id, room["roomId"], p))
    return out


# ── the projection ───────────────────────────────────────────────────────


def test_the_data_still_has_skill_platforms(ds):
    """Guards the rest of this file from passing vacuously."""
    assert len(skill_placements(ds)) >= 4


def test_skill_placements_carry_their_skill_id(ds):
    """The one field that says what the platform grants must reach the UI."""
    for level_id, room_id, p in skill_placements(ds):
        assert p["skillId"], f"{level_id}/{room_id}: placement lost its skillId"
        assert p["skill"] is not None


def test_the_brief_resolves_the_skill_not_the_item(ds):
    """`shield_blue` is both an ItemData and a SkillData; only one is right.

    The item is a drop with a rarity string; the skill is what a platform
    grants and carries `type` and iconKeys. Reading the item here would give
    a plausible-looking name for the wrong record.
    """
    brief = ds.skill_brief("shield_blue")
    assert brief["missing"] is False
    assert brief["displayName"] == "Blue Shield"
    assert brief["skillType"] == "passive"
    assert brief["iconKey"] == "skill/shield_blue@icon"
    assert brief["sprite"] is not None, "the platform's icon should resolve"

    # The item of the same id is a different record with a different shape.
    assert ds.item_brief("shield_blue")["itemType"] == "skill"
    assert "skillType" not in ds.item_brief("shield_blue")


def test_an_unknown_skill_id_is_reported_not_guessed(ds):
    brief = ds.skill_brief("no_such_skill")
    assert brief["missing"] is True
    assert brief["displayName"] is None


def test_only_skill_placements_get_a_brief(ds):
    """A cargo box has no skillId; resolving one for it would be noise."""
    view = ds.level_view("chapter_1")
    others = [p for f in view["floors"] for r in f["rooms"]
              for p in r["staticPlacements"] if p["kind"] != "skill"]
    assert others, "expected non-skill placements in chapter_1"
    assert all(p["skill"] is None for p in others)


# ── the picker's offer ───────────────────────────────────────────────────


def test_the_catalog_offers_only_what_can_be_placed(ds):
    """Every offered skill must survive SkillPlatformSpawnAdapter.

    The adapter throws on an active skill, on one with no iconKeys, and on one
    whose icon will not load, so an entry here that fails any of the three
    hands the designer a two-click path to a crash.
    """
    catalog = ds.skill_catalog()
    assert catalog
    for row in catalog:
        doc = ds.get("SkillData", row["id"])
        assert doc is not None
        assert doc.value.get("type") == "passive", row["id"]
        icon = (doc.value.get("iconKeys") or [None])[0]
        assert icon, row["id"]
        assert ds.sprites.guid_for_key(icon) is not None, row["id"]
        assert row["sprite"] is not None, f"{row['id']} has no art for its card"


def test_the_catalog_matches_resourcesdb_verbatim(ds):
    """No `@`-suffix fallback, because ResourcesAssetService has none.

    `sprite_for_resource_key` drops the suffix and retries, which is fine for
    drawing something in a list — but a key that only resolves that way still
    throws in game, so the catalog must not be built on it.
    """
    lenient = [d.id for d in ds.of_type("SkillData")
               if (d.value.get("iconKeys") or [None])[0]
               and ds.sprites.guid_for_key((d.value["iconKeys"])[0]) is None
               and ds.sprites.sprite_for_resource_key((d.value["iconKeys"])[0]) is not None]
    offered = {row["id"] for row in ds.skill_catalog()}
    assert not (offered & set(lenient)), \
        f"offered on the lenient fallback only: {sorted(offered & set(lenient))}"


def test_the_catalog_is_narrower_than_the_skill_list(ds):
    """It is a filter, not a rename of `ids_of("SkillData")`."""
    assert len(ds.skill_catalog()) < len(ds.ids_of("SkillData"))


# ── validation ───────────────────────────────────────────────────────────


def codes_for(ds, level_id):
    return [i["code"] for i in validate_level(ds, level_id)]


def a_skill_placement(ds, level_id):
    """The raw (writable) placement dict, for the monkeypatched cases."""
    for floor in ds.get("LevelData", level_id).value["floors"]:
        for room in floor["rooms"]:
            for p in room.get("staticPlacements") or []:
                if p.get("kind") == "skill":
                    return p
    raise AssertionError(f"{level_id} has no skill placement")


def test_an_unloadable_icon_is_an_error(ds, monkeypatch):
    """ResolveIcon's second throw: iconKeys names a key that will not load.

    Driven by monkeypatch rather than by whichever room currently has the
    problem. These are live authoring mistakes that get fixed — pinning the
    room makes the test fail when someone does the right thing, which is
    backwards. What must not regress is the rule.
    """
    # A real SkillData whose icon key is absent from ResourcesDB. This is a
    # property of the skill, not of any level, so it is stable to assert.
    unloadable = next(
        d.id for d in ds.of_type("SkillData")
        if d.value.get("type") == "passive"
        and (d.value.get("iconKeys") or [None])[0]
        and ds.sprites.guid_for_key(d.value["iconKeys"][0]) is None)

    placement = a_skill_placement(ds, "chapter_1")
    monkeypatch.setitem(placement, "skillId", unloadable)
    messages = [i["message"] for i in validate_level(ds, "chapter_1")
                if i["code"] == "ROOM_PLACEMENT_SKILL_NO_ICON"]
    assert any(unloadable in m and "ResourcesDB" in m for m in messages), messages


def test_a_skill_with_no_icon_is_an_error(ds, monkeypatch):
    """ResolveIcon's first throw: no iconKeys at all."""
    iconless = next(d.id for d in ds.of_type("SkillData")
                    if d.value.get("type") == "passive"
                    and not (d.value.get("iconKeys") or []))
    placement = a_skill_placement(ds, "chapter_1")
    monkeypatch.setitem(placement, "skillId", iconless)
    messages = [i["message"] for i in validate_level(ds, "chapter_1")
                if i["code"] == "ROOM_PLACEMENT_SKILL_NO_ICON"]
    assert any(iconless in m and "iconKeys" in m for m in messages), messages


def test_whatever_is_authored_today_is_reported_consistently(ds):
    """Not an assertion about which rooms are broken — that changes as they
    get fixed — but that a placement the adapter would throw on is always
    reported, and one it would accept never is.
    """
    for level_id in ds.ids_of("LevelData"):
        flagged = {i["pointer"] for i in validate_level(ds, level_id)
                   if i["code"].startswith("ROOM_PLACEMENT_SKILL")}
        view = ds.level_view(level_id)
        for fi, floor in enumerate(view["floors"]):
            for room in floor["rooms"]:
                for p in room["staticPlacements"]:
                    if p["kind"] != "skill":
                        continue
                    doc = ds.get("SkillData", p["skillId"] or "")
                    icon = (doc.value.get("iconKeys") or [None])[0] if doc else None
                    would_throw = (
                        not p["skillId"] or doc is None
                        or doc.value.get("type") != "passive"
                        or not icon or ds.sprites.guid_for_key(icon) is None)
                    ptr = f"{p['pointer']}/skillId"
                    # DATA_REFERENCE_MISSING covers the unknown-id case under
                    # its own code, so exclude it from the "is flagged" test.
                    if doc is None and p["skillId"]:
                        continue
                    assert (ptr in flagged) == would_throw, (level_id, ptr, would_throw)


def test_healthy_skill_placements_raise_nothing(ds):
    """chapter_1's platform grants a passive skill that has an icon."""
    assert not [c for c in codes_for(ds, "chapter_1") if c.startswith("ROOM_PLACEMENT_SKILL")]


def test_every_reported_skill_issue_is_an_error(ds):
    """The adapter throws for all of them, so none of these is advisory."""
    for level_id in ds.ids_of("LevelData"):
        for issue in validate_level(ds, level_id):
            if issue["code"].startswith("ROOM_PLACEMENT_SKILL"):
                assert issue["severity"] == "error", issue


def test_an_unknown_skill_id_is_a_reference_error(ds, monkeypatch):
    """skillId joins the other cross-references the validator resolves."""
    placement = a_skill_placement(ds, "chapter_1")
    monkeypatch.setitem(placement, "skillId", "no_such_skill")
    issues = [i for i in validate_level(ds, "chapter_1")
              if i["code"] == "DATA_REFERENCE_MISSING" and "skillId" in i["pointer"]]
    assert len(issues) == 1
    assert "no_such_skill" in issues[0]["message"]


def test_a_missing_skill_id_is_an_error(ds, monkeypatch):
    """The state the editor creates when you add a skill from the library."""
    placement = a_skill_placement(ds, "chapter_1")
    monkeypatch.setitem(placement, "skillId", "")
    assert "ROOM_PLACEMENT_SKILL_MISSING" in codes_for(ds, "chapter_1")


def test_an_active_skill_on_a_platform_is_an_error(ds, monkeypatch):
    """A SkillPlatform only grants passives; an active one throws."""
    active = next(d.id for d in ds.of_type("SkillData")
                  if d.value.get("type") == "active")
    placement = a_skill_placement(ds, "chapter_1")
    monkeypatch.setitem(placement, "skillId", active)
    assert "ROOM_PLACEMENT_SKILL_NOT_PASSIVE" in codes_for(ds, "chapter_1")


# ── the other channel: skills as loot ────────────────────────────────────
#
# A skill platform is not the only way a skill reaches the player. A
# LootTableData entry naming an ItemData of `type: "skill"` drops one on
# death, and `ItemService.ApplySkill` installs it. The two channels share
# nothing — different data families, different failure modes — so both are
# pinned here rather than one standing in for the other.


def test_the_drop_chain_follows_the_runtime_order(ds):
    """override → EnemyData.dropTableId → item_exp_small, and nothing else.

    `EventBindings.OnEnemyDied` picks exactly one table by this order. Getting
    it wrong shows a designer a table the game will never roll.
    """
    over = ds.wave_drop({"enemyId": "blight_sac_stalker",
                         "dropTableIdOverride": "loot_ch1_build3_skill"})
    assert over["source"] == "override"
    assert over["tableId"] == "loot_ch1_build3_skill"

    # Same enemy, no override: its own table wins.
    plain = ds.wave_drop({"enemyId": "blight_sac_stalker"})
    assert plain["source"] == "enemy"
    assert plain["tableId"] == ds.get("EnemyData", "blight_sac_stalker").value["dropTableId"]
    assert plain["tableId"] != over["tableId"]

    # Neither: the last-resort item, not "nothing".
    none = ds.wave_drop({"enemyId": "no_such_enemy"})
    assert none["source"] == "fallback"
    assert none["tableId"] is None
    assert none["fallbackItemId"] == "item_exp_small"


def test_the_elite_bonus_is_additive(ds):
    """An elite rolls its own table AND gets the crystal, never instead."""
    drop = ds.wave_drop({"enemyId": "blight_sac_stalker", "eliteCount": 1})
    assert drop["tableId"] is not None
    assert drop["eliteBonusItemId"] == "item_exp_large"
    assert ds.wave_drop({"enemyId": "blight_sac_stalker"})["eliteBonusItemId"] is None


def test_a_skill_drop_is_flagged_on_the_wave_that_carries_it(ds):
    """The one thing the Loot column can never show."""
    waves = ds.encounter_enemies("spawn_ch1_build3_01")
    carriers = [w for w in waves if w["drop"]["hasSkill"]]
    assert len(carriers) == 1, [w["id"] for w in carriers]
    assert carriers[0]["drop"]["tableId"] == "loot_ch1_build3_skill"
    assert {i["displayName"] for i in carriers[0]["drop"]["items"]} == \
        {"Crimson Barrage", "Blue Shield"}


def test_every_wave_reports_a_drop(ds):
    """There is no such thing as a wave that drops nothing — the chain always
    lands somewhere, so a blank here would be the view lying."""
    for level_id in ds.ids_of("LevelData"):
        view = ds.level_view(level_id)
        for floor in view["floors"]:
            for room in floor["rooms"]:
                for wave in room["enemies"]:
                    if wave.get("missing") and "drop" not in wave:
                        continue
                    drop = wave["drop"]
                    assert drop["tableId"] or drop["fallbackItemId"], wave


def test_a_skill_item_resolves_by_its_own_id_not_refid(ds):
    """`sprint_engine` names skill `sprint` via refId and still throws.

    ApplySkill passes `item.id`; refId is declared on ItemData and read
    nowhere in the runtime. Live in `loot_skills` today.
    """
    item = ds.get("ItemData", "sprint_engine")
    assert item.value["type"] == "skill"
    assert item.value["refId"] == "sprint"
    assert ds.get("SkillData", "sprint") is not None
    assert ds.get("SkillData", "sprint_engine") is None

    codes = [i["code"] for i in validate_loot(ds, "loot_skills")]
    assert "LOOT_SKILL_ITEM_UNRESOLVED" in codes


def test_an_active_skill_dropped_as_loot_is_a_warning(ds):
    """AddPassive does not check `type`, so this installs but misbehaves."""
    issues = [i for i in validate_loot(ds, "loot_skills")
              if i["code"] == "LOOT_SKILL_ITEM_NOT_PASSIVE"]
    assert {i["pointer"] for i in issues}
    assert all(i["severity"] == "warning" for i in issues)
    named = " ".join(i["message"] for i in issues)
    assert "fireball" in named and "sprint" in named


def test_a_sound_skill_drop_raises_nothing(ds):
    """loot_ch1_build3_skill: both entries are passives with matching ids."""
    assert not [i for i in validate_loot(ds, "loot_ch1_build3_skill")
                if i["code"].startswith("LOOT_SKILL_")]
