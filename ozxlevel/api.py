"""Local HTTP API over GameData. Python stdlib only, no dependencies.

Binds to localhost. This is an authoring tool for one person on one machine,
not a service — there is no auth, and it must not be exposed beyond loopback.
"""

from __future__ import annotations

import json
import mimetypes
import pathlib
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .dataset import DIRECTION_IDS, DIRECTION_TWIN, Dataset, ExternallyModified
from .validate import (validate_all, validate_encounter, validate_level,
                       validate_loot, validate_plan)

WEB_ROOT = pathlib.Path(__file__).resolve().parent.parent / "web"


class Api:
    """Holds the mounted dataset and answers the editor's requests."""

    def __init__(self, project_root: pathlib.Path):
        self.project_root = project_root
        self.dataset = Dataset(project_root)

    # ── reads ────────────────────────────────────────────────────────────

    def bootstrap(self) -> dict:
        ds = self.dataset
        ds.refresh()
        return {
            "projectRoot": str(ds.root),
            "levels": ds.level_summaries(),
            "plans": [{"id": d.id, "file": d.path.name,
                       "floors": len(d.value.get("floors") or [])}
                      for d in ds.of_type("LevelBasePlanData")],
            "enemyCatalog": ds.enemy_catalog(),
            "staticCatalog": ds.static_catalog(),
            "lootCatalog": ds.loot_catalog(),
            "rarityPalette": ds.rarity_palette(),
            "encounters": ds.ids_of("EncounterData"),
            "lootTables": ds.ids_of("LootTableData"),
            "counts": {
                "levels": len(ds.of_type("LevelData")),
                "plans": len(ds.of_type("LevelBasePlanData")),
                "encounters": len(ds.of_type("EncounterData")),
                "enemies": len(ds.of_type("EnemyData")),
                "lootTables": len(ds.of_type("LootTableData")),
                "documents": len(ds.docs),
            },
            "problems": ds.problems,
        }

    def level(self, level_id: str) -> dict:
        self.dataset.refresh()
        view = self.dataset.level_view(level_id)
        view["issues"] = validate_level(self.dataset, level_id)
        return view

    def validate(self, scope: str | None, target: str | None) -> dict:
        ds = self.dataset
        ds.refresh()
        if scope == "level" and target:
            issues = validate_level(ds, target)
        elif scope == "plan" and target:
            issues = validate_plan(ds, target)
        elif scope == "encounter" and target:
            issues = validate_encounter(ds, target)
        elif scope == "loot" and target:
            issues = validate_loot(ds, target)
        else:
            issues = validate_all(ds)
        return {
            "issues": issues,
            "errors": sum(1 for i in issues if i["severity"] == "error"),
            "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        }

    # ── writes ───────────────────────────────────────────────────────────

    def edit(self, data_type: str, entity_id: str, edits: list[dict]) -> dict:
        """Apply span edits to one document and save it byte-preservingly."""
        doc = self.dataset.get(data_type, entity_id)
        if doc is None:
            raise KeyError(f"{data_type} '{entity_id}' not found")
        if not edits:
            return {"ok": True, "applied": 0}
        try:
            doc.apply(edits)
        except ExternallyModified as exc:
            self.dataset.refresh()
            return {"ok": False, "externallyModified": True, "error": str(exc)}
        return {"ok": True, "applied": len(edits), "file": str(doc.path)}

    def set_door(self, level_id: str, floor: int, room: int,
                 direction_name: str, open_: bool) -> dict:
        """Toggle one of a room's four doors, keeping the twin in step.

        Creating a door without its twin is the single most common way to
        produce a level that looks right and plays wrong, so the twin is part
        of the same write, never a follow-up the user has to remember.
        """
        ds = self.dataset
        # Deliberately NOT refreshing here. A door edit is computed from the
        # in-memory graph and addressed by array index; if the file changed
        # underneath, silently re-reading would apply the click to whatever
        # now sits at that index. Document.apply refuses instead, and the
        # client reloads and asks the user to redo it.
        doc = ds.get("LevelData", level_id)
        if doc is None:
            raise KeyError(level_id)
        direction = DIRECTION_IDS[direction_name]
        floors = doc.value["floors"]
        room_node = floors[floor]["rooms"][room]
        room_id = room_node.get("roomId")
        base = f"/floors/{floor}/rooms/{room}"
        doors = room_node.get("doors") or []

        existing = next((i for i, d in enumerate(doors)
                         if d.get("direction") == direction), None)
        edits: list[dict] = []

        if not open_:
            if existing is None:
                return {"ok": True, "applied": 0}
            target_id = doors[existing].get("toRoomId")
            edits.append({"op": "remove", "path": f"{base}/doors/{existing}"})
            twin = _find_room(floors, target_id)
            if twin:
                t_fi, t_ri, t_room = twin
                for i, d in enumerate(t_room.get("doors") or []):
                    if d.get("toRoomId") == room_id and \
                       d.get("direction") == DIRECTION_TWIN[direction]:
                        edits.append({"op": "remove",
                                      "path": f"/floors/{t_fi}/rooms/{t_ri}/doors/{i}"})
                        break
            try:
                doc.apply(edits)
            except ExternallyModified as exc:
                ds.refresh()
                return {"ok": False, "externallyModified": True, "error": str(exc)}
            return {"ok": True, "applied": len(edits)}

        if existing is not None:
            return {"ok": True, "applied": 0}

        # Opening a door needs a neighbour. Pick the room on the other side
        # of that direction if the level's grid naming makes one obvious;
        # otherwise report back and let the UI ask.
        neighbour = _guess_neighbour(floors[floor]["rooms"], room, direction)
        if neighbour is None:
            return {"ok": False, "needsTarget": True,
                    "message": f"no obvious room {direction_name} of {room_id}; "
                               f"pick a target room"}
        n_index, n_room = neighbour
        edits.append({"op": "append", "path": f"{base}/doors",
                      "value": {"direction": direction,
                                "toRoomId": n_room.get("roomId"),
                                "toDoorId": DIRECTION_TWIN[direction],
                                "locked": False}})
        edits.append({"op": "append",
                      "path": f"/floors/{floor}/rooms/{n_index}/doors",
                      "value": {"direction": DIRECTION_TWIN[direction],
                                "toRoomId": room_id,
                                "toDoorId": direction,
                                "locked": False}})
        try:
            doc.apply(edits)
        except ExternallyModified as exc:
            ds.refresh()
            return {"ok": False, "externallyModified": True, "error": str(exc)}
        return {"ok": True, "applied": len(edits),
                "linked": n_room.get("roomId")}

    def reload(self) -> dict:
        self.dataset.load()
        return self.bootstrap()


def _find_room(floors, room_id):
    for fi, floor in enumerate(floors):
        for ri, room in enumerate(floor.get("rooms") or []):
            if room.get("roomId") == room_id:
                return fi, ri, room
    return None


def _guess_neighbour(rooms, index, direction):
    """Best-effort neighbour lookup for grid-named rooms (`..._x_y`).

    Room ids in this data encode a grid position (`f0_room_0_4`), and Up
    increments y while Right increments x. When the ids do not follow that
    shape we return None rather than guessing wrong.
    """
    current = rooms[index].get("roomId") or ""
    parts = current.rsplit("_", 2)
    if len(parts) != 3:
        return None
    prefix, sx, sy = parts
    try:
        x, y = int(sx), int(sy)
    except ValueError:
        return None
    dx, dy = {0: (0, 1), 1: (0, -1), 2: (-1, 0), 3: (1, 0)}[direction]
    wanted = f"{prefix}_{x + dx}_{y + dy}"
    for i, room in enumerate(rooms):
        if room.get("roomId") == wanted:
            return i, room
    return None


# ── HTTP plumbing ────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    api: Api = None  # set by serve()

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # -- helpers
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc, status=400):
        self._json({"ok": False, "error": str(exc),
                    "detail": traceback.format_exc(limit=3)}, status)

    # -- routes
    def do_GET(self):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path == "/api/bootstrap":
                return self._json(self.api.bootstrap())
            if path.startswith("/api/level/"):
                return self._json(self.api.level(path[len("/api/level/"):]))
            if path == "/api/validate":
                return self._json(self.api.validate(
                    (query.get("scope") or [None])[0],
                    (query.get("target") or [None])[0]))
            if path == "/api/reload":
                return self._json(self.api.reload())
            return self._static(path)
        except KeyError as exc:
            return self._error(exc, 404)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc, 500)

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            return self._error(exc, 400)
        try:
            if url.path == "/api/edit":
                return self._json(self.api.edit(
                    payload["dataType"], payload["id"], payload["edits"]))
            if url.path == "/api/door":
                return self._json(self.api.set_door(
                    payload["levelId"], payload["floor"], payload["room"],
                    payload["direction"], payload["open"]))
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)
        except KeyError as exc:
            return self._error(exc, 404)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc, 500)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT)) or not target.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(project_root: pathlib.Path, port: int = 8765):
    Handler.api = Api(project_root)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, Handler.api
