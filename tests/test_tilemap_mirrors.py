"""Mirrored tilemaps in the draw pool (OLE-59 / OZX-1467).

`RoomTilemapQuery.LoadPool` follows each template not marked `noMirror`
with three mirrors — FlipX, FlipY, Rotate180 — and remaps `openDoors` with
them. Every pool consumer sees the expanded pool, so this tool's must be the
expanded one too, or a mask only a mirror serves reads as uncovered. A pinned
`templateId` matches the authored file only.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import tilemaps  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
TOP, RIGHT, BOTTOM, LEFT = 1, 2, 4, 8


@pytest.mark.parametrize("orientation,mask,want", [
    ("FlipX", LEFT, RIGHT),
    ("FlipX", TOP | LEFT, TOP | RIGHT),
    ("FlipY", TOP, BOTTOM),
    ("FlipY", TOP | LEFT, BOTTOM | LEFT),
    ("Rotate180", TOP | LEFT, BOTTOM | RIGHT),
    ("Rotate180", 15, 15),
    ("FlipX", 16 | LEFT, 16 | RIGHT),   # bits above the four sides are kept
])
def test_door_bits_follow_the_flip(orientation, mask, want):
    assert tilemaps.mirror_doors(mask, orientation) == want


def fact(**kw):
    base = {"file": "t", "folder": "normal", "category": "", "shape": "",
            "stageType": "default", "openDoors": TOP | LEFT, "noMirror": False,
            "hasEmptyGround": False, "hasSoftEdge": False, "hasRail": False}
    return dict(base, **kw)


def test_a_template_enters_the_pool_four_times():
    """Always four, even when symmetry makes some identical: every template
    keeps the same share of the draw."""
    out = tilemaps.expand(fact(openDoors=15))
    assert [t["orientation"] for t in out] == ["Authored", "FlipX", "FlipY", "Rotate180"]
    assert [t["file"] for t in out] == ["t", "t@FlipX", "t@FlipY", "t@Rotate180"]
    assert {t["family"] for t in out} == {"t"}


def test_no_mirror_keeps_one():
    assert [t["file"] for t in tilemaps.expand(fact(noMirror=True))] == ["t"]


def test_a_mask_only_a_mirror_serves_is_covered():
    pool = tilemaps.expand(fact(openDoors=TOP | LEFT))
    got = tilemaps.candidates(pool, "default", BOTTOM | RIGHT)
    assert [t["file"] for t in got] == ["t@Rotate180"]
    assert tilemaps.candidates(tilemaps.expand(fact(noMirror=True)), "default", BOTTOM | RIGHT) == []


def test_exact_matches_win_over_supersets():
    pool = tilemaps.expand(fact(file="a", openDoors=LEFT)) + [fact(file="all", openDoors=15, noMirror=True)]
    assert [t["file"] for t in tilemaps.candidates(pool, "default", RIGHT)] == ["a@FlipX", "a@Rotate180"]
    assert [t["file"] for t in tilemaps.candidates(pool, "default", RIGHT | TOP)] == ["all"]


def test_rail_alone_falls_back_when_it_empties_the_list():
    pool = [fact(openDoors=15, noMirror=True)]
    assert tilemaps.candidates(pool, "default", 15, require_rail=True) == pool
    assert tilemaps.candidates(pool, "default", 15, require_full_ground=True) == pool
    assert tilemaps.candidates([fact(openDoors=15, hasEmptyGround=True)], "default", 15,
                               require_full_ground=True) == []


def test_basement_is_folder_scoped():
    pool = [fact(category="", folder="normal", noMirror=True)]
    assert tilemaps.candidates(pool, "default", TOP | LEFT, room_category="basement") == []


# ── against shipped data ─────────────────────────────────────────────────

needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/TilemapData").is_dir(),
    reason="sibling ozx_base checkout not found")


@needs_data
def test_the_shipped_pool_is_expanded():
    ds = Dataset(OZX_BASE)
    facts = ds.tilemaps.facts()
    files = sorted((OZX_BASE / "Assets/StreamingAssets/TilemapData").glob("*/*.json"))
    authored = [f for f in facts if f["orientation"] == "Authored"]
    assert len(authored) == len(files)
    opted_out = sum(1 for f in authored if f["noMirror"])
    assert len(facts) == 4 * len(files) - 3 * opted_out


@needs_data
def test_a_pin_names_the_authored_file_only():
    ds = Dataset(OZX_BASE)
    name = next(f["file"] for f in ds.tilemaps.facts() if f["orientation"] == "Authored")
    assert ds.pinned_templates(name)
    assert ds.pinned_templates(f"{name}@FlipX") == []
