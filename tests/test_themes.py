"""Floor-plan appearance: `FloorPlanData.appearance.tilePartConfigs` (OLE-2).

This is the one field whose legal values live outside `GameData/`. A
TilePartConfig is a Unity asset under `Assets/Prefabs/Room/Theme/`, so the
names can only be checked by walking the same chain the runtime walks:

    themeId → ResourcesDB → <Theme>.asset → tileTheme → <Theme>TileTheme.asset
                                                      → tilePartConfigs
                                                      + startRoomTilePartConfig

Globbing the theme folder for `*TilePart*.asset` would be easier and wrong,
which is what most of this file pins.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def floor(ds):
    """chapter_3 floor 0, restored after each test.

    The Dataset is module-scoped and its parsed values are shared, so a test
    that edits one must put it back.
    """
    f = ds.get("LevelBasePlanData", "chapter_3").value["floors"][0]
    before = dict(f["appearance"]), f["themeId"]
    yield f
    f["appearance"], f["themeId"] = dict(before[0]), before[1]


# ── resolving the theme ──────────────────────────────────────────────────


def test_a_theme_resolves_to_the_parts_it_owns(ds):
    owned = ds.themes.tile_parts("themes/factory")
    assert owned["found"] is True
    assert set(owned["pool"]) == {"FactoryTilePart1", "FactoryTilePart2",
                                  "FactoryTilePart3", "FactoryTilePart4"}


def test_the_named_only_part_is_addressable_but_not_in_the_pool(ds):
    """`startRoomTilePartConfig` is owned, deliberately kept out of the roll.

    `TileThemeConfig.FindTilePartConfig` searches it, so level data may name
    it; `RoomInstanceView` never rolls it for a room that did not ask. A
    validator that only knew the pool would reject a legal name.
    """
    owned = ds.themes.tile_parts("themes/factory")
    assert owned["named"] == ["FactoryTilePartTransparentEdge"]
    assert "FactoryTilePartTransparentEdge" not in owned["pool"]
    assert "FactoryTilePartTransparentEdge" in owned["all"]


def test_the_theme_asset_itself_is_not_a_part(ds):
    """The trap a folder glob falls into.

    `Assets/Prefabs/Room/Theme/Factory/` also holds `FactoryTheme.asset` and
    `FactoryTileTheme.asset`. Neither is a TilePartConfig.
    """
    owned = ds.themes.tile_parts("themes/factory")
    assert "FactoryTheme" not in owned["all"]
    assert "FactoryTileTheme" not in owned["all"]


def test_each_theme_owns_its_own_parts(ds):
    factory = ds.themes.tile_parts("themes/factory")["all"]
    basement = ds.themes.tile_parts("themes/basement")["all"]
    assert basement == ["BasementTilePart"]
    assert not set(factory) & set(basement)


def test_an_unknown_theme_reports_not_found_rather_than_empty(ds):
    """`found` and "owns nothing" must not look alike.

    Reading a broken chain as an empty set would turn every name on that
    floor into an error the moment the Unity tree moved.
    """
    missing = ds.themes.tile_parts("themes/nope")
    assert missing["found"] is False
    assert missing["all"] == []


# ── validation ───────────────────────────────────────────────────────────


def codes(ds, plan_id="chapter_3"):
    return [f'{i["severity"]}/{i["code"]}' for i in validate_plan(ds, plan_id)
            if "TILEPART" in i["code"]]


def test_the_shipped_plan_is_clean(ds):
    """chapter_3 names FactoryTilePart1 + 2 on every floor."""
    assert codes(ds) == []


def test_a_name_the_theme_does_not_own_is_an_error(ds, floor):
    floor["appearance"] = {"tilePartConfigs": ["FactoryTilePart1", "Nope"]}
    assert codes(ds) == ["error/LEVEL_TILEPART_UNKNOWN"]


def test_another_themes_part_is_an_error(ds, floor):
    """Ownership is per theme; a real asset name is not enough."""
    floor["appearance"] = {"tilePartConfigs": ["BasementTilePart"]}
    assert codes(ds) == ["error/LEVEL_TILEPART_UNKNOWN"]


def test_a_blank_entry_is_an_error(ds, floor):
    """AssignRoomAppearance throws on it at generation time."""
    floor["appearance"] = {"tilePartConfigs": ["FactoryTilePart1", ""]}
    assert codes(ds) == ["error/LEVEL_TILEPART_EMPTY"]


def test_the_named_only_part_validates(ds, floor):
    """Legal, because FindTilePartConfig searches the stage override too."""
    floor["appearance"] = {"tilePartConfigs": ["FactoryTilePartTransparentEdge"]}
    assert codes(ds) == []


def test_omitting_the_field_is_the_default_not_a_finding(ds, floor):
    """Absent = draw from the theme's full pool, which is most plans."""
    floor["appearance"] = {}
    assert codes(ds) == []
    floor["appearance"] = {"tilePartConfigs": []}
    assert codes(ds) == []


def test_an_unresolvable_theme_warns_instead_of_failing_every_name(ds, floor):
    """Saying nothing would skip the check silently; erroring would be wrong."""
    floor["themeId"] = "themes/ghost"
    assert codes(ds) == ["warning/LEVEL_TILEPART_UNCHECKED"]


def test_every_shipped_plan_passes(ds):
    for plan_id in ds.ids_of("LevelBasePlanData"):
        assert codes(ds, plan_id) == [], plan_id


# ── the bossId half of OLE-3 ─────────────────────────────────────────────


def test_an_empty_boss_id_is_a_legitimate_choice(ds):
    """OZX-994: blank means "no boss on this floor", not "unfilled".

    chapter_3 ships two floors with no boss, on purpose.
    """
    bosses = [f.get("bossId")
              for f in ds.get("LevelBasePlanData", "chapter_3").value["floors"]]
    assert bosses == [None, None, "blight_sac_sovereign"]
    assert not [i for i in validate_plan(ds, "chapter_3")
                if "bossId" in (i.get("pointer") or "")]


# ── the plan view ────────────────────────────────────────────────────────


def test_a_plan_view_has_no_rooms(ds):
    """The whole point of the distinction.

    A LevelBasePlanData is a recipe; rooms are computed at generation time.
    If a floor here ever grows a `rooms` key, something has started faking
    generation in the projection.
    """
    view = ds.plan_view("chapter_3")
    assert view["missing"] is False
    assert len(view["floors"]) == 3
    for floor in view["floors"]:
        assert "rooms" not in floor
        assert floor["roomCountMin"] and floor["roomCountMax"]


def test_the_view_carries_what_the_theme_owns(ds):
    """So the UI can offer the closed set instead of a free-text field."""
    floor = ds.plan_view("chapter_3")["floors"][0]
    assert floor["themeParts"]["found"] is True
    assert set(floor["appearance"]["tilePartConfigs"]) <= set(floor["themeParts"]["all"])


def test_declared_distinguishes_absent_from_empty(ds, floor):
    """The span writer replaces nodes and cannot add a key to an object, so
    the UI has to know whether there is anything to patch."""
    floor["appearance"] = {"tilePartConfigs": []}
    assert ds.plan_view("chapter_3")["floors"][0]["appearance"]["declared"] is True
    floor["appearance"] = {}
    assert ds.plan_view("chapter_3")["floors"][0]["appearance"]["declared"] is False


def test_a_missing_plan_is_reported_not_raised(ds):
    assert ds.plan_view("no_such_plan")["missing"] is True


def test_boss_presence_is_explicit_in_the_view(ds):
    """`hasBoss` exists so the UI never has to read a blank as "unfilled"."""
    floors = ds.plan_view("chapter_3")["floors"]
    assert [f["hasBoss"] for f in floors] == [False, False, True]
    assert floors[2]["bossMissing"] is False


def test_editing_tile_groups_leaves_the_file_byte_identical(ds, tmp_path):
    """Add a group then remove it: the text must come back unchanged.

    chapter_3 writes `[ "a", "b" ]` with inner padding, which is NOT the
    writer's house style — so rewriting the whole array would reformat it and
    reorder the author's entries. Append/remove touch neither, and this is
    what pins that.
    """
    from ozxlevel import jsonspan

    path = ds.get("LevelBasePlanData", "chapter_3").path
    before = path.read_bytes()
    ptr = "/floors/0/appearance/tilePartConfigs"

    doc = jsonspan.parse(before.decode("utf-8"))
    added = jsonspan.patch(doc, [{"op": "append", "path": ptr,
                                  "value": "FactoryTilePart4"}])
    assert "FactoryTilePart4" in added
    # The padding the file uses must survive the insert.
    assert '[ "FactoryTilePart1"' in added

    doc2 = jsonspan.parse(added)
    index = len(jsonspan.parse(added).value["floors"][0]["appearance"]["tilePartConfigs"]) - 1
    back = jsonspan.patch(doc2, [{"op": "remove", "path": f"{ptr}/{index}"}])
    assert back.encode("utf-8") == before
