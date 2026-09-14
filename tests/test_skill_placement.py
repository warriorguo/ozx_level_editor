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
from ozxlevel.validate import validate_level  # noqa: E402

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

    The adapter throws on an active skill and again on one with no iconKeys,
    so an entry here that fails either check hands the designer a two-click
    path to a crash.
    """
    catalog = ds.skill_catalog()
    assert catalog
    for row in catalog:
        doc = ds.get("SkillData", row["id"])
        assert doc is not None
        assert doc.value.get("type") == "passive", row["id"]
        assert doc.value.get("iconKeys"), row["id"]


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


def test_a_skill_with_no_icon_is_an_error(ds):
    """chapter_2's f0_room_2_2 grants `shield_orange`, which has no iconKeys.

    This is live in the shipped data: ResolveIcon throws, so that room does
    not spawn. Pinned as a regression rather than a fixture because the fix
    belongs in GameData, not here.
    """
    orange = ds.get("SkillData", "shield_orange")
    assert orange is not None and not (orange.value.get("iconKeys") or [])
    assert "ROOM_PLACEMENT_SKILL_NO_ICON" in codes_for(ds, "chapter_2")


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
