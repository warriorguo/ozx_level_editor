"""A static placement's `chance` and `countMin` (OZX-1057).

`count` has always been a CEILING: random fill places as many as the room's
free Static cells allow. These are the two ends that grew around it.

- `countMin` is the floor below which the entry declines entirely. A
  wall-mounted set-piece placed once reads as clutter rather than as a room,
  so a room that can only hold one pod gets none and the cells go to whatever
  adapter runs next.
- `chance` is rolled ONCE for the whole entry, not once per object. `count: 3,
  chance: 0.2` is a room that is either a cultivation chamber with up to three
  pods or an ordinary room with none — never one lonely pod in every fifth
  room, which is the distribution that makes a set-piece read as clutter.

Where they are authored matters for this side: a procedural floor has no
hand-written rooms, so both live on `FloorPlanData.defaultStaticPlacements`,
which this tool did not look at at all before. chapter_3 writes the pod on all
three floors; no `LevelData` room carries either field today.

The two rules the engine throws on are `chance > 1` and `chance` on a kind
whose adapter never reads it. The two it does NOT throw on are the ones worth
having here anyway, because both end in a placement that is silently absent.
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

POD = {"kind": "prop", "prefabKey": "prefab/biocultivationpod",
       "footprintCols": 2, "footprintRows": 2,
       "count": 3, "countMin": 2, "chance": 0.2, "placement": "sidewall"}


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def floor(ds):
    """chapter_3 floor 0, restored after each test."""
    doc = ds.get("LevelBasePlanData", "chapter_3")
    before = copy.deepcopy(doc.value["floors"][0])
    yield doc.value["floors"][0]
    doc.value["floors"][0] = before


def codes(ds, entries):
    """Validate chapter_3 floor 0 with `entries` as its default placements."""
    doc = ds.get("LevelBasePlanData", "chapter_3")
    doc.value["floors"][0]["defaultStaticPlacements"] = entries
    return [i["code"] for i in validate_plan(ds, "chapter_3")]


# ── where the fields actually live ───────────────────────────────────────


def test_the_shipped_plan_authors_them_on_the_floor(ds):
    """Not on a room: a procedural floor has none to author them on."""
    view = ds.plan_view("chapter_3")
    # Ground lasers roll a chance too since OZX-1377; the pod is the prop.
    pods = [p for f in view["floors"] for p in f["defaultStaticPlacements"]
            if p["chance"] and p["kind"] == "prop"]
    assert len(pods) == 3, "chapter_3 writes the pod on all three floors"
    assert all(p["kind"] == "prop" and p["countMin"] == 2 for p in pods)
    assert all(p["chancePercent"] == 20.0 for p in pods)


def test_an_unauthored_chance_reads_as_always_not_never(ds):
    """0 means the field was never written. 0% would say the opposite."""
    view = ds.plan_view("chapter_3")
    cargo = next(p for p in view["floors"][0]["defaultStaticPlacements"]
                 if p["kind"] == "cargo")
    assert cargo["chance"] == 0
    assert cargo["chancePercent"] == 100.0


def test_the_shipped_plans_have_no_placement_findings(ds):
    for doc in ds.of_type("LevelBasePlanData"):
        found = [i["code"] for i in validate_plan(ds, doc.id)
                 if "PLACEMENT_CHANCE" in i["code"] or "COUNT_MIN" in i["code"]]
        assert not found, (doc.id, found)


# ── the two the engine throws on ─────────────────────────────────────────


def test_a_chance_above_one_is_refused(ds, floor):
    assert "ROOM_PLACEMENT_CHANCE_INVALID" in codes(ds, [{**POD, "chance": 1.5}])


def test_a_chance_on_a_kind_that_never_rolls_it_is_refused(ds, floor):
    """The adapter throws rather than letting it appear every time — a field
    that quietly does nothing reads as a mis-tuned number, not an unread one."""
    assert "ROOM_PLACEMENT_CHANCE_UNREAD" in codes(
        ds, [{"kind": "cargo", "count": 5, "chance": 0.2}])
    # `prop` is the one kind that does read it.
    assert "ROOM_PLACEMENT_CHANCE_UNREAD" not in codes(ds, [POD])


def test_a_chance_of_exactly_one_is_fine(ds, floor):
    """(0, 1] — 1 is 'always', the same as omitting it."""
    assert not [c for c in codes(ds, [{**POD, "chance": 1.0}])
                if "PLACEMENT_CHANCE" in c]


# ── the two it does not ──────────────────────────────────────────────────


def test_a_floor_above_the_ceiling_is_never_placeable(ds, floor):
    """count is the ceiling random fill takes what it can of, so a floor
    above it can never be met — and nothing upstream says so."""
    assert "ROOM_PLACEMENT_COUNT_MIN_ABOVE_MAX" in codes(
        ds, [{**POD, "count": 2, "countMin": 3}])
    assert "ROOM_PLACEMENT_COUNT_MIN_ABOVE_MAX" not in codes(
        ds, [{**POD, "count": 3, "countMin": 3}])


def test_a_floor_on_a_pinned_entry_is_not_read(ds, floor):
    """A pinned entry already names exactly what it wants."""
    found = codes(ds, [{**POD, "cells": [{"x": 1, "y": 1}], "countMin": 2}])
    assert "ROOM_PLACEMENT_COUNT_MIN_UNREAD" in found


def test_a_prop_off_the_wall_reads_countmin_as_a_range(ds, floor):
    """OZX-1465: `PropSpawnAdapter`'s plain random fill now rolls
    [countMin, count] per room (`RollRangedCount`, as toxicbarrel) — it used
    to read the field and drop it. The side-wall fill keeps its decline."""
    assert "ROOM_PLACEMENT_COUNT_MIN_UNREAD" not in codes(
        ds, [{**POD, "placement": None}])
    assert "ROOM_PLACEMENT_COUNT_MIN_UNREAD" not in codes(ds, [POD])


def test_a_kind_that_never_reads_countmin_is_told_so(ds, floor):
    assert "ROOM_PLACEMENT_COUNT_MIN_UNREAD" in codes(
        ds, [{"kind": "cargo", "count": 5, "countMin": 2}])


def test_an_empty_range_is_refused(ds, floor):
    """RollRangedCount throws on countMin > count."""
    assert "ROOM_PLACEMENT_COUNT_MIN_ABOVE_MAX" in codes(
        ds, [{"kind": "prop", "prefabKey": "prefab/groundbomb", "count": 2, "countMin": 3}])


@pytest.mark.parametrize("plan_id", ["chapter_4", "chapter_5", "chapter_6", "chapter_7"])
def test_the_shipped_ground_bombs_read_as_a_range(ds, plan_id):
    """OZX-1465 put prefab/groundbomb on every area of chapters 4–7."""
    view = ds.plan_view(plan_id)
    for f in view["floors"]:
        bombs = [p for p in f["defaultStaticPlacements"]
                 if p["prefabKey"] == "prefab/groundbomb"]
        assert bombs, (plan_id, f["index"] if "index" in f else None)
        assert all(b["countMinReading"] == "range" for b in bombs)
    pods = [p for f in ds.plan_view("chapter_3")["floors"]
            for p in f["defaultStaticPlacements"] if p.get("placement") == "sidewall"]
    assert pods and all(p["countMinReading"] == "floor" for p in pods)


def test_a_floor_of_one_or_zero_is_no_floor_at_all(ds, floor):
    """Both mean "take whatever fits", which is every placement before the
    field existed — so neither is a finding on any shape."""
    for value in (0, 1):
        entry = {**POD, "placement": None, "countMin": value}
        assert not [c for c in codes(ds, [entry]) if "COUNT_MIN" in c], value


# ── a LevelData room gets the same rules ─────────────────────────────────


def test_a_room_placement_is_checked_the_same_way(ds):
    """Nothing authors these on a room today, but the shape is legal and the
    adapter reads the room's entries through exactly the same path."""
    doc = ds.get("LevelData", "chapter_1")
    room = doc.value["floors"][0]["rooms"][0]
    before = copy.deepcopy(room["staticPlacements"])
    try:
        room["staticPlacements"] = [{"kind": "cargo", "count": 2, "chance": 0.5}]
        found = [i["code"] for i in validate_level(ds, "chapter_1")]
        assert "ROOM_PLACEMENT_CHANCE_UNREAD" in found
    finally:
        room["staticPlacements"] = before


# ── the one room-level exception (OZX-1057 / OLE-17) ─────────────────────


def test_an_oil_tank_is_marked_as_skipping_the_start_room(ds):
    """`defaultStaticPlacements` applies to a whole floor and never names a
    room, so the exception cannot be authored — it lives in
    `StaticPlacementPolicy` and is invisible in the JSON. The start room is
    the one room the player does not choose to enter (at launch and after
    every death) and an oil tank is a chain-reaction hazard that damages
    them, so a view that drew it on every room would draw one that is not
    there."""
    view = ds.plan_view("chapter_3")
    for floor in view["floors"]:
        by_kind = {p["kind"]: p for p in floor["defaultStaticPlacements"]}
        assert by_kind["oiltank"]["skipsStartRoom"] is True
        assert by_kind["cargo"]["skipsStartRoom"] is False
        assert by_kind["prop"]["skipsStartRoom"] is False


# ── groundlaser (OZX-1371 / OZX-1373 / OZX-1377) ────────────────────────


def laser(**extra):
    return {"kind": "groundlaser", **extra}


def test_the_shipped_lasers_roll_a_chance_and_are_clean(ds):
    lasers = [p for f in ds.plan_view("chapter_3")["floors"]
              for p in f["defaultStaticPlacements"] if p["kind"] == "groundlaser"]
    assert lasers and all(p["chancePercent"] == 30.0 for p in lasers)
    assert not [c for c in codes(ds, [laser(chance=0.3)])
                if c.startswith(("ROOM_PLACEMENT", "ROOM_GROUNDLASER"))]


@pytest.mark.parametrize("entry", [
    laser(count=6), laser(countMin=1), laser(countMin=4, count=3), laser(countMin=3, count=3),
    laser(cells=[{"x": 2, "y": 2}]), laser(footprintCols=2), laser(placement="sidewall"),
])
def test_a_laser_out_of_its_rules_is_refused(ds, entry):
    assert "ROOM_GROUNDLASER_INVALID" in codes(ds, [entry])


@pytest.mark.parametrize("entry", [laser(), laser(countMin=2, count=2), laser(countMin=3, count=4)])
def test_a_laser_range_with_an_even_count_is_fine(ds, entry):
    assert "ROOM_GROUNDLASER_INVALID" not in codes(ds, [entry])


def test_a_toxicbarrel_reads_countmin_as_a_range(ds):
    """OZX-1226: [countMin, count], uniform, rolled once per entry."""
    assert "ROOM_PLACEMENT_COUNT_MIN_UNREAD" not in codes(
        ds, [{"kind": "toxicbarrel", "count": 5, "countMin": 2}])
