"""Loads GameData and projects it into the shape the editor UI needs.

Two rules govern everything here, both from the TD:

1. The DOM is the only writable truth. We keep each file's original text and
   its span map (see :mod:`ozxlevel.jsonspan`); the dicts handed to the UI are
   a read-only projection. Nothing is ever re-serialised wholesale.
2. Several fields fail silently in production. Those are surfaced explicitly
   rather than smoothed over — see :mod:`ozxlevel.validate`.
"""

from __future__ import annotations

import pathlib
from typing import Any

from . import jsonspan

# DoorDirection in Game.Contracts/Enums/GameEnums.cs:58 — the integer IS the
# enum, so 0 means Up and never "absent".
DIRECTIONS = {0: "N", 1: "S", 2: "W", 3: "E"}
DIRECTION_IDS = {v: k for k, v in DIRECTIONS.items()}
# Twin of each direction, for generating the other side of a door.
DIRECTION_TWIN = {0: 1, 1: 0, 2: 3, 3: 2}

# The five families Level Studio owns; everything else is read-only closure.
OWNED = ("levels", "level_plans", "encounters", "enemies", "loot_tables")

# dataType -> the GameData subdirectory it lives in. `levels/` holds two
# types, told apart by dataType (never by filename).
SUBDIR = {
    "LevelData": "levels",
    "LevelBasePlanData": "levels",
    "EncounterData": "encounters",
    "EnemyData": "enemies",
    "LootTableData": "loot_tables",
    "ItemData": "items",
}


class Document:
    """One JSON file: its path, parsed value, and span map for editing."""

    __slots__ = ("path", "doc", "data_type", "id")

    def __init__(self, path: pathlib.Path):
        self.path = path
        text = path.read_bytes().decode("utf-8")
        self.doc = jsonspan.parse(text)
        value = self.doc.value
        self.data_type = value.get("dataType") if isinstance(value, dict) else None
        self.id = value.get("id") if isinstance(value, dict) else None

    @property
    def value(self) -> Any:
        return self.doc.value

    def reload(self) -> None:
        text = self.path.read_bytes().decode("utf-8")
        self.doc = jsonspan.parse(text)

    def apply(self, edits: list[dict]) -> None:
        """Patch the file on disk, preserving every byte we did not edit."""
        new_text = jsonspan.patch(self.doc, edits)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(new_text.encode("utf-8"))
        tmp.replace(self.path)
        self.doc = jsonspan.parse(new_text)


class Dataset:
    """All of GameData, indexed by (dataType, id)."""

    def __init__(self, project_root: str | pathlib.Path):
        self.root = pathlib.Path(project_root).expanduser().resolve()
        self.game_data = self.root / "Assets/StreamingAssets/GameData"
        self.tilemap_data = self.root / "Assets/StreamingAssets/TilemapData"
        self.docs: dict[tuple[str, str], Document] = {}
        self.problems: list[dict] = []
        self.load()

    # ── loading ──────────────────────────────────────────────────────────

    def load(self) -> None:
        self.docs.clear()
        self.problems.clear()
        if not self.game_data.is_dir():
            self.problems.append({
                "severity": "error",
                "code": "DATA_ROOT_MISSING",
                "message": f"GameData not found under {self.root}",
            })
            return

        for path in sorted(self.game_data.rglob("*.json")):
            try:
                doc = Document(path)
            except Exception as exc:  # malformed JSON must not be silent
                self.problems.append({
                    "severity": "error",
                    "code": "DATA_JSON_INVALID",
                    "message": f"{path.name}: {exc}",
                    "path": str(path),
                })
                continue
            if not doc.data_type:
                self.problems.append({
                    "severity": "error", "code": "DATA_TYPE_UNKNOWN",
                    "message": f"{path.name}: no dataType", "path": str(path)})
                continue
            if not doc.id:
                self.problems.append({
                    "severity": "error", "code": "DATA_ID_MISSING",
                    "message": f"{path.name}: no id", "path": str(path)})
                continue
            key = (doc.data_type, doc.id)
            if key in self.docs:
                # JsonDataRepository silently lets the last file win. Don't.
                self.problems.append({
                    "severity": "error", "code": "DATA_ID_DUPLICATE",
                    "message": f"duplicate {doc.data_type} id '{doc.id}': "
                               f"{self.docs[key].path.name} and {path.name}",
                    "path": str(path)})
                continue
            self.docs[key] = doc

    # ── lookup ───────────────────────────────────────────────────────────

    def get(self, data_type: str, id_: str) -> Document | None:
        return self.docs.get((data_type, id_))

    def of_type(self, data_type: str) -> list[Document]:
        return [d for (t, _), d in sorted(self.docs.items()) if t == data_type]

    def ids_of(self, data_type: str) -> list[str]:
        return [d.id for d in self.of_type(data_type)]

    # ── projections the UI consumes ──────────────────────────────────────

    def level_summaries(self) -> list[dict]:
        out = []
        for doc in self.of_type("LevelData"):
            floors = doc.value.get("floors") or []
            out.append({
                "id": doc.id,
                "displayName": doc.value.get("displayName") or doc.id,
                "file": doc.path.name,
                "floors": len(floors),
                "rooms": sum(len(f.get("rooms") or []) for f in floors),
            })
        return out

    def enemy_catalog(self) -> list[dict]:
        out = []
        for doc in self.of_type("EnemyData"):
            v = doc.value
            meta = " · ".join(x for x in (
                v.get("movementType"), v.get("attackType"), v.get("role")) if x)
            out.append({
                "id": doc.id,
                "meta": meta or "enemy",
                "code": _code(doc.id),
                "category": v.get("category"),
                "role": v.get("role"),
                "hasElite": bool(v.get("elite")),
                "dropTableId": v.get("dropTableId"),
            })
        return out

    def loot_catalog(self) -> list[dict]:
        out = []
        for doc in self.of_type("LootTableData"):
            entries = doc.value.get("entries") or []
            items = ", ".join(e.get("itemId", "?") for e in entries[:3])
            if len(entries) > 3:
                items += f", +{len(entries) - 3}"
            out.append({
                "id": doc.id,
                "meta": (f"{'pick one' if doc.value.get('pickOne') else 'independent'}"
                         f" · {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
                         + (f" · {items}" if items else " · EMPTY")),
                "code": _code(doc.id),
                "pickOne": bool(doc.value.get("pickOne")),
                "entryCount": len(entries),
                "empty": not entries,
            })
        return out

    def item_brief(self, item_id: str | None) -> dict:
        """Resolve an itemId to what a designer needs to recognise it.

        Filenames do not carry the id — 22 of the 47 items are stored under a
        different basename (`item_exp_large` lives in `exp_large.json`) — so
        this always goes through the (dataType, id) index, never the path.
        """
        if not item_id:
            return {"missing": True, "displayName": None, "itemType": None,
                    "rarity": None, "value": None, "spriteKey": None}
        doc = self.get("ItemData", item_id)
        if doc is None:
            return {"missing": True, "displayName": None, "itemType": None,
                    "rarity": None, "value": None, "spriteKey": None}
        v = doc.value
        return {
            "missing": False,
            # nameTemplate wins at generation time when set; displayName is the
            # fixed-literal fallback (OZX-586).
            "displayName": v.get("nameTemplate") or v.get("displayName") or item_id,
            "itemType": v.get("type"),
            "rarity": v.get("rarity"),
            "value": v.get("value"),
            "spriteKey": v.get("spriteKey"),
        }

    def rarity_palette(self) -> dict:
        """The game's own rarity -> text colour map, from box_rarity.json.

        Read rather than hardcoded: this data has `uncommon` blue and `rare`
        green, which inverts the usual convention, so a guessed palette would
        mislabel every item.
        """
        doc = self.get("BoxRarityConfigData", "box_rarity")
        if doc is None:
            return {}
        return {e.get("rarity"): e.get("textColor")
                for e in doc.value.get("entries") or [] if e.get("rarity")}

    def loot_table_view(self, table_id: str | None) -> dict | None:
        """Resolve a loot table into rows with their effective drop chance.

        The two weight semantics are genuinely different and the UI must not
        blur them (``DropTableResolver``):

        - ``pickOne`` — exactly one entry is chosen, weighted. A row's chance
          is its share of the total weight.
        - default — every entry rolls independently, and ``weight`` IS the
          percentage. Weights do not sum to 100 and must not be normalised.
        """
        if not table_id:
            return None
        doc = self.get("LootTableData", table_id)
        if doc is None:
            return {"id": table_id, "missing": True, "entries": [], "pickOne": False}
        entries = doc.value.get("entries") or []
        pick_one = bool(doc.value.get("pickOne"))
        total = sum(max(0, e.get("weight", 0)) for e in entries)
        rows = []
        for index, e in enumerate(entries):
            weight = e.get("weight", 0)
            if pick_one:
                chance = (weight / total * 100) if total else 0.0
            else:
                chance = min(100.0, max(0.0, float(weight)))
            item_id = e.get("itemId")
            row = {
                "index": index,
                "itemId": item_id,
                "weight": weight,
                "minCount": e.get("minCount", 0),
                "maxCount": e.get("maxCount", 0),
                "chance": round(chance, 1),
                "pointer": f"/entries/{index}",
            }
            row.update(self.item_brief(item_id))
            rows.append(row)
        return {
            "id": table_id,
            "missing": False,
            "pickOne": pick_one,
            "entries": rows,
            "empty": not entries,
        }

    def static_catalog(self) -> list[dict]:
        """Placement kinds, from what the data actually uses.

        StaticPlacementEntryData.kind is a controlled discriminator; the
        adapters switch on it and throw on anything unrecognised, so the
        library offers exactly the kinds in use plus the documented set.
        """
        seen: dict[str, int] = {}
        for doc in self.of_type("LevelData"):
            for floor in doc.value.get("floors") or []:
                for room in floor.get("rooms") or []:
                    for p in room.get("staticPlacements") or []:
                        kind = p.get("kind")
                        if kind:
                            seen[kind] = seen.get(kind, 0) + 1
        known = ["cargo", "oiltank", "toxicbarrel", "pinballhammer", "prop",
                 "skill", "device", "trigger", "equipment"]
        for k in known:
            seen.setdefault(k, 0)
        return [{"id": k, "meta": f"placement · used {seen[k]}×",
                 "code": _code(k), "uses": seen[k]}
                for k in sorted(seen, key=lambda k: (-seen[k], k))]

    def encounter_enemies(self, encounter_id: str | None) -> list[dict]:
        """The Effective Enemy view for a room.

        Room enemies are not authored on the room — they come from the
        EncounterData its `encounterId` points at. Each `launch` step
        contributes its own count; a step with no `action` is a pure wait.
        """
        if not encounter_id:
            return []
        doc = self.get("EncounterData", encounter_id)
        if doc is None:
            return [{"id": encounter_id, "count": 0, "missing": True,
                     "meta": "encounter not found"}]
        out = []
        for index, step in enumerate(doc.value.get("steps") or []):
            action = step.get("action")
            if not action:
                continue  # absent action == wait, not an enemy source
            enemy_id = action.get("enemyId")
            if not enemy_id:
                continue
            out.append({
                "id": enemy_id,
                "count": action.get("max", action.get("min", 0)) or 0,
                "min": action.get("min", 0),
                "max": action.get("max", 0),
                "stepIndex": index,
                "stepId": step.get("id"),
                "eliteCount": action.get("eliteCount", 0),
                "viaSpawner": action.get("viaSpawner"),
                "pointer": f"/steps/{index}/action",
                "encounterId": encounter_id,
                "missing": self.get("EnemyData", enemy_id) is None,
            })
        return out

    def level_view(self, level_id: str) -> dict:
        """Everything the editor needs to render one level."""
        doc = self.get("LevelData", level_id)
        if doc is None:
            raise KeyError(level_id)
        floors = []
        for f_index, floor in enumerate(doc.value.get("floors") or []):
            rooms = []
            for r_index, room in enumerate(floor.get("rooms") or []):
                base = f"/floors/{f_index}/rooms/{r_index}"
                doors = room.get("doors") or []
                rooms.append({
                    "index": r_index,
                    "pointer": base,
                    "roomId": room.get("roomId"),
                    "roomCategory": room.get("roomCategory"),
                    "stageType": room.get("stageType"),
                    "roomShape": room.get("roomShape"),
                    "templateId": room.get("templateId"),
                    "encounterId": room.get("encounterId"),
                    "lootPlanId": room.get("lootPlanId"),
                    "bossId": room.get("bossId"),
                    "isFinalRoom": bool(room.get("isFinalRoom")),
                    "hasTeleportSpot": bool(room.get("hasTeleportSpot")),
                    "themeId": (room.get("appearance") or {}).get("backgroundImage"),
                    "doors": [
                        {"index": i,
                         "direction": d.get("direction", 0),
                         "dir": DIRECTIONS.get(d.get("direction", 0), "?"),
                         "toRoomId": d.get("toRoomId"),
                         "toDoorId": d.get("toDoorId", 0),
                         "locked": bool(d.get("locked")),
                         "keyId": d.get("keyId"),
                         "pointer": f"{base}/doors/{i}"}
                        for i, d in enumerate(doors)
                    ],
                    "openDoors": [DIRECTIONS.get(d.get("direction", 0), "?") for d in doors],
                    # staticPlacements is REQUIRED (non-null) for non-cave
                    # rooms; the adapters throw on null. null and [] mean
                    # different things and the UI must not conflate them.
                    "staticPlacements": [
                        {"index": i, "kind": p.get("kind"),
                         "itemId": p.get("itemId"),
                         "lootTableId": p.get("lootTableId"),
                         "mountedEnemyId": p.get("mountedEnemyId"),
                         "count": p.get("count"),
                         "cells": p.get("cells") or [],
                         "pointer": f"{base}/staticPlacements/{i}"}
                        for i, p in enumerate(room.get("staticPlacements") or [])
                    ],
                    "staticPlacementsDeclared": room.get("staticPlacements") is not None,
                    "decorations": [
                        {"index": i,
                         "decalCategory": d.get("decalCategory"),
                         "prefabKey": d.get("prefabKey"),
                         "pointer": f"{base}/decorations/{i}"}
                        for i, d in enumerate(room.get("decorations") or [])
                    ],
                    "enemies": self.encounter_enemies(room.get("encounterId")),
                    "roomClearLoot": self.loot_table_view(room.get("lootPlanId")),
                    "cargoLoot": [
                        {"placementIndex": i,
                         "kind": p.get("kind"),
                         "pointer": f"{base}/staticPlacements/{i}/lootTableId",
                         "table": self.loot_table_view(p.get("lootTableId"))}
                        for i, p in enumerate(room.get("staticPlacements") or [])
                        if p.get("lootTableId")
                    ],
                })
            layout = self.floor_layout(floor)
            for room in rooms:
                pos = layout["positions"].get(room["roomId"])
                room["gridX"] = pos["x"] if pos else None
                room["gridY"] = pos["y"] if pos else None
                room["hasLayout"] = pos is not None
                room["parentRoomId"] = layout["subRoomParents"].get(room["roomId"])
                room["cols"] = max(1, (floor.get("rooms") or [])[room["index"]].get("roomCols") or 1)
                room["rows"] = max(1, (floor.get("rooms") or [])[room["index"]].get("roomRows") or 1)
            floors.append({
                "layout": layout,
                "index": floor.get("index", f_index),
                "pointer": f"/floors/{f_index}",
                "themeId": floor.get("themeId"),
                "startRoomId": floor.get("startRoomId"),
                "bossRoomId": floor.get("bossRoomId"),
                "rooms": rooms,
            })
        return {
            "id": doc.id,
            "displayName": doc.value.get("displayName") or doc.id,
            "file": doc.path.name,
            "floors": floors,
        }


    def floor_layout(self, floor: dict) -> dict:
        """Grid positions and door connectors for one floor.

        This mirrors ``MiniMapLayoutBuilder.BuildGridLayout`` in
        ``Game.Level`` so the editor's map and the in-game minimap agree:
        BFS the door graph from ``startRoomId``, offsetting by direction, then
        anchor whatever is left (caves, basements — reached by cave/stair
        links rather than doors) beside the room you enter them from.

        Grid Y increases upward, as it does in production. Flipping for
        screen coordinates is the renderer's job, not this function's.
        """
        rooms = floor.get("rooms") or []
        by_id = {r.get("roomId"): r for r in rooms if r.get("roomId")}
        start = floor.get("startRoomId") or (rooms[0].get("roomId") if rooms else None)

        positions: dict[str, tuple[int, int]] = {}
        sub_parents: dict[str, str] = {}
        if start and start in by_id:
            positions[start] = (0, 0)
            queue = [start]
            visited: set[str] = set()
            while queue:
                room_id = queue.pop(0)
                if room_id in visited:
                    continue
                visited.add(room_id)
                for door in by_id.get(room_id, {}).get("doors") or []:
                    target = door.get("toRoomId")
                    if not target or target in positions or target not in by_id:
                        continue
                    dx, dy = _DIRECTION_OFFSET[door.get("direction", 0)]
                    x, y = positions[room_id]
                    positions[target] = (x + dx, y + dy)
                    queue.append(target)

        self._anchor_sub_rooms(by_id, positions, sub_parents)

        # Connectors, deduplicated the way production does it: emit only from
        # the room with the smaller ordinal id, and treat a door as locked if
        # either side says so.
        connectors = []
        for room_id, room in by_id.items():
            for door in room.get("doors") or []:
                target = door.get("toRoomId")
                if not target:
                    continue
                same_floor = target in by_id
                if same_floor and room_id > target:
                    continue
                locked = bool(door.get("locked"))
                if not locked and same_floor:
                    locked = any(d.get("toRoomId") == room_id and d.get("locked")
                                 for d in by_id[target].get("doors") or [])
                connectors.append({
                    "from": room_id,
                    "to": target,
                    "locked": locked,
                    "keyId": door.get("keyId"),
                    "horizontal": _DIRECTION_OFFSET[door.get("direction", 0)][0] != 0,
                    "crossFloor": not same_floor,
                })

        return {
            "positions": {k: {"x": v[0], "y": v[1]} for k, v in positions.items()},
            "subRoomParents": sub_parents,
            "connectors": connectors,
            "startRoomId": start,
        }

    @staticmethod
    def _anchor_sub_rooms(by_id, positions, sub_parents) -> None:
        """Place rooms the door BFS never reached, beside their entry room.

        Caves and basements are entered through cave/stair links, not doors,
        so they carry no door-graph position. Production hangs them one cell
        down-and-right of their parent, bumping X for siblings.
        """
        unanchored = sorted(rid for rid in by_id if rid not in positions)
        bumps: dict[str, int] = {}
        progressed = True
        while progressed and unanchored:
            progressed = False
            remaining = []
            for sub_id in unanchored:
                parent = _find_anchor_parent(by_id, sub_id, positions)
                if parent is None:
                    remaining.append(sub_id)
                    continue
                bump = bumps.get(parent, 0)
                px, py = positions[parent]
                positions[sub_id] = (px + 1 + bump, py - 1)
                sub_parents[sub_id] = parent
                bumps[parent] = bump + 1
                progressed = True
            unanchored = remaining


_DIRECTION_OFFSET = {0: (0, 1), 1: (0, -1), 2: (-1, 0), 3: (1, 0)}


def _find_anchor_parent(by_id, room_id, positions):
    """The positioned room a cave/basement is entered from, if any."""
    room = by_id.get(room_id) or {}
    for link in room.get("caveLinks") or []:
        for side in ("roomIdA", "roomIdB"):
            other = link.get(side)
            if other and other != room_id and other in positions:
                return other
    for link in room.get("stairLinks") or []:
        other = link.get("roomIdB") or link.get("toRoomId")
        if other and other in positions:
            return other
    # Reverse lookup: someone else's link points at us.
    for other_id, other in by_id.items():
        if other_id not in positions:
            continue
        for link in (other.get("stairLinks") or []) + (other.get("caveLinks") or []):
            if room_id in (link.get("roomIdB"), link.get("toRoomId"), link.get("roomIdA")):
                return other_id
    return None


def _code(identifier: str) -> str:
    """Two-letter glyph for a catalog card, from the id's word initials."""
    parts = [p for p in identifier.replace("-", "_").split("_") if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
