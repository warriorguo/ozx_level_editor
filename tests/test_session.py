"""Generated levels and lock-aware reachability (OLE-5).

A generated `LevelData` arrives as a `LevelDocument`: the level at the root
plus `generation`, and `generatedLootTables` for the tables the assigner
synthesised in memory. Those tables exist nowhere on disk, so anything that
resolves loot for a generated level has to read them from the document — a
GameData-only lookup reports them missing, which would be the most confusing
thing this view could say.

The headline question is reachability, and it is NOT the walk `validate_level`
already does. That one crosses every door regardless of `locked`, which
answers "is this room connected". The player's question is "can I get there",
and the two diverge exactly when a key is stranded behind the door it opens.

Most of these tests build their own document. The one real dump on disk is a
healthy seed, so asserting against it alone would pass a checker that never
reports anything.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import session  # noqa: E402


def doc(floors, tables=None):
    return {"dataType": "LevelData", "id": "t",
            "generation": {"source": "test", "planId": "t", "masterSeed": 1},
            "floors": floors, "generatedLootTables": tables or {}}


def room(rid, doors=(), **kw):
    out = {"roomId": rid, "doors": [dict(d) for d in doors]}
    out.update(kw)
    return out


def door(to, locked=False, key=None):
    return {"toRoomId": to, "locked": locked, "keyId": key}


KEY_TABLE = {"loot_key": {"id": "loot_key",
                          "entries": [{"itemId": "key_silver"}]}}


# ── reachability ─────────────────────────────────────────────────────────


def test_an_unlocked_chain_is_all_reachable():
    d = doc([{"startRoomId": "a",
              "rooms": [room("a", [door("b")]), room("b", [door("c")]), room("c")]}])
    assert all(r["reachable"] for r in session.reachability(d))


def test_a_locked_door_stops_the_walk():
    """The difference from the validator's connectivity walk."""
    d = doc([{"startRoomId": "a",
              "rooms": [room("a", [door("b", locked=True, key="key_silver")]),
                        room("b")]}])
    reach = {r["roomId"]: r["reachable"] for r in session.reachability(d)}
    assert reach == {"a": True, "b": False}


def test_a_key_found_on_the_way_opens_the_door():
    """Collection is iterative: reaching `k` unlocks what was blocked before."""
    d = doc([{"startRoomId": "a",
              "rooms": [room("a", [door("k"), door("b", locked=True, key="key_silver")]),
                        room("k", guaranteedCargoTableIds=["loot_key"]),
                        room("b")]}], KEY_TABLE)
    reach = {r["roomId"]: r["reachable"] for r in session.reachability(d)}
    assert reach == {"a": True, "k": True, "b": True}


def test_a_key_behind_its_own_lock_is_a_deadlock():
    """The bug this view exists to find.

    Connectivity says every room is fine — they are all joined by doors. The
    player can never open the door, because the only key is on the far side.
    """
    d = doc([{"startRoomId": "a",
              "rooms": [room("a", [door("b", locked=True, key="key_silver")]),
                        room("b", [door("c")], guaranteedCargoTableIds=["loot_key"]),
                        room("c")]}], KEY_TABLE)
    reach = {r["roomId"]: r["reachable"] for r in session.reachability(d)}
    assert reach == {"a": True, "b": False, "c": False}


def test_the_walk_crosses_floors():
    """Locks in this data gate the floor transition, not movement within one.

    Walking each floor separately would call every floor fully reachable and
    find nothing, because each floor's own start is trivially reachable.
    """
    d = doc([
        {"startRoomId": "a", "rooms": [room("a", [door("b", locked=True, key="key_silver")])]},
        {"startRoomId": "b", "rooms": [room("b")]},
    ])
    reach = {r["roomId"]: r["reachable"] for r in session.reachability(d)}
    assert reach["b"] is False


def test_an_unlocked_door_with_a_key_id_still_opens():
    """`locked` is the gate; a keyId on an open door is not a lock."""
    d = doc([{"startRoomId": "a",
              "rooms": [room("a", [door("b", locked=False, key="key_silver")]), room("b")]}])
    assert all(r["reachable"] for r in session.reachability(d))


# ── where keys come from ─────────────────────────────────────────────────


def test_keys_are_found_in_every_loot_channel():
    tables = {"t": {"entries": [{"itemId": "key_gold"}]}}
    rooms = [
        room("plan", lootPlanId="t"),
        room("floor", floorLootTableId="t"),
        room("cargo", guaranteedCargoTableIds=["t"]),
        room("static", staticPlacements=[{"lootTableId": "t"}]),
        room("named", staticPlacements=[{"kind": "equipment", "itemId": "key_gold"}]),
        room("wave", inlineEncounter={"steps": [
            {"action": {"enemyId": "x", "dropTableIdOverride": "t"}}]}),
    ]
    found = session.key_sources(doc([{"startRoomId": "plan", "rooms": rooms}], tables))
    assert set(found) == {"plan", "floor", "cargo", "static", "named", "wave"}


def test_a_table_that_is_not_in_the_document_yields_nothing():
    """Synthesised tables live in the document; an id with no table is not a
    key source, and must not raise."""
    d = doc([{"startRoomId": "a", "rooms": [room("a", floorLootTableId="nope")]}])
    assert session.key_sources(d) == {}


def test_non_key_items_are_ignored():
    tables = {"t": {"entries": [{"itemId": "item_exp_small"}, {"itemId": "key_silver"}]}}
    d = doc([{"startRoomId": "a", "rooms": [room("a", floorLootTableId="t")]}], tables)
    assert session.key_sources(d) == {"a": {"key_silver"}}


# ── the real dump, when there is one ─────────────────────────────────────

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
REAL = session.list_documents(OZX_BASE)
real_only = pytest.mark.skipif(not REAL, reason="no session dump on disk")


@pytest.fixture(scope="module")
def dump():
    return session.load(REAL[0]["name"], OZX_BASE)


@real_only
def test_captures_are_found_in_the_session_folder_not_persistent_data():
    """OZX-1004 moved the dump next to the run that produced it.

    `AiTelemetry.SessionRoot` is `<project>/SessionFolder`, one folder per run.
    Globbing the pre-OZX-1004 persistentDataPath directory instead finds only
    whatever stale capture is still lying there — which is exactly what this
    loader did at first, showing one old run and hiding three current ones.
    """
    current = [d for d in REAL if not d["legacy"]]
    assert current, "no capture found under <project>/SessionFolder"
    assert all("/" in d["name"] for d in current), \
        "a capture is <session>/level*.json, not a flat file"


@real_only
def test_more_than_one_capture_per_run_is_listed():
    """`/api/control/reload` regenerates inside one run and
    SessionLevelDumpNaming lands it on level_2.json. Listing one row per
    session folder would hide every regenerate, so this globs files."""
    for d in REAL:
        assert d["name"].endswith(".json")
    names = {d["name"] for d in REAL}
    assert len(names) == len(REAL), "names must be unique keys"


@real_only
def test_a_capture_outside_the_root_is_refused():
    assert session.load("../../../etc/passwd", OZX_BASE) is None
    assert session.load("nope/level.json", OZX_BASE) is None


@real_only
def test_the_real_dump_parses_as_one_document(dump):
    """One loader for both producers: root LevelData + generation + tables."""
    assert dump["dataType"] == "LevelData"
    assert dump["generation"]["source"] in {"session", "cli"}
    assert dump["generation"]["planId"]
    assert isinstance(dump["generatedLootTables"], dict)
    assert dump["floors"]


@real_only
def test_the_real_dump_carries_its_keys_in_the_death_channel(dump):
    """Not on the room: the cross-floor keys ride a wave's
    dropTableIdOverride, which is InjectGuaranteedDrops. A view that only read
    room-level loot would report no key anywhere."""
    sources = session.key_sources(dump)
    assert sources, "no key source found in a dump that has locked doors"
    locked = [d for f in dump["floors"] for r in f["rooms"]
              for d in r.get("doors") or [] if d.get("locked")]
    assert locked
    needed = {d.get("keyId") for d in locked}
    assert needed <= {k for keys in sources.values() for k in keys}


@real_only
def test_a_generated_level_renders_through_the_ordinary_level_view(dump):
    """One view, not two.

    A generated level is the same LevelData shape, so it earns the floor map,
    the enemy rows and the loot columns like an authored one. A second,
    thinner projection was the first thing built here and it was wrong — it
    silently dropped the map and the loot.
    """
    from ozxlevel.dataset import Dataset

    ds = Dataset(pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base")
    view = ds.level_view(dump["id"], document=dump, file_name="dump.json")
    assert view["readOnly"] is True
    assert view["masterSeed"] == dump["masterSeed"]

    rooms = [r for f in view["floors"] for r in f["rooms"]]
    # The generator leaves encounterId empty and puts the waves inline, so a
    # view that only read encounterId would call every room quiet.
    assert any(r["enemies"] for r in rooms), "no room resolved any enemies"
    # Loot ids are synthesised in memory; resolving them against GameData
    # alone reports every one as a missing table.
    resolved = [r for r in rooms
                if r["roomClearLoot"] and not r["roomClearLoot"]["missing"]]
    assert resolved, "no synthesised loot table resolved"
    # And the layout has to survive, or there is no floor map.
    assert any(r["hasLayout"] for r in rooms)


@real_only
def test_a_generated_level_is_not_editable(dump):
    """A run that already happened has no source file to patch."""
    from ozxlevel.dataset import Dataset

    ds = Dataset(pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base")
    assert ds.level_view(dump["id"], document=dump)["readOnly"] is True
    assert ds.level_view("chapter_1")["readOnly"] is False



