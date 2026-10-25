"""`decorations[].decalCategory` (OLE-41 / OLE-42 — OZX-1268, OZX-1274).

The level says how much, the room theme says what it looks like.
`DecalSurface.StampCategory` throws on a name outside its ten cases, and a
known name the theme's `stainConfig` lacks draws nothing. The five
BattleField props are only in `BattleFieldDecalConfig`.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import DECAL_CATEGORIES, validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

BATTLEFIELD = {"ground_crater", "ground_scrub", "ground_gravel", "ground_rubble",
               "ground_stones_by_edge"}


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def level(ds):
    doc = ds.get("LevelData", "chapter_1")
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def decorate(level, category):
    room = level["floors"][0]["rooms"][0]
    room.setdefault("decorations", []).append({"decalCategory": category, "count": 3})


def found(ds, code):
    return [i for i in validate_level(ds, "chapter_1") if i["code"] == code]


def test_the_new_names_are_authorable():
    assert BATTLEFIELD <= set(DECAL_CATEGORIES)


def test_the_themes_say_which_they_can_draw(ds):
    assert BATTLEFIELD <= set(ds.themes.decal_categories("themes/battlefield")["names"])
    assert not BATTLEFIELD & set(ds.themes.decal_categories("themes/factory")["names"])


def test_an_unknown_category_throws_upstream(ds, level):
    decorate(level, "ground_craters")
    assert found(ds, "ROOM_DECAL_CATEGORY_UNKNOWN")[0]["severity"] == "error"


def test_cables_are_not_authorable(ds, level):
    """In the factory config, but placed around devices and never counted."""
    decorate(level, "abandoned_cable")
    assert "devices" in found(ds, "ROOM_DECAL_CATEGORY_UNKNOWN")[0]["message"]


def test_a_battlefield_prop_on_a_factory_floor_draws_nothing(ds, level):
    level["floors"][0]["themeId"] = "themes/factory"
    decorate(level, "ground_stones_by_edge")
    assert found(ds, "ROOM_DECAL_CATEGORY_NOT_IN_THEME")[0]["severity"] == "warning"


def test_it_draws_on_a_battlefield_floor(ds, level):
    level["floors"][0]["themeId"] = "themes/battlefield"
    decorate(level, "ground_stones_by_edge")
    added = f"/floors/0/rooms/0/decorations/{len(level['floors'][0]['rooms'][0]['decorations']) - 1}"
    assert not [i for i in found(ds, "ROOM_DECAL_CATEGORY_NOT_IN_THEME")
                if i["pointer"].startswith(added)]


def test_an_unresolvable_theme_is_not_judged(ds, level):
    level["floors"][0]["themeId"] = "themes/ghost"
    decorate(level, "ground_crater")
    assert not found(ds, "ROOM_DECAL_CATEGORY_NOT_IN_THEME")


def test_a_changed_theme_asset_is_reread(ds, tmp_path):
    """The theme cache is keyed on the stamps of the asset files it read —
    those live in the Unity tree, where `Dataset.refresh` never looks."""
    from ozxlevel import themes
    calls = []
    real = themes._category_names

    def counting(text):
        calls.append(1)
        return real(text)
    themes._category_names = counting
    try:
        ds.themes.invalidate()
        ds.themes.decal_categories("themes/battlefield")
        ds.themes.decal_categories("themes/battlefield")
        assert len(calls) == 1
        path = next(p for p, _ in ds.themes._cache["decals:themes/battlefield"][1].items()
                    if p.name.endswith("DecalConfig.asset"))
        ds.themes._cache["decals:themes/battlefield"][1][path] = (0, 0)
        ds.themes.decal_categories("themes/battlefield")
        assert len(calls) == 2
    finally:
        themes._category_names = real
