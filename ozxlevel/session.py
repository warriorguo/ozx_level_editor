"""Generated levels: the one loader for a `LevelDocument` (OLE-5 / OLE-1).

A generated `LevelData` reaches this tool two ways — the headless `genlevel`
CLI ("this plan at seed N", a hypothetical run) and the editor's session dump
("the level the player actually got", a run that happened). `Game.Level/
LevelDocument.cs` deliberately gives both the same on-disk shape so that this
side needs ONE parser and the source is just a field. If a second parser ever
becomes necessary here, the shapes have drifted and the fix belongs upstream.

The document is a `LevelData` at the root plus three keys a LevelData reader
ignores:

- ``dataType`` — the usual GameData discriminator.
- ``generation`` — ``{source, planId, masterSeed}``; without it a dump cannot
  be traced back to a run.
- ``generatedLootTables`` — id -> LootTableData for the tables the assigner
  synthesised in memory. They exist nowhere on disk, and rooms reference them
  by id, so resolving loot for a generated level means looking here FIRST and
  falling back to GameData. Reporting them as missing tables (which is what a
  GameData-only lookup does) would be the single most confusing thing this
  view could say.
"""

from __future__ import annotations

import json
import pathlib

# Where the Unity player writes its session dumps. `Application.persistentDataPath`
# on macOS is ~/Library/Application Support/<company>/<product>.
SESSION_DIRS = (
    pathlib.Path.home() / "Library/Application Support/DefaultCompany/new_ozx/sessiondata",
)


def session_dir() -> pathlib.Path | None:
    for candidate in SESSION_DIRS:
        if candidate.is_dir():
            return candidate
    return None


def list_documents() -> list[dict]:
    """Every dump on disk, newest first.

    Cheap: filename and mtime only. The file is ~160KB, and a picker that
    parsed all of them to build a list would pay that for nothing.
    """
    root = session_dir()
    if root is None:
        return []
    out = []
    for path in root.glob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "name": path.name,
            # The stem is `<timestamp>_<planId>_m<seed>`; showing the raw name
            # is honest and sorts correctly, so it is not parsed apart here.
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    return sorted(out, key=lambda d: d["mtime"], reverse=True)


def load(name: str) -> dict | None:
    """Parse one dump by filename. Returns None for anything outside the dir."""
    root = session_dir()
    if root is None:
        return None
    # Resolve and contain: `name` arrives from an HTTP query.
    target = (root / name).resolve()
    if not str(target).startswith(str(root.resolve())) or not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


# ── reachability ─────────────────────────────────────────────────────────


def reachability(document: dict) -> list[dict]:
    """Where can the player get, and what is holding them up?

    The validator's existing walk crosses every door regardless of `locked`,
    which answers "is this room connected" — a different question from "can
    the player get there", and the one that matters when a floor is gated by a
    key. This walks locked doors only once their key has been collected, so a
    room behind a lock whose key lies behind that same lock never opens.

    The walk is over the WHOLE level rather than per floor, because the locks
    in this data are cross-floor transitions: floor 1's start is behind floor
    0's locked door. Treating each floor separately would call every floor
    fully reachable and find nothing.
    """
    rooms = {}
    for fi, floor in enumerate(document.get("floors") or []):
        for room in floor.get("rooms") or []:
            rid = room.get("roomId")
            if rid:
                rooms[rid] = (fi, room)

    floors = document.get("floors") or []
    start = floors[0].get("startRoomId") if floors else None
    drops = key_sources(document)

    reached: set[str] = set()
    keys: set[str] = set()
    # Doors we have stood in front of but could not open yet, as
    # (from_room, to_room, keyId).
    blocked: list[tuple[str, str, str]] = []

    progressed = True
    while progressed:
        progressed = False
        queue = [start] if start else []
        blocked = []
        seen_this_pass = set()
        while queue:
            current = queue.pop()
            if current in seen_this_pass or current not in rooms:
                continue
            seen_this_pass.add(current)
            # Standing in a room collects whatever it drops.
            for key_id in drops.get(current, set()):
                if key_id not in keys:
                    keys.add(key_id)
                    progressed = True
            for door in rooms[current][1].get("doors") or []:
                nxt = door.get("toRoomId")
                if not nxt:
                    continue
                key_id = door.get("keyId")
                if door.get("locked") and key_id and key_id not in keys:
                    blocked.append((current, nxt, key_id))
                    continue
                queue.append(nxt)
        if seen_this_pass - reached:
            progressed = True
        reached |= seen_this_pass

    out = []
    for rid, (fi, room) in rooms.items():
        out.append({
            "roomId": rid,
            "floor": fi,
            "reachable": rid in reached,
            "dropsKeys": sorted(drops.get(rid, ())),
        })
    out.sort(key=lambda r: (r["floor"], r["roomId"]))
    return out


def key_sources(document: dict) -> dict[str, set[str]]:
    """room id -> the key ids that room can yield.

    Keys reach the player through every loot channel, so all of them are
    walked: the room's own plan, its floor table, its guaranteed cargo, a
    static placement's table, and — the one that actually carries the
    cross-floor keys in this data — a `dropTableIdOverride` on an
    `inlineEncounter` wave, which is `InjectGuaranteedDrops`' death channel.
    """
    tables = document.get("generatedLootTables") or {}

    def keys_in(table_id):
        table = tables.get(table_id)
        if not table:
            return set()
        return {e.get("itemId") for e in table.get("entries") or []
                if str(e.get("itemId") or "").startswith("key_")}

    out: dict[str, set[str]] = {}
    for floor in document.get("floors") or []:
        for room in floor.get("rooms") or []:
            rid = room.get("roomId")
            if not rid:
                continue
            found: set[str] = set()
            for table_id in (room.get("lootPlanId"), room.get("floorLootTableId")):
                found |= keys_in(table_id)
            for table_id in room.get("guaranteedCargoTableIds") or []:
                found |= keys_in(table_id)
            for placement in room.get("staticPlacements") or []:
                found |= keys_in(placement.get("lootTableId"))
                # An equipment/cargo placement can also name the item outright.
                item = placement.get("itemId")
                if item and str(item).startswith("key_"):
                    found.add(item)
            encounter = room.get("inlineEncounter") or {}
            for step in encounter.get("steps") or []:
                action = step.get("action") or {}
                found |= keys_in(action.get("dropTableIdOverride"))
            if found:
                out[rid] = found
    return out
