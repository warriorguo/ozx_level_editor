"""Resolving a game id to the pixels that represent it.

Five hops through Unity's asset graph, none of them typed or checked by
anything else: animConfigKey -> ResourcesDB -> AnimConfig -> SpriteAnimationData
-> a sprite sheet and the rect inside it. A wrong turn anywhere produces a
plausible-looking wrong picture rather than an error, so the shape of each hop
is pinned here against the real project.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.sprites import SpriteIndex, _css_offset, png_size  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/Prefabs/System/ResourcesDB.asset").is_file(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture(scope="module")
def idx():
    return SpriteIndex(OZX_BASE)


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


# ── the chain, one hop at a time ─────────────────────────────────────────


def test_resources_db_is_parsed(idx):
    assert len(idx.resource_keys) > 200
    assert idx.guid_for_key("anim/husk")


def test_the_guid_index_covers_the_project(idx):
    assert len(idx.guid_to_path) > 3000


def test_an_enemy_resolves_to_its_idle_frame(idx):
    """husk: anim/husk -> HuskAnimConfig -> idle -> Husk-BreathAnim -> frame 0."""
    sprite = idx.idle_sprite_for_key("anim/husk")
    assert sprite is not None
    assert sprite["texture"].endswith("HuskBreath.png")
    assert sprite["name"] == "HuskBreath_0"
    assert (sprite["width"], sprite["height"]) == (192, 192)


def test_the_idle_state_is_preferred_over_other_states(idx):
    """A catalog wants the resting pose, not whatever state happens to be first."""
    config = idx.path_for(idx.guid_for_key("anim/husk"))
    text = config.read_text(errors="ignore")
    assert "name: idle" in text, "fixture no longer has an idle state to prefer"
    assert idx.idle_sprite_for_key("anim/husk")["name"] == "HuskBreath_0"


def test_a_prefab_rigged_enemy_falls_back_to_its_parts(idx):
    """demolition has spriteAnimStates: [] AND an empty body sprite.

    Its whole silhouette is the head prefab, so resolution has to walk past
    both the animation states and the body to find anything at all.
    """
    sprite = idx.idle_sprite_for_key("anim/demolition")
    assert sprite is not None, "demolition should resolve through its head prefab"
    assert sprite["width"] > 0 and sprite["height"] > 0


def test_an_item_resolves_in_one_hop(idx):
    """Items point straight at a sprite; the @suffix is part of the key."""
    sprite = idx.sprite_for_resource_key("skill/crimson_barrage@icon")
    assert sprite is not None
    assert sprite["texture"].endswith(".png")


def test_an_unknown_key_is_none_not_an_exception(idx):
    assert idx.sprite_for_resource_key("no/such@thing") is None
    assert idx.idle_sprite_for_key("anim/does_not_exist") is None
    assert idx.idle_sprite_for_key(None) is None
    assert idx.sprite_for_prefab_key(None) is None


# ── the coordinate flip ──────────────────────────────────────────────────


def test_unity_rects_are_flipped_for_css():
    """Unity measures a sprite rect from the bottom of the texture, CSS from
    the top. Getting this backwards silently shows the wrong sprite."""
    # a 192-tall sprite sitting at the bottom of a 768-tall sheet
    assert _css_offset(0, 192, 768) == 576
    # ...and one at the top
    assert _css_offset(576, 192, 768) == 0


def test_a_real_sheet_rect_stays_inside_its_texture(idx):
    sprite = idx.idle_sprite_for_key("anim/big_mouth")
    assert sprite is not None
    assert 0 <= sprite["x"] <= sprite["sheetWidth"] - sprite["width"]
    assert 0 <= sprite["y"] <= sprite["sheetHeight"] - sprite["height"]


def test_no_resolved_sprite_anywhere_escapes_its_sheet(ds, idx):
    """A rect outside the texture renders as blank, which looks like no art."""
    bad = []
    for doc in ds.of_type("EnemyData"):
        s = idx.idle_sprite_for_key(doc.value.get("animConfigKey"))
        if not s:
            continue
        if not (0 <= s["x"] <= s["sheetWidth"] - s["width"]
                and 0 <= s["y"] <= s["sheetHeight"] - s["height"]):
            bad.append((doc.id, s))
    assert not bad, bad


def test_png_size_reads_the_header_without_decoding():
    texture = OZX_BASE / "Assets/Images/Characters/Husk/HuskBreath.png"
    assert png_size(texture) == (960, 192)
    assert png_size(OZX_BASE / "nope.png") is None


# ── coverage, stated rather than assumed ─────────────────────────────────


def test_most_enemies_resolve(ds, idx):
    """Four are drawn procedurally and have no static sprite; the rest should.

    Stated as a floor so a regression in the walk shows up, without pinning the
    exact number as the roster changes.
    """
    resolved = [d.id for d in ds.of_type("EnemyData")
                if idx.idle_sprite_for_key(d.value.get("animConfigKey"))]
    total = len(ds.of_type("EnemyData"))
    assert len(resolved) >= total - 6, \
        f"only {len(resolved)}/{total} enemies resolved to artwork"


def test_the_catalog_carries_the_sprite(ds):
    catalog = ds.enemy_catalog()
    assert any(e["sprite"] for e in catalog)
    for entry in catalog:
        # every row keeps a usable fallback
        assert entry["code"]
        if entry["sprite"]:
            assert entry["sprite"]["texture"].endswith(".png")


def test_loot_entries_carry_their_item_icon(ds):
    view = ds.loot_table_view("loot_ch1_build3_skill")
    assert any(e.get("sprite") for e in view["entries"])
