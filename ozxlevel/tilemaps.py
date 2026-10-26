"""Room tilemap geometry, for the one place this tool reads TilemapData.

A room that pins a `templateId` skips `RoomTilemapQuery` entirely
(`RoomLoadManager.PreloadRoom`), so none of the checks a drawn template
gets — door mask, rail — are made on it, and nothing downstream asks again.
`Game.Tests/Level/PinnedTemplateDoorTests` is upstream's answer for shipped
data; this is the same set of questions asked at edit time, rule for rule.

Grids are `[x][y]`: the outer array is the column (`RoomTilemapData.Size`
is `(Ground.Count, Ground[0].Count)`), and the file's own `meta.width` /
`meta.height` are not trusted.
"""

from __future__ import annotations

# OpenDoors flags (GameEnums.cs), keyed by DoorDirection (Up/Down/Left/Right).
OPEN_DOOR_FLAG = {0: 1, 1: 4, 2: 8, 3: 2}
FLAG_SIDE = {1: "Top", 2: "Right", 4: "Bottom", 8: "Left"}


def door_mask(doors) -> int:
    """`RoomQueryHelper.DeriveOpenDoors`: the sides a room has doors on."""
    mask = 0
    for door in doors or []:
        mask |= OPEN_DOOR_FLAG.get((door or {}).get("direction", 0), 0)
    return mask


def sides(mask: int) -> str:
    return "+".join(name for flag, name in FLAG_SIDE.items() if mask & flag) or "none"


def size(tilemap: dict) -> tuple[int, int]:
    ground = tilemap.get("ground") or []
    return (len(ground), len(ground[0]) if ground else 0)


def door_cell(direction: int, w: int, h: int) -> tuple[int, int]:
    """Where a door stands: the midpoint of the edge it faces, on the boundary
    cell (`RoomInstanceView.DoorAnchorLocal`, as the upstream test mirrors it)."""
    return {0: (w // 2, h - 1), 1: (w // 2, 0),
            2: (0, h // 2), 3: (w - 1, h // 2)}[direction]


def _cell(tilemap: dict, layer: str, x: int, y: int) -> int:
    grid = tilemap.get(layer) or []
    if 0 <= x < len(grid):
        column = grid[x] or []
        if 0 <= y < len(column):
            return column[y] or 0
    return 0


def on_ground(tilemap: dict, x: int, y: int) -> bool:
    """`RoomTilemapData.IsOnGround`: Ground or Bridge."""
    return _cell(tilemap, "ground", x, y) > 0 or _cell(tilemap, "bridge", x, y) > 0


def walkable(tilemap: dict, x: int, y: int) -> bool:
    """Floor that is neither Static nor Pipeline.

    Pipeline is the trap: it is a solid, breakable wall on its own physics
    layer, and `RoomWalkableGrid` never looks at it — so the nav grid calls a
    pipe-sealed room fine. Static is a placement mask, not an obstacle, for
    a single doorway cell; it does block a route, which is why the upstream
    flood excludes it.
    """
    return (on_ground(tilemap, x, y)
            and _cell(tilemap, "static", x, y) <= 0
            and _cell(tilemap, "pipeline", x, y) <= 0)


def flood(tilemap: dict, start: tuple[int, int]) -> set:
    seen: set = set()
    stack = [start] if walkable(tilemap, *start) else []
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        x, y = c
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n not in seen and walkable(tilemap, *n):
                stack.append(n)
    return seen


def has_rail(tilemap: dict) -> bool:
    return any(any(cell for cell in column or []) for column in tilemap.get("rail") or [])


def pin_door_findings(room: dict, tilemap: dict) -> list[tuple[str, str, str]]:
    """(code, pointer suffix, message) for each door rule one template breaks.

    The four from `PinnedTemplateDoorTests`, in its order: the template must
    open every side the room has a door on; put floor in every doorway; keep
    the doors walkable to one another; and no pinned solid placement may
    stand in a doorway (pinned cells skip `DoorClearance`, which only guards
    count-mode fill — triggers and equipment are walked over, not stood on).
    """
    out: list[tuple[str, str, str]] = []
    # Every door, a run-exit gate included: it stands on its edge like any
    # other, and the player has to be able to walk to it.
    all_doors = [d for d in room.get("doors") or [] if d]
    mask = door_mask(all_doors)
    opens = tilemap.get("openDoors") or 0
    missing = mask & ~opens
    if missing:
        out.append(("LEVEL_PIN_DOOR_SIDE_CLOSED", "/templateId",
                    f"has doors on {sides(mask)} but the template opens {sides(opens)} — "
                    f"{sides(missing)} is a wall"))

    w, h = size(tilemap)
    if not w or not h:
        return out
    cells = []
    for door in all_doors:
        direction = door.get("direction", 0)
        cell = door_cell(direction, w, h)
        cells.append((door, cell))
        if not on_ground(tilemap, *cell):
            out.append(("LEVEL_PIN_DOORWAY_NO_FLOOR", "/templateId",
                        f"its {_dir(direction)} door stands on cell {cell}, where the "
                        f"template has no floor; whoever comes in through it arrives "
                        f"off the floor"))

    walk = cells
    if len(walk) >= 2:
        reached = flood(tilemap, walk[0][1])
        for door, cell in walk[1:]:
            if cell not in reached:
                out.append(("LEVEL_PIN_DOORS_UNCONNECTED", "/templateId",
                            f"its {_dir(door.get('direction', 0))} door {cell} cannot be "
                            f"walked to from the {_dir(walk[0][0].get('direction', 0))} door "
                            f"(floor that is not Static or Pipeline — a pipe run is a "
                            f"solid wall)"))

    door_cells = {c: d for d, c in cells}
    for pi, entry in enumerate(room.get("staticPlacements") or []):
        if not entry or not entry.get("cells") or entry.get("kind") in ("trigger", "equipment"):
            continue
        cols = entry.get("footprintCols") or 1
        rows = entry.get("footprintRows") or 1
        for anchor in entry["cells"]:
            hit = next(((anchor.get("x", 0) + dx, anchor.get("y", 0) + dy)
                        for dx in range(cols) for dy in range(rows)
                        if (anchor.get("x", 0) + dx, anchor.get("y", 0) + dy) in door_cells),
                       None)
            if hit:
                out.append(("LEVEL_PIN_PLACEMENT_IN_DOORWAY", f"/staticPlacements/{pi}",
                            f"pins {entry.get('kind')} over the "
                            f"{_dir(door_cells[hit].get('direction', 0))} doorway {hit}; "
                            f"pinned cells skip DoorClearance, so it seals the door"))
                break
    return out


def _dir(direction: int) -> str:
    return {0: "Up", 1: "Down", 2: "Left", 3: "Right"}.get(direction, str(direction))


# OZX-1467: the orientations every authored template also enters the pool
# as, unless it sets `noMirror`. All four keep the grid size, which is why a
# quarter turn is not among them. `RoomTilemapData.Mirrored` names a mirror
# `<file>@<orientation>`.
MIRROR_ORIENTATIONS = ("FlipX", "FlipY", "Rotate180")


def mirror_doors(mask: int, orientation: str) -> int:
    """`RoomTilemapData.MirrorDoors`: the sides that trade places swap bits.

    Doors sit at the edge midpoints, so a flip carries each onto the opposite
    side's door position. FlipX swaps Left/Right, FlipY Top/Bottom, Rotate180
    both; bits above the four sides are kept as they are.
    """
    flip_x = orientation in ("FlipX", "Rotate180")
    flip_y = orientation in ("FlipY", "Rotate180")
    top, right, bottom, left = (bool(mask & 1), bool(mask & 2),
                                bool(mask & 4), bool(mask & 8))
    if flip_x:
        left, right = right, left
    if flip_y:
        top, bottom = bottom, top
    return ((mask & ~0xF) | (1 if top else 0) | (2 if right else 0)
            | (4 if bottom else 0) | (8 if left else 0))


def expand(fact: dict) -> list[dict]:
    """`RoomTilemapQuery.LoadPool` for one file: it, then its three mirrors.

    Always all three, even when a symmetric layout makes some identical —
    that keeps every template at the same share of the draw. Everything but
    the door mask is the same in every orientation (a flip moves cells, it
    neither adds nor removes ground, soft edge or rail), so a mirror is the
    authored fact with its mask remapped. `family` is the authored file:
    neighbour dedup and the chapter assignment compare that, not the entry.
    """
    authored = dict(fact, orientation="Authored", family=fact["file"])
    if fact.get("noMirror"):
        return [authored]
    return [authored] + [
        dict(fact, file=f"{fact['file']}@{o}", orientation=o, family=fact["file"],
             openDoors=mirror_doors(fact["openDoors"], o))
        for o in MIRROR_ORIENTATIONS]


class TilemapIndex:
    """Facts about every tilemap in the pool, the ones `LevelPlanValidator` reads.

    `TilemapFacts` upstream: file, `roomCategory` (empty = wildcard),
    `stageType`, `openDoors`, and whether the ground leaves a cell empty
    (OZX-1340). TilemapData is replaced wholesale by the room-template sync,
    so each file's facts are kept against its stat stamp and re-read when it
    moves; a refresh costs one stat per file.

    The pool is the EXPANDED one (OZX-1467): each file not marked `noMirror`
    is four entries, as `RoomTilemapQuery.LoadPool` loads it for the runtime
    draw and for the TilemapFacts the plan validator reads. A pinned
    `templateId` still names only the authored file — see
    `Dataset.pinned_templates`, which reads files, not this pool.
    """

    def __init__(self, root):
        self.root = root
        self._facts: dict = {}

    def facts(self) -> list[dict]:
        if self.root is None or not self.root.is_dir():
            return []
        seen = {}
        for path in sorted(self.root.glob("*/*.json")):
            stamp = _file_stamp(path)
            hit = self._facts.get(path)
            if hit is None or hit[0] != stamp:
                read = _read_facts(path)
                hit = (stamp, expand(read) if read is not None else None)
            seen[path] = hit
        self._facts = seen
        return [f for _, entries in seen.values() if entries for f in entries]


def _file_stamp(path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_facts(path) -> dict | None:
    import json
    try:
        t = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    ground = t.get("ground") or []
    return {"file": path.stem, "folder": path.parent.name,
            "category": t.get("roomCategory") or "",
            "shape": t.get("roomShape") or "",
            "stageType": t.get("stageType") or "",
            "openDoors": t.get("openDoors") or 0,
            "noMirror": bool(t.get("noMirror")),
            # `HasEmptyGround`: a missing ground layer is a room with no floor.
            "hasEmptyGround": (not ground) or any(
                not column or any((cell or 0) <= 0 for cell in column) for column in ground),
            "hasSoftEdge": any((cell or 0) > 0 for column in t.get("softEdge") or []
                               for cell in column or []),
            "hasRail": has_rail(t)}


def for_theme(facts: list[dict], theme_id: str | None) -> list[dict]:
    """`LevelPlanValidator.TilemapsForTheme`: the library an area draws from.

    The theme names a category (`themes/X` → `X`); a category no tilemap
    claims falls back to `normal`; an uncategorised tilemap is a wildcard.
    """
    category = (theme_id or "normal").rsplit("/", 1)[-1] if theme_id else "normal"
    if not any(f["category"] == category for f in facts):
        category = "normal"
    return [f for f in facts if not f["category"] or f["category"] == category]


def matches_stage(tilemap_stage: str, query: str) -> bool:
    """`RoomTilemapQuery.MatchesStageType`: all / none / empty serve any stage."""
    if not query:
        return True
    if not tilemap_stage or tilemap_stage in ("all", "none"):
        return True
    return tilemap_stage == query


ALL_DOORS = 15


def candidates(facts: list[dict], stage_type: str, open_doors: int, *,
               room_shape: str | None = None, room_category: str | None = None,
               exclude_soft_edge: bool = False, require_rail: bool = False,
               require_full_ground: bool = False) -> list[dict]:
    """`RoomTilemapQuery.Candidates`: every tilemap a room may draw.

    The hard filters (category — basement is folder-scoped —, shape, stage,
    soft edge, full ground, rail), then the exact door-mask matches if there
    are any, else the superset ones. Rail alone falls back to the unfiltered
    set when it is what emptied the list (OZX-1164). An empty result is
    where upstream throws. Run over the expanded pool, so a mirror answers a
    mask its authored file does not (OZX-1467).
    """
    def category_ok(t):
        if room_category == "basement" and t["folder"] != "basement":
            return False
        return not room_category or not t["category"] or t["category"] == room_category

    def shape_ok(t):
        return not room_shape or t.get("shape") in ("", "all") or t.get("shape") == room_shape

    pool, rail_rejected = [], 0
    for t in facts:
        if not (category_ok(t) and shape_ok(t) and matches_stage(t["stageType"], stage_type)):
            continue
        if exclude_soft_edge and t.get("hasSoftEdge"):
            continue
        if require_full_ground and t["hasEmptyGround"]:
            continue
        if require_rail and not t.get("hasRail"):
            rail_rejected += 1
            continue
        pool.append(t)
    if not pool and require_rail and rail_rejected:
        return candidates(facts, stage_type, open_doors, room_shape=room_shape,
                          room_category=room_category, exclude_soft_edge=exclude_soft_edge,
                          require_full_ground=require_full_ground)
    exact = [t for t in pool if t["openDoors"] == open_doors]
    if exact:
        return exact
    return [t for t in pool if (t["openDoors"] & open_doors) == open_doors]
