"""Validation rules, weighted toward the failures that produce no error.

A broken level usually does not crash — it just quietly plays wrong. A door
whose twin is missing is a one-way door. An unknown `generatorType` becomes
`tree`. A `start` room past the first area cannot load. Those are the rules
worth having, and they are Errors, not Warnings.

Issue codes come from the TD's Appendix B; they are stable machine ids.
"""

from __future__ import annotations

import math

from . import plandefaults, tilemaps
from .dataset import (DIRECTION_TWIN, DIRECTIONS, SKILL_DROP_LEVEL_CAP_DEFAULT,
                      Dataset, _stage_budget, banned_stages, countmin_reading)

GENERATOR_TYPES = {"tree", "cycle", "mixed", "static"}
STAGE_TYPES = {"start", "teaching", "building", "default",
               "pressure", "peak", "release", "boss", "exit",
               # OZX-1171: a room that only moves the story on. Hand-placed in
               # the teaching chapters; no plan deals it.
               "story"}
ROOM_CATEGORIES = {"normal", "basement", "cave", "test"}


def _issue(severity, code, message, *, entity=None, pointer=None, fix=None):
    return {"severity": severity, "code": code, "message": message,
            "entity": entity, "pointer": pointer, "fix": fix}


def validate_level(ds: Dataset, level_id: str) -> list[dict]:
    doc = ds.get("LevelData", level_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING",
                       f"level '{level_id}' not found")]

    issues: list[dict] = []
    floors = doc.value.get("floors") or []

    # roomId -> (floor index, room index)
    locations: dict[str, tuple[int, int]] = {}
    for fi, floor in enumerate(floors):
        for ri, room in enumerate(floor.get("rooms") or []):
            rid = room.get("roomId")
            if not rid:
                issues.append(_issue("error", "LEVEL_ROOM_ID_MISSING",
                                     f"floor {fi} room {ri} has no roomId",
                                     entity=level_id,
                                     pointer=f"/floors/{fi}/rooms/{ri}"))
                continue
            if rid in locations:
                # OZX-1116: `FloorGraph.Build(LevelData)` indexes every area's
                # rooms in one flat table and throws on a clash, so a roomId
                # has to be unique across the LEVEL, not just its area.
                other = locations[rid][0]
                issues.append(_issue("error", "LEVEL_ROOM_ID_DUPLICATE",
                                     f"duplicate roomId '{rid}'"
                                     + (f" (also on area {other}; room ids are one "
                                        f"namespace across the level)"
                                        if other != fi else ""),
                                     entity=level_id,
                                     pointer=f"/floors/{fi}/rooms/{ri}/roomId"))
            locations[rid] = (fi, ri)

    for fi, floor in enumerate(floors):
        rooms = floor.get("rooms") or []
        fptr = f"/floors/{fi}"

        start = floor.get("startRoomId")
        if not start:
            issues.append(_issue("error", "LEVEL_START_ROOM_MISSING",
                                 f"floor {fi} has no startRoomId",
                                 entity=level_id, pointer=f"{fptr}/startRoomId"))
        elif start not in {r.get("roomId") for r in rooms}:
            issues.append(_issue("error", "LEVEL_START_ROOM_MISSING",
                                 f"floor {fi} startRoomId '{start}' is not a room on this floor",
                                 entity=level_id, pointer=f"{fptr}/startRoomId"))

        for ri, room in enumerate(rooms):
            rid = room.get("roomId")
            rptr = f"{fptr}/rooms/{ri}"
            category = room.get("roomCategory")

            if category and category not in ROOM_CATEGORIES:
                issues.append(_issue("warning", "TYPE_VALUE_UNKNOWN",
                                     f"{rid}: unknown roomCategory '{category}'",
                                     entity=level_id, pointer=f"{rptr}/roomCategory"))

            stage = room.get("stageType")
            if stage is not None and stage not in STAGE_TYPES:
                issues.append(_issue("warning", "TYPE_VALUE_UNKNOWN",
                                     f"{rid}: unknown stageType '{stage}'",
                                     entity=level_id, pointer=f"{rptr}/stageType"))
            elif stage == "start" and fi > 0:
                # OZX-1116: areas join seamlessly, so a later area's entry room
                # is walked into, not spawned in — and `start` has only mask-1
                # tilemaps, so the return door makes it mask 5, which no start
                # template has and no superset falls back to. RoomTilemapQuery
                # throws and the room never loads.
                issues.append(_issue(
                    "error", "LEVEL_START_STAGE_NOT_FIRST_AREA",
                    f"{rid}: stageType 'start' on area {fi}. Only area 0 is where "
                    f"the run begins; a later area's entry room is walked into, and "
                    f"`start` has only single-door (mask 1) tilemaps, so its return "
                    f"door leaves nothing to load. Use teaching or building",
                    entity=level_id, pointer=f"{rptr}/stageType"))

            # OZX-1159 renamed the ride from a steam train to a rail cart. As
            # with `dropTableIdOverride`, the old name is no alias: JsonUtility
            # drops an undeclared field silently, so the room just has no cart.
            if "steamTrain" in room:
                issues.append(_issue(
                    "error", "LEVEL_ROOM_FIELD_RENAMED",
                    f"{rid}: `steamTrain` was renamed `railCart` (OZX-1159); the old "
                    f"name is ignored without a word, so this room gets no cart",
                    entity=level_id, pointer=f"{rptr}/steamTrain"))

            issues.extend(_pinned_rail_issues(ds, level_id, rid, rptr, room))
            issues.extend(_full_ground_issues(level_id, rid, rptr, room,
                                              bool(floor.get("requireFullGround"))))
            issues.extend(_pinned_door_issues(ds, level_id, rid, rptr, room))
            issues.extend(_decal_issues(ds, level_id, rid, rptr, room,
                                        floor.get("themeId")))
            if "exitBlockedTip" in room:
                issues.extend(_tip_issues(
                    level_id, f"{rid}: exitBlockedTip", f"{rptr}/exitBlockedTip",
                    room["exitBlockedTip"],
                    shown=bool(room.get("requiredForExitItemIds")),
                    never_shown="the room has no requiredForExitItemIds, so its "
                                "exit never refuses anyone"))

            # Non-cave rooms MUST declare staticPlacements. Every spawn adapter
            # calls RequireDeclared and throws on null — an empty array is the
            # way to say "this room places nothing".
            if category != "cave" and room.get("staticPlacements") is None:
                issues.append(_issue("error", "ROOM_STATIC_PLACEMENTS_UNDECLARED",
                                     f"{rid}: staticPlacements is null; non-cave rooms must declare "
                                     f"at least an empty array or the spawn adapters throw",
                                     entity=level_id, pointer=f"{rptr}/staticPlacements",
                                     fix={"op": "set", "path": f"{rptr}/staticPlacements",
                                          "value": [], "label": "Declare empty []"}))

            # A bossId alone drives nothing — EncounterRunner needs an encounter.
            if room.get("bossId") and not room.get("encounterId"):
                issues.append(_issue("error", "LEVEL_BOSS_WITHOUT_ENCOUNTER",
                                     f"{rid}: bossId is set but there is no encounterId; "
                                     f"bossId alone does not drive EncounterRunner",
                                     entity=level_id, pointer=f"{rptr}/bossId"))

            # OZX-1381 deleted the room-clear channel. JsonUtility drops the
            # field unread, so a room still naming one drops nothing on clear.
            if room.get("lootPlanId"):
                issues.append(_issue(
                    "error", "LEVEL_FIELD_REMOVED",
                    f"{rid}: lootPlanId '{room['lootPlanId']}' — the room-clear loot channel "
                    f"was removed (OZX-1381) and the field is dropped unread. Put the table on "
                    f"a cargo entry's lootTableId, or the prize in guaranteedDrops",
                    entity=level_id, pointer=f"{rptr}/lootPlanId"))
            issues.extend(_room_pin_issues(ds, level_id, rid, rptr, room))
            for name, dtype in (("encounterId", "EncounterData"),
                                ("bossId", "EnemyData")):
                ref = room.get(name)
                if ref and ds.get(dtype, ref) is None:
                    issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                                         f"{rid}: {name} '{ref}' does not exist",
                                         entity=level_id, pointer=f"{rptr}/{name}"))

            for pi, p in enumerate(room.get("staticPlacements") or []):
                pptr = f"{rptr}/staticPlacements/{pi}"
                for name, dtype in (("lootTableId", "LootTableData"),
                                    ("mountedEnemyId", "EnemyData"),
                                    ("itemId", "ItemData"),
                                    ("skillId", "SkillData")):
                    ref = p.get(name)
                    if ref and ds.get(dtype, ref) is None:
                        issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                                             f"{rid}: placement {pi} {name} '{ref}' does not exist",
                                             entity=level_id,
                                             pointer=f"{pptr}/{name}"))

                # LevelValidator.ValidateStaticPlacementPolicy throws on load.
                # Floor-wide defaults are filtered silently instead; only a
                # room that names the entry itself can be wrong about it.
                if stage in banned_stages(p):
                    what = (f"{p.get('kind')} ({p['prefabKey']})" if p.get("prefabKey")
                            else p.get("kind"))
                    issues.append(_issue(
                        "error", "ROOM_PLACEMENT_STAGE_BANNED",
                        f"{rid}: placement {pi} is a '{what}' in a '{stage}' room, which "
                        f"StaticPlacementPolicy keeps out of that stage (the start room is "
                        f"where the player spawns; a release room is the breather after a "
                        f"peak) — the level refuses to load. Put it in another room",
                        entity=level_id, pointer=pptr))
                issues.extend(_placement_count_issues(level_id, rid, pi, pptr, p))
                issues.extend(_cargo_issues(level_id, rid, pi, pptr, p,
                                            bool(room.get("floorLootTableId"))))
                issues.extend(_cargo_id_issues(ds, level_id, rid, pi, pptr, p))

                # SkillPlatformSpawnAdapter throws on both of these when the
                # room spawns, so an unfinished skill platform takes the whole
                # room down rather than quietly placing nothing.
                if p.get("kind") == "skill":
                    # OZX-1400: touching the platform grants or refills this
                    # many uses, and the adapter throws without one.
                    if (p.get("charges") or 0) <= 0:
                        issues.append(_issue(
                            "error", "ROOM_PLACEMENT_SKILL_NO_CHARGES",
                            f"{rid}: placement {pi} is a skill platform with charges "
                            f"{p.get('charges') or 0}; it must author charges > 0",
                            entity=level_id, pointer=f"{pptr}/charges" if "charges" in p else pptr))
                    if "buffDurationSeconds" in p:
                        issues.append(_issue(
                            "warning", "ROOM_PLACEMENT_FIELD_UNREAD",
                            f"{rid}: placement {pi} sets buffDurationSeconds, which a skill "
                            f"platform no longer reads (OZX-1400: it grants charges of an "
                            f"active skill instead)",
                            entity=level_id, pointer=f"{pptr}/buffDurationSeconds"))
                    skill = ds.get("SkillData", p.get("skillId") or "")
                    if not p.get("skillId"):
                        issues.append(_issue("error", "ROOM_PLACEMENT_SKILL_MISSING",
                                             f"{rid}: placement {pi} is kind 'skill' with no "
                                             f"skillId; the spawn adapter throws",
                                             entity=level_id, pointer=f"{pptr}/skillId"))
                    elif skill is not None:
                        problem = _platform_skill_problem(ds, skill)
                        if problem:
                            issues.append(_issue("error", problem[0],
                                                 f"{rid}: placement {pi} grants "
                                                 f"'{p['skillId']}', {problem[1]}",
                                                 entity=level_id, pointer=f"{pptr}/skillId"))

            # ── doors ────────────────────────────────────────────────────
            seen_dirs: dict[int, int] = {}
            for di, door in enumerate(room.get("doors") or []):
                dptr = f"{rptr}/doors/{di}"
                direction = door.get("direction", 0)
                target = door.get("toRoomId")

                if direction in seen_dirs:
                    issues.append(_issue("error", "LEVEL_DOOR_DIRECTION_DUPLICATE",
                                         f"{rid}: two doors face {DIRECTIONS.get(direction, direction)}",
                                         entity=level_id, pointer=dptr))
                seen_dirs[direction] = di

                if "lockedTip" in door:
                    issues.extend(_tip_issues(
                        level_id, f"{rid}: door {di} lockedTip", f"{dptr}/lockedTip",
                        door["lockedTip"],
                        shown=bool(door.get("locked") and door.get("keyId")),
                        never_shown="the door is not locked with a keyId, so it "
                                    "never refuses anyone for want of a key"))
                if door.get("exitsRun"):
                    issues.extend(_run_exit_issues(level_id, rid, di, dptr, door, room))
                    continue
                if not target:
                    issues.append(_issue("error", "LEVEL_DOOR_TARGET_MISSING",
                                         f"{rid}: door {di} has no toRoomId",
                                         entity=level_id, pointer=dptr))
                    continue
                if target not in locations:
                    issues.append(_issue("error", "LEVEL_DOOR_TARGET_MISSING",
                                         f"{rid}: door {di} points at unknown room '{target}'",
                                         entity=level_id, pointer=dptr))
                    continue

                t_fi, t_ri = locations[target]
                twin_dir = DIRECTION_TWIN[direction]
                t_room = floors[t_fi]["rooms"][t_ri]
                twins = [d for d in (t_room.get("doors") or [])
                         if d.get("direction") == twin_dir and d.get("toRoomId") == rid]
                if not twins:
                    issues.append(_issue(
                        "error", "LEVEL_DOOR_TWIN_MISSING",
                        f"{rid}: door {DIRECTIONS.get(direction)}→{target} has no twin "
                        f"{DIRECTIONS.get(twin_dir)}→{rid}; this is a silent one-way door",
                        entity=level_id, pointer=dptr,
                        fix={"op": "append",
                             "path": f"/floors/{t_fi}/rooms/{t_ri}/doors",
                             "value": {"direction": twin_dir, "toRoomId": rid,
                                       "toDoorId": direction, "locked": False},
                             "label": "Create twin door"}))

                # A door into another area is a SEAM, not a dead end (OZX-1116):
                # the graph is built over the whole level, areas sit side by
                # side, and the join is an ordinary twin pair — checked above.
                # `stairLinks` are for drilling down into a cave or basement.
                #
                # `toDoorId` picks the arrival door in the target room. A wrong
                # one finds no door on that side and costs an entry-point bias
                # (DoorService), so it is a warning; omitted it is not checked,
                # since the enum's 0 is indistinguishable from "unset".
                if twins and "toDoorId" in door and door["toDoorId"] != twin_dir:
                    issues.append(_issue(
                        "warning", "LEVEL_DOOR_ARRIVAL_MISMATCH",
                        f"{rid}: door {DIRECTIONS.get(direction)}→{target} arrives at "
                        f"toDoorId {DIRECTIONS.get(door['toDoorId'], door['toDoorId'])}, "
                        f"but the twin door is on the {DIRECTIONS.get(twin_dir)} side",
                        entity=level_id, pointer=f"{dptr}/toDoorId",
                        fix={"op": "set", "path": f"{dptr}/toDoorId",
                             "value": twin_dir, "label": "Arrive at the twin"}))

                # The generator never locks a seam (OZX-1117), and the reason
                # holds for a hand-written one: a key is consumed when spent,
                # so locking the one edge out of an area shuts a player who
                # walked back in out of the rest of the run. The gate belongs
                # on a door INSIDE the area, as chapter_2 does with key_silver.
                if t_fi != fi and door.get("locked"):
                    issues.append(_issue(
                        "warning", "LEVEL_SEAM_DOOR_LOCKED",
                        f"{rid}: the seam door into area {t_fi} ('{target}') is "
                        f"locked. A spent key is gone, so a player who comes back "
                        f"through it cannot leave again; lock a door inside area "
                        f"{fi} on the way to this room instead",
                        entity=level_id, pointer=f"{dptr}/locked"))

                if door.get("locked") and not door.get("keyId"):
                    issues.append(_issue("warning", "LEVEL_DOOR_LOCKED_NO_KEY",
                                         f"{rid}: door {di} is locked but names no keyId",
                                         entity=level_id, pointer=dptr))

        issues.extend(_stair_link_issues(level_id, floors, fi))

        # ── reachability from the start room, over doors only ────────────
        if start:
            by_id = {r.get("roomId"): r for r in rooms}
            seen = set()
            queue = [start]
            while queue:
                current = queue.pop()
                if current in seen or current not in by_id:
                    continue
                seen.add(current)
                for door in by_id[current].get("doors") or []:
                    nxt = door.get("toRoomId")
                    if nxt and nxt not in seen:
                        queue.append(nxt)
            for ri, room in enumerate(rooms):
                rid = room.get("roomId")
                if rid and rid not in seen:
                    issues.append(_issue("error", "LEVEL_ROOM_UNREACHABLE",
                                         f"{rid}: not reachable from start room '{start}'",
                                         entity=level_id,
                                         pointer=f"{fptr}/rooms/{ri}"))
    return issues


def _stair_link_issues(level_id: str, floors: list, fi: int) -> list[dict]:
    """`LevelValidator.ValidateStairLinks`, which throws at level load.

    Since OZX-1116 stairs are for going down into a basement or a cave and
    back; areas side by side join with doors. Each link is one direction,
    `{caveId, roomIdA, roomIdB, targetFloor}`, and the rules are:

    - `roomIdA` is a room on the link's own floor;
    - `targetFloor` is another floor of the level;
    - the target floor holds the twin (`roomIdA`/`roomIdB` swapped,
      `targetFloor` back here) — without it the trip is one way;
    - the two floors stand at different `elevation`s (OZX-1338), since the
      stair shows Up or Down from them and nothing else;
    - a room holds ONE stair end (OZX-1188): `PlaceStairs` skips a second
      stair in a room that has one, so the extra link loads as a one-way
      trip. Cave-themed floors place their own and are exempt.
    """
    floor = floors[fi]
    links = floor.get("stairLinks") or []
    if not links:
        return []
    issues: list[dict] = []
    here = {r.get("roomId") for r in floor.get("rooms") or []}
    seen: dict = {}
    cave = "cave" in (floor.get("themeId") or "")
    for li, link in enumerate(links):
        if not link:
            continue
        lptr = f"/floors/{fi}/stairLinks/{li}"
        name = link.get("caveId") or f"stairLinks[{li}]"
        a, b, target = link.get("roomIdA"), link.get("roomIdB"), link.get("targetFloor", 0)
        if a not in here:
            issues.append(_issue(
                "error", "LEVEL_STAIR_ROOM_NOT_ON_FLOOR",
                f"stair '{name}' on floor {fi} has roomIdA '{a}', which is not a room of "
                f"floor {fi}; a stair's roomIdA is on its own floor",
                entity=level_id, pointer=f"{lptr}/roomIdA"))
        if not (0 <= target < len(floors)) or target == fi:
            issues.append(_issue(
                "error", "LEVEL_STAIR_TARGET_INVALID",
                f"stair '{name}' on floor {fi} targets floor {target}; a stair crosses to "
                f"another floor of the level (0..{len(floors) - 1})",
                entity=level_id, pointer=f"{lptr}/targetFloor"))
            continue
        twin = any(t and t.get("targetFloor", 0) == fi and t.get("roomIdA") == b
                   and t.get("roomIdB") == a
                   for t in floors[target].get("stairLinks") or [])
        if not twin:
            issues.append(_issue(
                "error", "LEVEL_STAIR_TWIN_MISSING",
                f"stair '{name}' ({a} → {b}, floor {fi} → {target}) has no twin on floor "
                f"{target}; the way back needs roomIdA '{b}', roomIdB '{a}', targetFloor "
                f"{fi}",
                entity=level_id, pointer=lptr,
                fix={"op": "append", "path": f"/floors/{target}/stairLinks",
                     "value": {"caveId": f"{link.get('caveId') or 'stair'}_back",
                               "roomIdA": b, "roomIdB": a, "targetFloor": fi},
                     "label": "Create the return stair"}
                if "stairLinks" in floors[target] else None))
        if (floor.get("elevation") or 0) == (floors[target].get("elevation") or 0):
            issues.append(_issue(
                "error", "LEVEL_STAIR_NO_DIRECTION",
                f"stair '{name}' joins floors {fi} and {target}, which both stand at "
                f"elevation {floor.get('elevation') or 0}; give a basement a negative "
                f"elevation and an upper floor a positive one",
                entity=level_id, pointer=lptr))
        if not cave and a in seen:
            issues.append(_issue(
                "error", "LEVEL_STAIR_ROOM_TWO_ENDS",
                f"room '{a}' on floor {fi} holds two stair ends ('{seen[a]}' and '{name}'); "
                f"a room places one stair and skips the rest, so the second is a one-way "
                f"trip",
                entity=level_id, pointer=lptr))
        seen.setdefault(a, name)
    return issues


# RoomTips.MaxTipLength: one line of 56pt bold in an 860px banner.
TIP_MAX_LENGTH = 25


def _tip_issues(level_id: str, where: str, pointer: str, tip,
                *, shown: bool, never_shown: str) -> list[dict]:
    """A HUD tip — `DoorLinkData.lockedTip` / `RoomNodeData.exitBlockedTip`.

    OZX-1168's banner tells a player why a door will not let them through.
    `RoomTips.Validated` THROWS on a tip over 25 characters rather than
    cutting it, because half a sentence naming which key to find is worse
    than none; the shipped-data guard refuses a tip that is present but
    blank, because the way to say nothing is to leave the key out. The game
    fonts (Play-Bold / Aldrich-Regular) carry no CJK glyphs, so anything past
    Latin draws as missing boxes — a warning, since nothing throws.
    """
    text = tip if isinstance(tip, str) else ""
    if not text.strip():
        return [_issue(
            "error", "LEVEL_TIP_BLANK",
            f"{where} is present but blank; omit the field to show no tip",
            entity=level_id, pointer=pointer,
            fix={"op": "remove", "path": pointer, "label": "Remove the empty tip"})]
    issues: list[dict] = []
    if len(text) > TIP_MAX_LENGTH:
        issues.append(_issue(
            "error", "LEVEL_TIP_TOO_LONG",
            f"{where} is {len(text)} characters, over the {TIP_MAX_LENGTH}-character "
            f"banner: \"{text}\". RoomTips throws rather than truncating",
            entity=level_id, pointer=pointer))
    foreign = sorted({c for c in text if ord(c) > 0x24F or not c.isprintable()})
    if foreign:
        issues.append(_issue(
            "warning", "LEVEL_TIP_GLYPH_MISSING",
            f"{where} uses {''.join(foreign)!r}, which the banner fonts cannot draw; "
            f"write it in Latin characters",
            entity=level_id, pointer=pointer))
    if not shown:
        issues.append(_issue(
            "warning", "LEVEL_TIP_NEVER_SHOWN",
            f"{where} is written, but {never_shown}",
            entity=level_id, pointer=pointer))
    return issues


# `DecalSurface.StampCategory`'s cases — anything else throws. The last five
# are the BattleField props (OZX-1268, OZX-1274). `abandoned_cable` exists in
# the factory config but is deliberately NOT authorable: cables hang off the
# room's devices and take no count (OZX-962).
DECAL_CATEGORIES = ("ground_crack", "ground_utility_by_edge", "edge_crack",
                    "floor_static_2x2", "ground_blood", "ground_crater",
                    "ground_scrub", "ground_gravel", "ground_rubble",
                    "ground_stones_by_edge")


def decal_category_issues(ds: Dataset, entity: str, where: str, pointer: str,
                          category: str, theme_id: str | None) -> list[dict]:
    """One `decalCategory`: a known name, and one this theme can draw.

    The level says how much, the room theme says what it looks like. An
    unknown name throws when the room is dressed; a known name the theme's
    `stainConfig` lacks draws nothing, with no fallback sprite — which is
    the only outcome of writing a BattleField prop on a factory floor.
    """
    if category not in DECAL_CATEGORIES:
        why = (" — cables are placed around the room's devices, not authored"
               if category == "abandoned_cable" else "")
        return [_issue(
            "error", "ROOM_DECAL_CATEGORY_UNKNOWN",
            f"{where} names decal category '{category}'{why}; DecalSurface throws on "
            f"anything but {', '.join(DECAL_CATEGORIES)}",
            entity=entity, pointer=pointer)]
    drawable = ds.themes.decal_categories(theme_id)
    if drawable["found"] and category not in drawable["names"]:
        return [_issue(
            "warning", "ROOM_DECAL_CATEGORY_NOT_IN_THEME",
            f"{where} scatters '{category}', which theme '{theme_id}' has no sprites for, "
            f"so nothing is drawn",
            entity=entity, pointer=pointer)]
    return []


def _decal_issues(ds: Dataset, level_id: str, rid: str, rptr: str, room: dict,
                  theme_id: str | None) -> list[dict]:
    """`decorations[].decalCategory` (OZX-967), with the room's theme.

    The theme is the floor's (`RoomServiceAdapter.ThemeIdForRoom`); a room
    has no theme of its own.
    """
    issues: list[dict] = []
    for di, deco in enumerate(room.get("decorations") or []):
        category = (deco or {}).get("decalCategory")
        if category:
            issues.extend(decal_category_issues(
                ds, level_id, f"{rid}: decoration {di}",
                f"{rptr}/decorations/{di}/decalCategory", category, theme_id))
    return issues


def _pinned_door_issues(ds: Dataset, level_id: str, rid: str, rptr: str,
                        room: dict) -> list[dict]:
    """Can the player get through a pinned template's doors? (OZX-1171)

    A pin skips `RoomTilemapQuery`, so a template whose sides, floor or
    walls disagree with the room's doors is accepted in silence and the room
    reads as ordinary JSON. ch2's first story room was exactly that: its
    lanes ran on Pipeline, which is solid, so the doors never connected.
    `PinnedTemplateDoorTests` guards shipped data; these are the same rules
    at edit time (see `ozxlevel.tilemaps.pin_door_findings`).

    Caves and composites are out of scope, as they are upstream: a cave has
    no tilemap, and a composite places its doors on the stitched grid, not
    on the pinned tile. A name that resolves to no tilemap at all throws at
    room load. Since OZX-1571 an id names exactly one file.
    """
    template = room.get("templateId")
    if not template or room.get("roomCategory") == "cave":
        return []
    if (room.get("roomCols") or 1) > 1 or (room.get("roomRows") or 1) > 1:
        return []
    copies = ds.pinned_templates(template)
    if not copies:
        if ds.tilemap_data is None or not ds.tilemap_data.is_dir():
            return []
        # OZX-1571: ids carry their folder now and bare names are not kept
        # compatible. A bare name that a file still has is the one mistake
        # worth naming — and fixing, when only one folder holds it.
        meant = ds.pin_id_candidates(template)
        hint = (f" — since OZX-1571 a template id carries its folder: "
                f"{' or '.join(repr(m) for m in meant)}" if meant else "")
        return [_issue(
            "error", "LEVEL_PIN_TEMPLATE_MISSING",
            f"{rid}: templateId '{template}' names no tilemap; RoomLoadManager "
            f"throws when the room loads{hint}",
            entity=level_id, pointer=f"{rptr}/templateId",
            fix=({"op": "set", "path": f"{rptr}/templateId", "value": meant[0],
                  "label": f"Pin {meant[0]}"} if len(meant) == 1 else None))]
    return [_issue("error", code, f"{rid}: pinned '{template}' {message}",
                   entity=level_id, pointer=f"{rptr}{suffix}")
            for code, suffix, message in tilemaps.pin_door_findings(room, copies[0])]


def _full_ground_issues(level_id: str, rid: str, rptr: str, room: dict,
                        area_wide: bool) -> list[dict]:
    """`requireFullGround` (OZX-1265 per room, OZX-1340 per area).

    A HARD filter in `RoomTilemapQuery`: only templates whose ground layer
    has no empty cell, and none for the door mask is a throw. Two shapes
    the query never sees:

    - a composite room (`roomCols/roomRows` > 1) is stitched by
      `CompositeRoomBuilder`, which has no full-ground filter, so
      `RoomLoadManager` throws on one that asks — its own flag or its
      area's;
    - a pinned `templateId` skips the query, so the flag does nothing.
    """
    own = bool(room.get("requireFullGround"))
    if not (own or area_wide):
        return []
    composite = (room.get("roomCols") or 1) > 1 or (room.get("roomRows") or 1) > 1
    if composite and room.get("roomCategory") != "cave" and not room.get("templateId"):
        return [_issue(
            "error", "ROOM_FULL_GROUND_COMPOSITE",
            f"{rid}: requires full ground ({'its own requireFullGround' if own else 'its area does'}) "
            f"but is a {room.get('roomCols') or 1}x{room.get('roomRows') or 1} composite room; "
            f"the composite builder has no full-ground filter and RoomLoadManager throws",
            entity=level_id,
            pointer=f"{rptr}/requireFullGround" if own else f"{rptr}/roomCols")]
    if own and room.get("templateId"):
        return [_issue(
            "warning", "ROOM_FULL_GROUND_PINNED",
            f"{rid}: requireFullGround does nothing on a pinned templateId "
            f"('{room['templateId']}') — the pin skips the template query it filters",
            entity=level_id, pointer=f"{rptr}/requireFullGround")]
    return []


def _pinned_rail_issues(ds: Dataset, level_id: str, rid: str, rptr: str,
                        room: dict) -> list[dict]:
    """A pinned template has to carry the track the room asks for (OZX-1164).

    A pinned `templateId` is taken as is: `RoomTilemapSelector` and
    `RoomLoadManager` never run it through `RoomTilemapQuery`, so the rail
    filter `requireRail` normally applies is skipped. The two flags then
    fail differently:

    - `railCart` (a cart parked here) throws in `RailCartSpawnAdapter` when
      the room has no traceable rail — an error;
    - `requireRail` (track for a cart riding through) is only a request to
      the filter, so the ride simply ends at this room — a warning, and the
      one nobody sees until they ride into it.

    Caves are skipped, as the adapter skips them.
    """
    template = room.get("templateId")
    if not template or room.get("roomCategory") == "cave":
        return []
    if not (room.get("railCart") or room.get("requireRail")):
        return []
    if ds.pinned_template_rail(template) is not False:
        return []
    if room.get("railCart"):
        return [_issue(
            "error", "LEVEL_ROOM_RAIL_MISSING",
            f"{rid}: railCart parks a cart here, but the pinned template "
            f"'{template}' has no rail layer; RailCartSpawnAdapter throws",
            entity=level_id, pointer=f"{rptr}/templateId")]
    return [_issue(
        "warning", "LEVEL_ROOM_RAIL_MISSING",
        f"{rid}: requireRail asks for track, but the pinned template "
        f"'{template}' has none. A pin skips the rail filter, so a cart that "
        f"rides in stops here",
        entity=level_id, pointer=f"{rptr}/templateId")]


def _run_exit_issues(level_id: str, rid: str, di: int, dptr: str,
                     door: dict, room: dict) -> list[dict]:
    """A door with `exitsRun` leads out of the run, not to a room.

    Walking through it is the victory (`RunExitEnteredEvent` →
    `SessionVictoryEvent`), and it is not an edge of the graph —
    `LeadsToRoom` is false for it, so no target, twin or reachability rule
    applies. What does apply, from `Game.Level/README.md`:

    - it names no room: a `toRoomId` as well is a fail-fast, because a door
      either leaves or goes somewhere;
    - it is gated like any door by `RefreshRoomGateStates` — it opens when
      the room is clear — so a lock and key on it only gets in the way;
    - `isFinalRoom` on the same room wins first (the run ends the moment the
      room clears), and the gate becomes decoration.
    """
    issues: list[dict] = []
    if door.get("toRoomId"):
        issues.append(_issue(
            "error", "LEVEL_DOOR_EXIT_HAS_TARGET",
            f"{rid}: door {di} exits the run and also names toRoomId "
            f"'{door['toRoomId']}'; a door leaves or goes somewhere, never both",
            entity=level_id, pointer=f"{dptr}/toRoomId"))
    if door.get("locked") or door.get("keyId"):
        issues.append(_issue(
            "warning", "LEVEL_DOOR_EXIT_LOCKED",
            f"{rid}: door {di} exits the run and is locked. The gate already "
            f"stays shut until the room is clear, so a key adds nothing but a "
            f"way to never finish",
            entity=level_id, pointer=dptr))
    if room.get("isFinalRoom"):
        issues.append(_issue(
            "warning", "LEVEL_DOOR_EXIT_WITH_FINAL_ROOM",
            f"{rid}: is isFinalRoom AND has a run-exit door. isFinalRoom ends the "
            f"run the moment the room clears, so the exit is never reached — pick "
            f"one",
            entity=level_id, pointer=dptr))
    return issues


def _cargo_id_issues(ds: Dataset, entity: str, where: str, pi: int, pptr: str,
                     p: dict) -> list[dict]:
    """`cargoId` (OZX-1468): the CargoData every crate of a cargo entry uses.

    Omitted is the ordinary choice. Named, it is the only way an
    `authoredOnly` crate (cargo_premium) appears. Both failures throw when
    the room spawns: on any other kind (`AuthoredStaticPlacementsLogic.
    ValidateCargoIds`), and an id no CargoData has
    (`CargoVariantLogic.ResolveNamed`).
    """
    cargo_id = p.get("cargoId")
    if not cargo_id:
        return []
    if p.get("kind") != "cargo":
        return [_issue(
            "error", "ROOM_PLACEMENT_CARGO_ID_MISPLACED",
            f"{where}: placement {pi} sets cargoId '{cargo_id}' on kind "
            f"'{p.get('kind')}'; only kind 'cargo' reads it, and the room throws on spawn",
            entity=entity, pointer=f"{pptr}/cargoId")]
    if ds.get("CargoData", cargo_id) is None:
        return [_issue(
            "error", "DATA_REFERENCE_MISSING",
            f"{where}: placement {pi} cargoId '{cargo_id}' does not exist (GameData/cargo/)",
            entity=entity, pointer=f"{pptr}/cargoId")]
    return []


def _cargo_issues(entity: str, where: str, pi: int, pptr: str, p: dict,
                  has_floor_table: bool) -> list[dict]:
    """A `cargo` entry's crates: where their loot comes from, and their size.

    OZX-1348: every crate rolls its OWN entry's `lootTableId`, falling back
    to the room's `floorLootTableId`, and `CargoSpawnAdapter` throws when
    neither is there — for count and pinned crates alike. (Before it, only
    the first count entry with a table counted, and several count entries
    could not each carry a mount; neither restriction remains.) On a
    hand-written level `floorLootTableId` never exists — it is the
    generator's `[NonSerialized]` stamp — and on a plan it exists exactly
    when the floor has probabilistic loot to synthesise it from.

    The adapter also only supports a 1×1 crate.
    """
    if p.get("kind") != "cargo":
        return []
    issues: list[dict] = []
    if not p.get("lootTableId") and not has_floor_table:
        issues.append(_issue(
            "error", "ROOM_CARGO_LOOT_SOURCE_MISSING",
            f"{where}: placement {pi} is cargo with no lootTableId, and there is no floor "
            f"loot table to fall back to (a hand-written room never has one; a plan floor "
            f"has one only with floorLootPlan.probabilistic). CargoSpawnAdapter throws",
            entity=entity, pointer=pptr))
    if (p.get("footprintCols") or 1) > 1 or (p.get("footprintRows") or 1) > 1:
        issues.append(_issue(
            "error", "ROOM_CARGO_FOOTPRINT_INVALID",
            f"{where}: placement {pi} is a {p.get('footprintCols') or 1}x"
            f"{p.get('footprintRows') or 1} cargo; CargoSpawnAdapter only places 1×1 crates "
            f"and throws on anything larger",
            entity=entity, pointer=pptr))
    return issues


# OZX-1057: only these adapters read `chance`; anything else throws rather
# than silently appearing every time (`ChanceHonouringKinds`; groundlaser
# since OZX-1377). `countMin` is narrower still — see below.
CHANCE_HONOURING_KINDS = {"prop", "groundlaser"}
# Who reads `countMin`, and how, is `countmin_reading` (dataset.py): a floor
# on the side-wall prop fill, a per-room range on toxicbarrel, groundlaser
# and — since OZX-1465 — a prop off the wall; nothing else reads it.

# GroundLaserPlacementLogic: lasers per room, placed in left-right pairs on
# fixed cells of a 10×16 room.
GROUNDLASER_MIN, GROUNDLASER_MAX = 2, 4


def _placement_count_issues(level_id: str, rid: str, pi: int,
                            pptr: str, p: dict) -> list[dict]:
    """`chance` and `countMin` on one static placement (OZX-1057).

    `count` has always been a ceiling — random fill places what the room can
    hold. These are the two ends that grew around it:

    - `countMin` is, on a side-wall prop, the floor below which the entry
      declines ENTIRELY, so a wall-mounted set-piece that would stand alone
      reads as a room rather than as clutter. Everywhere else that reads it
      (toxicbarrel, groundlaser, a prop off the wall since OZX-1465) it is
      the low end of a per-room range, rolled in [countMin, count].
    - `chance` is rolled once for the whole entry, not once per object. A room
      is either a cultivation chamber with up to three pods or an ordinary
      room with none; rolling per object would thin every room out evenly,
      which is the distribution that makes a set-piece read as clutter.

    Two of these throw upstream (`AuthoredStaticPlacementsLogic.ValidateChances`)
    and two do not — but all four end in a placement that is not there and
    says nothing about why, which is the same failure either way.
    """
    kind = p.get("kind")
    count = p.get("count") or 0
    count_min = p.get("countMin") or 0
    chance = p.get("chance") or 0
    pinned = bool(p.get("cells"))
    issues: list[dict] = []

    if chance:
        if chance > 1:
            issues.append(_issue(
                "error", "ROOM_PLACEMENT_CHANCE_INVALID",
                f"{rid}: placement {pi} sets chance={chance}; it is a probability in "
                f"(0, 1]. Omit it (or write 0) to mean 'always'",
                entity=level_id, pointer=f"{pptr}/chance"))
        elif kind not in CHANCE_HONOURING_KINDS:
            issues.append(_issue(
                "error", "ROOM_PLACEMENT_CHANCE_UNREAD",
                f"{rid}: placement {pi} is kind '{kind}', which does not roll chance; "
                f"only {', '.join(sorted(CHANCE_HONOURING_KINDS))} does. The adapter "
                f"throws rather than letting it appear every time",
                entity=level_id, pointer=f"{pptr}/chance"))

    if count_min >= 2:
        reading = countmin_reading(p)
        if count and count_min > count:
            # A range kind throws on this (RollRangedCount); the side-wall
            # floor just never fits. Either way nothing is placed.
            issues.append(_issue(
                "error", "ROOM_PLACEMENT_COUNT_MIN_ABOVE_MAX",
                f"{rid}: placement {pi} has countMin {count_min} above count {count}. "
                + ("[countMin, count] is the per-room range, and the adapter throws on "
                   "an empty one" if reading == "range" else
                   "count is the ceiling random fill takes what it can of, so a floor "
                   "above it can never be met and the entry is never placed"),
                entity=level_id, pointer=f"{pptr}/countMin"))
        elif pinned:
            issues.append(_issue(
                "warning", "ROOM_PLACEMENT_COUNT_MIN_UNREAD",
                f"{rid}: placement {pi} pins its cells, so countMin is not read — a "
                f"pinned entry already names exactly what it wants",
                entity=level_id, pointer=f"{pptr}/countMin"))
        elif reading is None:
            issues.append(_issue(
                "warning", "ROOM_PLACEMENT_COUNT_MIN_UNREAD",
                f"{rid}: placement {pi} is kind '{kind}', whose adapter never reads "
                f"countMin; it places up to count, whatever fits",
                entity=level_id, pointer=f"{pptr}/countMin"))

    if kind == "groundlaser":
        issues.extend(_groundlaser_issues(level_id, rid, pi, pptr, p))
    return issues


def _groundlaser_issues(level_id: str, rid: str, pi: int, pptr: str,
                        p: dict) -> list[dict]:
    """`kind: "groundlaser"` (OZX-1371, cells per OZX-1373).

    `GroundLaserPlacementLogic.ResolvePairRange` throws on all of these. The
    kind picks its own cells — columns {2, 7} × the entry side's rows, in
    left-right pairs — so `cells`, a footprint or a `placement` rule are
    errors. `countMin` / `count` are a range of lasers, defaulting to 2 / 4
    and confined to 2–4, and the range must hold an even number because a
    laser always has a twin.
    """
    issues: list[dict] = []

    def err(message, pointer=pptr):
        issues.append(_issue("error", "ROOM_GROUNDLASER_INVALID",
                             f"{rid}: placement {pi} (groundlaser) {message}",
                             entity=level_id, pointer=pointer))

    if p.get("cells"):
        err("pins cells; the kind uses its own fixed cells", f"{pptr}/cells")
    if (p.get("footprintCols") or 1) > 1 or (p.get("footprintRows") or 1) > 1:
        err("has a footprint; a laser is 1×1")
    if p.get("placement"):
        err(f"sets placement '{p['placement']}'; the kind has its own placement rule",
            f"{pptr}/placement")
    lo = p.get("countMin") or GROUNDLASER_MIN
    hi = p.get("count") or GROUNDLASER_MAX
    if lo < GROUNDLASER_MIN or hi > GROUNDLASER_MAX or lo > hi:
        err(f"asks for {lo}–{hi} lasers; the range lies within "
            f"{GROUNDLASER_MIN}–{GROUNDLASER_MAX} with countMin ≤ count")
    elif (lo + 1) // 2 > hi // 2:
        err(f"asks for {lo}–{hi} lasers, which holds no even number; lasers come in "
            f"left-right pairs")
    return issues


def validate_encounter(ds: Dataset, encounter_id: str) -> list[dict]:
    doc = ds.get("EncounterData", encounter_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING",
                       f"encounter '{encounter_id}' not found")]
    issues: list[dict] = []
    for index, step in enumerate(doc.value.get("steps") or []):
        sptr = f"/steps/{index}"
        condition = step.get("condition")
        if condition:
            kind = condition.get("kind")
            if kind not in {"time", "cleared", "touch", "killed", "custom"}:
                issues.append(_issue("warning", "TYPE_VALUE_UNKNOWN",
                                     f"step {index}: unknown condition kind '{kind}'",
                                     entity=encounter_id, pointer=f"{sptr}/condition/kind"))
        action = step.get("action")
        if action:
            verb = action.get("verb")
            # OZX-1173: `sound` plays one sfx and shares no field with a
            # launch — EncounterValidator returns before the launch checks,
            # so demanding an enemyId of it would be wrong. A missing id only
            # logs a line in SoundServiceAdapter: a step that looks fired and
            # is silent, which is why the validator throws on it. Whether the
            # id is registered in SoundConfig is upstream's to check.
            if verb == "sound":
                if not action.get("soundId"):
                    issues.append(_issue(
                        "error", "ENCOUNTER_SOUND_ID_MISSING",
                        f"step {index}: a 'sound' action needs a soundId; without one "
                        f"the step looks like it fired and plays nothing",
                        entity=encounter_id, pointer=f"{sptr}/action"))
                continue
            # "wait" is not a verb — a wait step is one with no action at all.
            if verb != "launch":
                issues.append(_issue(
                    "error", "ENCOUNTER_ACTION_VERB_INVALID",
                    f"step {index}: verb '{verb}' is invalid; the legal verbs are "
                    f"'launch' and 'sound' (a wait step omits `action` entirely)",
                    entity=encounter_id, pointer=f"{sptr}/action/verb"))
            enemy_id = action.get("enemyId")
            rail_issues, rail = _rail_launch_issues(ds, encounter_id, index, sptr, action)
            issues.extend(rail_issues)
            if rail:
                pass
            elif not enemy_id:
                issues.append(_issue("error", "ENCOUNTER_ENEMY_MISSING",
                                     f"step {index}: launch with no enemyId (or railTrainId / "
                                     f"railOilTankId)",
                                     entity=encounter_id, pointer=f"{sptr}/action"))
            elif (((ds.get("EnemyData", enemy_id) or _NO_DOC).value.get("railConfig") or {})
                  .get("enabled")):
                # The executor throws on it: a rail-bound enemy placed as a body
                # stands off the rail and fails on its first frame.
                issues.append(_issue(
                    "error", "ENCOUNTER_RAIL_ENEMY_AS_BODY",
                    f"step {index}: '{enemy_id}' is a rail-bound enemy; launch it as a train "
                    f"(railTrainId naming a GameData/railenemy train — a one-car train for a "
                    f"lone car)",
                    entity=encounter_id, pointer=f"{sptr}/action/enemyId"))
            elif ds.get("EnemyData", enemy_id) is None:
                issues.append(_issue("error", "ENCOUNTER_ENEMY_MISSING",
                                     f"step {index}: enemy '{enemy_id}' does not exist",
                                     entity=encounter_id, pointer=f"{sptr}/action/enemyId"))
            elif action.get("eliteCount", 0) > 0:
                enemy = ds.get("EnemyData", enemy_id)
                if enemy and not enemy.value.get("elite"):
                    issues.append(_issue(
                        "error", "ENCOUNTER_ELITE_CONFIG_MISSING",
                        f"step {index}: eliteCount is {action['eliteCount']} but "
                        f"'{enemy_id}' has no elite config",
                        entity=encounter_id, pointer=f"{sptr}/action/eliteCount"))

            lo, hi = action.get("min", 0), action.get("max", 0)
            if hi and lo and lo > hi:
                issues.append(_issue("error", "ENCOUNTER_COUNT_RANGE_INVALID",
                                     f"step {index}: min {lo} exceeds max {hi}",
                                     entity=encounter_id, pointer=f"{sptr}/action"))

            # OZX-1041 renamed this field from `dropTableIdOverride`. The old
            # name is not a back-compat alias: JsonUtility drops a field it
            # does not declare without a word, so a file still carrying it
            # falls silently back to the enemy's own table.
            drop = action.get("dropTableId")
            if drop and ds.get("LootTableData", drop) is None:
                issues.append(_issue("error", "LOOT_TABLE_MISSING",
                                     f"step {index}: dropTableId '{drop}' does not exist",
                                     entity=encounter_id,
                                     pointer=f"{sptr}/action/dropTableId"))
            if "dropTableIdOverride" in action:
                issues.append(_issue(
                    "error", "ENCOUNTER_DROP_FIELD_RENAMED",
                    f"step {index}: `dropTableIdOverride` was renamed to `dropTableId` "
                    f"(OZX-1041). JsonUtility discards the undeclared field in silence, "
                    f"so this launch quietly falls back to the enemy's own table",
                    entity=encounter_id,
                    pointer=f"{sptr}/action/dropTableIdOverride"))
            issues.extend(_pin_issues(ds, encounter_id, index, sptr, action))

            # OZX-1063: the sampler copies a pool entry's variant and clump
            # fields onto the step it synthesises, so an action carries the
            # same four. The role rule is NOT repeated here — an action has
            # no pool to take a representative's spawn layer from, and
            # EncounterValidator does not check it either.
            aptr = f"{sptr}/action"
            # OZX-1328: the Boss flag is read on the direct-placement path
            # only; a channel request can be discarded (spawner killed,
            # emitMaxTotal spent), which would lose the floor's Boss quietly.
            if action.get("boss") and action.get("viaSpawner") and not rail:
                issues.append(_issue(
                    "error", "ENCOUNTER_BOSS_ON_SPAWNER_CHANNEL",
                    f"step {index}: boss cannot ride a viaSpawner launch — the channel never "
                    f"reads the flag, so the floor's Boss would spawn as an ordinary body",
                    entity=encounter_id, pointer=f"{aptr}/boss"))
            # A rail launch has had its body-placement fields refused above,
            # by name; checking their shape as well would say it twice.
            if not rail:
                issues.extend(_variant_issues(
                    ds, encounter_id, f"step {index}", aptr, action.get("enemyId"),
                    list(action.get("variantIds") or []),
                    via_spawner=bool(action.get("viaSpawner"))))
                issues.extend(_cluster_issues(
                    encounter_id, f"step {index}", aptr, action,
                    via_spawner=bool(action.get("viaSpawner"))))

    return issues


class _NoDoc:
    value: dict = {}


_NO_DOC = _NoDoc()


def _rail_launch_issues(ds: Dataset, encounter_id: str, index: int, sptr: str,
                        action: dict) -> tuple[list[dict], bool]:
    """A launch that lays cars on the rail (OZX-1309 trains, OZX-1317 tanks).

    `railTrainId` names a `RailTrainData` (GameData/railenemy: `cars` head
    first, coupling gap, close-up speed); `railOilTankId` an `OilTankData`
    whose `railConfig` is enabled. One launch places one kind of thing, so
    the two and `enemyId` are exclusive. A rail launch never reads the fields
    that place or decorate bodies on a spawn LAYER, and an unread field
    looks like a working one — the train still comes out — so
    `EncounterValidator.RailLaunchProblem` refuses each by name. Since
    OZX-1315 a train's cars DO roll the launch's `dropTableId` (a pool floor
    stamps its loot table on every launch); a tank drops nothing. Since
    OZX-1341 a train may ride a rail factory's spawner channel; a tank may
    not.

    Returns (issues, is_rail).
    """
    train = action.get("railTrainId") or None
    tank = action.get("railOilTankId") or None
    if not (train or tank):
        return [], False
    aptr = f"{sptr}/action"
    issues: list[dict] = []

    def err(code, message, pointer=aptr):
        issues.append(_issue("error", code, f"step {index}: {message}",
                             entity=encounter_id, pointer=pointer))

    if train and tank:
        err("ENCOUNTER_LAUNCH_TARGET_AMBIGUOUS",
            f"names both railTrainId '{train}' and railOilTankId '{tank}' — split it into "
            f"two steps")
    if action.get("enemyId"):
        err("ENCOUNTER_LAUNCH_TARGET_AMBIGUOUS",
            f"names both enemyId '{action['enemyId']}' and a rail target; a launch places "
            f"bodies on a spawn layer OR cars on the rail")
    if train and ds.get("RailTrainData", train) is None:
        err("DATA_REFERENCE_MISSING", f"railTrainId '{train}' is not a RailTrainData",
            f"{aptr}/railTrainId")
    if tank:
        tank_doc = ds.get("OilTankData", tank)
        if tank_doc is None:
            err("DATA_REFERENCE_MISSING", f"railOilTankId '{tank}' is not an OilTankData",
                f"{aptr}/railOilTankId")
        elif not (tank_doc.value.get("railConfig") or {}).get("enabled"):
            err("ENCOUNTER_RAIL_TANK_NOT_ON_RAIL",
                f"railOilTankId '{tank}' has no enabled railConfig, so it cannot ride the rail",
                f"{aptr}/railOilTankId")

    unread = None
    if action.get("viaSpawner") and tank:
        unread = "viaSpawner (tanks are placed on the rail, not emitted by a spawner)"
    elif action.get("at"):
        unread = f"at '{action['at']}' (a train stands on the rail, not on a spawn layer)"
    elif action.get("eliteCount"):
        unread = "eliteCount (the cars spawn as the train data lists them)"
    elif action.get("boss"):
        unread = "boss (a floor's Boss is one body placed by its bossId launch)"
    elif action.get("dropTableId") and tank:
        unread = "dropTableId (a rail oil tank is neutral and drops nothing)"
    elif action.get("variantIds"):
        unread = "variantIds (a train's cars are listed in its RailTrainData)"
    elif any(action.get(k) for k in ("clusterSizeMin", "clusterSizeMax", "clusterRadius",
                                     "sharedRoot", "fanArcDegrees")):
        unread = "cluster / sharedRoot fields (a train is laid along the rail, not clumped)"
    if unread:
        err("ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD", f"a rail launch sets {unread}, which it never reads")
    return issues, True


def _pin_issues(ds: Dataset, encounter_id: str, index: int,
                sptr: str, action: dict) -> list[dict]:
    """`EncounterAction.guaranteedDrop` — removed by OZX-1380.

    The prize pinned to a launch moved to the ROOM (`guaranteedDrops`), paid
    by the last-killed body of the opening wave: a pin on a launch could ride
    a later wave the player walks away from, or a spawner request that is
    discarded. JsonUtility drops the undeclared field without a word, so a
    file still carrying one pays nothing. A pin exists iff it names an item
    or a table (the object itself is always materialised).
    """
    pin = action.get("guaranteedDrop")
    if not isinstance(pin, dict) or not (pin.get("itemId") or pin.get("tableId")):
        return []
    return [_issue(
        "error", "ENCOUNTER_PIN_REMOVED",
        f"step {index}: guaranteedDrop '{pin.get('itemId') or pin.get('tableId')}' is on the "
        f"launch, where OZX-1380 removed it; the field is dropped unread. Move it to the "
        f"room's guaranteedDrops, which the last kill of the opening wave pays",
        entity=encounter_id, pointer=f"{sptr}/action/guaranteedDrop")]


def pin_shape_issues(ds: Dataset, entity: str, where: str, pptr: str,
                     pin: dict) -> list[dict]:
    """One `GuaranteedDrop`: `EncounterValidator.ValidateGuaranteedDrop`.

    An item or a `pickOne` table with entries (`LootTableDraw.Require`),
    never both and never neither; at least one copy; and a chance in (0, 1],
    0 meaning "not authored" (always). Every one of these is otherwise a
    prize that arrives as nothing, or a throw at RoomReady.
    """
    item_id = pin.get("itemId") or None
    table_id = pin.get("tableId") or None
    count = pin.get("count") or 0
    chance = pin.get("chance") or 0
    issues: list[dict] = []

    def err(code, message, pointer=pptr):
        issues.append(_issue("error", code, f"{where} {message}", entity=entity, pointer=pointer))

    if item_id and table_id:
        err("ROOM_PIN_AMBIGUOUS",
            f"names both itemId '{item_id}' and tableId '{table_id}'; a pin drops one "
            f"named thing or draws from one table, never both")
        return issues
    if not item_id and not table_id:
        err("ROOM_PIN_EMPTY", "names neither itemId nor tableId, so nothing would drop")
        return issues
    if item_id:
        if ds.get("ItemData", item_id) is None:
            named_table = ds.get("LootTableData", item_id) is not None
            err("ROOM_PIN_ITEM_MISSING",
                f"itemId '{item_id}' is not an ItemData"
                + (" — it is a loot table; a pin that draws from a table uses `tableId`"
                   if named_table else ""), f"{pptr}/itemId")
    else:
        table = ds.get("LootTableData", table_id)
        if table is None:
            err("LOOT_TABLE_MISSING", f"tableId '{table_id}' does not exist", f"{pptr}/tableId")
        elif not table.value.get("pickOne"):
            err("ROOM_PIN_TABLE_NOT_PICKONE",
                f"tableId '{table_id}' is not pickOne; an ordinary table rolls every entry "
                f"independently, so it can yield none or all of them and the count stops "
                f"meaning anything", f"{pptr}/tableId")
        elif not (table.value.get("entries") or []):
            err("ROOM_PIN_TABLE_EMPTY", f"tableId '{table_id}' has no entries to draw",
                f"{pptr}/tableId")
    if count < 1:
        err("ROOM_PIN_COUNT_INVALID",
            f"pins '{item_id or table_id}' with count {count}; a pin that pays no copy is "
            f"never what was meant", f"{pptr}/count")
    if chance < 0 or chance > 1:
        err("ROOM_PIN_CHANCE_INVALID",
            f"has chance {chance}; it must be in (0, 1], or omitted for a pin that always "
            f"fires. To switch a pin off, delete it", f"{pptr}/chance")
    return issues


def opening_wave_places_a_body(encounter: dict) -> bool:
    """`EncounterWaves.GuaranteesOpeningBody` (OZX-1380).

    The opening wave is every step before the first one with a condition;
    its DIRECT launches (not a viaSpawner request, which may be discarded,
    and not a rail launch, laid on the rail) are the one batch certain to
    appear. A room can carry guaranteedDrops iff one of them has min ≥ 1.
    """
    for step in encounter.get("steps") or []:
        if (step.get("condition") or {}).get("kind"):
            break
        action = step.get("action") or {}
        if (action.get("verb") == "launch" and not action.get("viaSpawner")
                and not action.get("railTrainId") and not action.get("railOilTankId")
                and (action.get("min") or 0) >= 1):
            return True
    return False


def _room_pin_issues(ds: Dataset, level_id: str, rid: str, rptr: str,
                     room: dict) -> list[dict]:
    """`RoomNodeData.guaranteedDrops` (OZX-1380) — what a room owes.

    Paid by the LAST-KILLED body of the room's opening wave, on top of that
    body's own drops. `EncounterValidator.ValidateRoomPins` runs at RoomReady
    and throws when the room runs no encounter, or one whose opening wave is
    not sure to place a body: there is no fallback channel, so nobody would
    carry the prize — and the prize is usually the floor's key.
    """
    pins = room.get("guaranteedDrops") or []
    if not pins:
        return []
    issues: list[dict] = []
    for i, pin in enumerate(pins):
        issues.extend(pin_shape_issues(ds, level_id, f"{rid}: guaranteedDrops[{i}]",
                                       f"{rptr}/guaranteedDrops/{i}", pin or {}))
    encounter_id = room.get("encounterId")
    encounter = room.get("inlineEncounter") or (
        (ds.get("EncounterData", encounter_id) or _NO_DOC).value if encounter_id else None)
    if not encounter:
        if encounter_id and ds.get("EncounterData", encounter_id) is None:
            return issues  # the missing encounter is reported on its own
        issues.append(_issue(
            "error", "ROOM_PIN_NO_CARRIER",
            f"{rid}: owes {len(pins)} guaranteedDrops but runs no encounter; they are paid "
            f"by the last-killed enemy of the opening wave, and this room has none",
            entity=level_id, pointer=f"{rptr}/guaranteedDrops"))
    elif not opening_wave_places_a_body(encounter):
        issues.append(_issue(
            "error", "ROOM_PIN_NO_CARRIER",
            f"{rid}: owes {len(pins)} guaranteedDrops, but encounter "
            f"'{encounter_id or encounter.get('id') or '(inline)'}' has no opening-wave launch "
            f"sure to place a body (a direct launch with min >= 1 before the first "
            f"conditioned step), so nobody carries them",
            entity=level_id, pointer=f"{rptr}/guaranteedDrops"))
    return issues


def validate_plan(ds: Dataset, plan_id: str) -> list[dict]:
    doc = ds.get("LevelBasePlanData", plan_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING", f"plan '{plan_id}' not found")]
    defaults = doc.value.get("floorDefaults") or {}
    issues: list[dict] = []
    for key, why in plandefaults.refused_defaults(defaults):
        issues.append(_issue(
            "error", "LEVEL_FLOOR_DEFAULT_REFUSED",
            f"floorDefaults sets {key}, which is never inherited: {why}",
            entity=plan_id, pointer=f"/floorDefaults/{key}"))
    issues.extend(_skill_drop_cap_issues(ds, plan_id, doc.value))
    # Every floor is checked as the generator sees it — merged with the
    # chapter defaults (OZX-1011) — and each finding is then moved to the
    # node that actually holds the value. A default that is wrong is wrong
    # once, in `/floorDefaults`, however many floors inherit it.
    merged = [plandefaults.merge_floor(defaults, raw)
              for raw in doc.value.get("floors") or []]
    issues.extend(_area_link_issues(ds, plan_id, doc.value, [m for m, _ in merged]))
    issues.extend(_final_area_issues(plan_id, doc.value, merged))
    issues.extend(_skill_platform_issues(ds, plan_id, doc.value, merged))
    arrival = _arrival_order(doc.value.get("areaLinks") or [], len(merged))
    by_pointer: dict = {}
    for fi, (floor, source) in enumerate(merged):
        for issue in _floor_plan_issues(ds, plan_id, fi, floor,
                                        bool(doc.value.get("waivePoolCoverage")),
                                        arrival[fi]):
            pointer = plandefaults.reroute(issue["pointer"], fi, source)
            if pointer == issue["pointer"]:
                issues.append(issue)
                continue
            issue["pointer"] = pointer
            key = (issue["code"], pointer)
            if key in by_pointer:
                by_pointer[key][1].append(fi)
                continue
            by_pointer[key] = (issue, [fi])
            issues.append(issue)
    for issue, floors in by_pointer.values():
        issue["message"] += (
            f" (inherited from floorDefaults by floor"
            f"{'s' if len(floors) > 1 else ''} {', '.join(map(str, floors))})")
    return issues


def _platform_skill_problem(ds: Dataset, skill) -> tuple | None:
    """Why SkillPlatformSpawnAdapter would throw on this SkillData, or None.

    (code, "which …") — the clause reads after "grants '<id>', ". Shared by
    authored platforms and the story rooms a plan hangs off its layout
    (OZX-1483), which place theirs through the same adapter.
    """
    if skill.value.get("type") != "active":
        # OZX-1400: the platform became a rechargeable station for a
        # positive (active) skill; it used to grant a passive, and now
        # throws on one.
        return ("ROOM_PLACEMENT_SKILL_NOT_ACTIVE",
                f"which is type '{skill.value.get('type')}'; a SkillPlatform only "
                f"offers active skills (OZX-1400)")
    # The platform draws the skill's level-1 icon in its centre, and
    # ResolveIcon throws twice over: once when iconKeys is empty, and again
    # when the key it names will not load. Both are legal SkillData and
    # illegal on a platform, so both are caught here.
    icon = (skill.value.get("iconKeys") or [None])[0]
    if not icon:
        return ("ROOM_PLACEMENT_SKILL_NO_ICON",
                "which has no iconKeys; a SkillPlatform needs an icon to display "
                "and throws without one")
    if ds.sprites.guid_for_key(icon) is None:
        # Verbatim, because ResourcesAssetService does no `@`-suffix
        # fallback — a key that resolves only by dropping the suffix still
        # throws in game.
        return ("ROOM_PLACEMENT_SKILL_NO_ICON",
                f"which names icon '{icon}', which is not in ResourcesDB; the "
                f"platform cannot load it and throws")
    return None


def _skill_platform_issues(ds: Dataset, plan_id: str, plan: dict,
                           merged: list) -> list[dict]:
    """A plan's skill platforms: `activeSkillIds`, `storyRooms` (OZX-1483)
    and `startSkillPlatform` (OZX-1488).

    `SkillPlatformPlanRules.Validate` rule for rule — `LevelBasePlan.FromData`
    and `LevelPlanValidator` both throw on these — then what that assembly
    cannot see: each id is placed by SkillPlatformSpawnAdapter, so it has
    to be an active skill with a loadable icon. And `ValidateStoryTilemaps`:
    a story room is a one-door dead end hung off any free side, so every
    generated area's theme needs a `story` tilemap for each single-door
    mask, exactly or by a superset, in the mirror-expanded pool.
    """
    issues: list[dict] = []

    def err(code, message, pointer, severity="error"):
        issues.append(_issue(severity, code, message, entity=plan_id, pointer=pointer))

    story = plan.get("storyRooms") if isinstance(plan.get("storyRooms"), dict) else None
    start = (plan.get("startSkillPlatform")
             if isinstance(plan.get("startSkillPlatform"), dict) else None)
    lo = (story or {}).get("countMin") or 0
    hi = (story or {}).get("countMax") or 0
    charges = (story or {}).get("charges") or 0
    start_charges = (start or {}).get("charges") or 0
    has_story, has_start = hi > 0, start_charges > 0

    if story is not None and "activeSkillIds" in story:
        # OZX-1488 moved the list to the plan; JsonUtility drops the old
        # field without a word, so the story rooms would draw from nothing.
        err("LEVEL_PLAN_FIELD_MOVED",
            "storyRooms.activeSkillIds moved to the plan's top-level activeSkillIds "
            "(OZX-1488), shared with the start room's platform; the old field is "
            "ignored without a word", "/storyRooms/activeSkillIds")
    if story is not None and not has_story and (lo or charges):
        # JsonUtility never leaves the block null; countMax is what declares it.
        err("LEVEL_STORY_ROOMS_UNDECLARED",
            f"storyRooms is filled in but countMax is {hi}; countMax is what "
            f"declares story rooms, so the generator throws. Omit the block for none",
            "/storyRooms/countMax")
    if start is not None and start_charges < 0:
        err("LEVEL_START_SKILL_PLATFORM_NO_CHARGES",
            f"startSkillPlatform has charges {start_charges}; the platform needs at "
            f"least 1, or omit the block for none", "/startSkillPlatform/charges")
    elif start is not None and not has_start:
        err("LEVEL_START_SKILL_PLATFORM_UNDECLARED",
            "startSkillPlatform has charges 0, which is what declares no platform, so "
            "the start room gets none. Give it charges, or omit the block",
            "/startSkillPlatform/charges", "warning")
    if has_story:
        if lo < 1 or lo > hi:
            err("LEVEL_STORY_ROOMS_COUNT_INVALID",
                f"storyRooms has countMin {lo} / countMax {hi}; need 1 ≤ countMin ≤ countMax",
                "/storyRooms/countMin")
        if charges <= 0:
            err("LEVEL_STORY_ROOMS_NO_CHARGES",
                f"storyRooms has charges {charges}; each platform needs at least 1",
                "/storyRooms/charges")

    ids = plan.get("activeSkillIds") or []
    drawn = (1 if has_start else 0) + (hi if has_story else 0)
    if drawn == 0:
        if ids:
            err("LEVEL_ACTIVE_SKILLS_UNUSED",
                f"lists {len(ids)} activeSkillIds but declares neither storyRooms nor "
                f"startSkillPlatform, so nothing draws from them", "/activeSkillIds")
        return issues
    if len(ids) < drawn:
        err("LEVEL_ACTIVE_SKILLS_TOO_FEW",
            f"lists {len(ids)} activeSkillIds but a run can draw {drawn} "
            f"(startSkillPlatform {1 if has_start else 0} + storyRooms.countMax "
            f"{hi if has_story else 0}). Skills are drawn without replacement, so the "
            f"list needs at least that many", "/activeSkillIds")
    seen: set = set()
    for i, sid in enumerate(ids):
        ptr = f"/activeSkillIds/{i}"
        if not sid:
            err("LEVEL_ACTIVE_SKILLS_EMPTY", f"activeSkillIds[{i}] is empty", ptr)
            continue
        if sid in seen:
            err("LEVEL_ACTIVE_SKILLS_DUPLICATE",
                f"lists '{sid}' twice in activeSkillIds; a duplicate would let one run "
                f"offer the same skill twice", ptr)
            continue
        seen.add(sid)
        skill = ds.get("SkillData", sid)
        if skill is None:
            err("DATA_REFERENCE_MISSING",
                f"activeSkillIds[{i}] '{sid}' is not a SkillData", ptr)
            continue
        problem = _platform_skill_problem(ds, skill)
        if problem:
            err(problem[0], f"a skill platform may grant '{sid}', {problem[1]}", ptr)

    if not has_story:
        return issues
    facts = ds.tilemaps.facts()
    if not facts:
        return issues
    for fi, (floor, source) in enumerate(merged):
        if floor.get("generatorType") == "static":
            continue
        library = tilemaps.for_theme(facts, floor.get("themeId"))
        missing = [f"{side} ({mask})" for mask, side in
                   ((1, "top"), (2, "right"), (4, "bottom"), (8, "left"))
                   if not any(tilemaps.matches_stage(t["stageType"], "story")
                              and (t["openDoors"] & mask) == mask for t in library)]
        if missing:
            err("LEVEL_STORY_TILEMAP_MISSING",
                f"storyRooms is declared, but floor {fi}'s theme "
                f"'{floor.get('themeId')}' has no 'story' tilemap with a door on: "
                f"{', '.join(missing)}. A story room hung off that side would throw "
                f"out of RoomTilemapQuery on entry",
                plandefaults.reroute(f"/floors/{fi}/themeId", fi, source))
    return issues


def _area_link_issues(ds: Dataset, plan_id: str, plan: dict,
                      floors: list[dict]) -> list[dict]:
    """`LevelBasePlanData.areaLinks` (OZX-1185) — how a plan's areas join.

    Without it a plan is the implicit chain i → i+1 of seam doors keyed by
    each floor's `transitionKeyId`. With it, each link names its two areas,
    whether it is a co-planar `door` (optionally keyed) or a `stair` into or
    out of a basement, and for a stair how many rooms of `fromArea` carry
    one. `AreaLinkPlan.Build` throws on every rule below; this is that
    function rule for rule, read on the MERGED floors as it is, because the
    basement test reads each area's effective `themeId`.
    """
    links = plan.get("areaLinks") or []
    if not links:
        return []
    n = len(floors)
    issues: list[dict] = []

    def err(code, message, pointer):
        issues.append(_issue("error", code, message, entity=plan_id, pointer=pointer))

    good = []
    joined: set = set()
    for i, link in enumerate(links):
        lptr = f"/areaLinks/{i}"
        a, b = link.get("fromArea", 0), link.get("toArea", 0)
        if not (0 <= a < n and 0 <= b < n):
            err("LEVEL_AREA_LINK_OUT_OF_RANGE",
                f"areaLinks[{i}] joins area {a} to area {b}, but the plan has {n} "
                f"area(s) (floors[0..{n - 1}])", lptr)
            continue
        if a == b:
            err("LEVEL_AREA_LINK_SELF", f"areaLinks[{i}] joins area {a} to itself", lptr)
            continue
        if b == 0:
            err("LEVEL_AREA_LINK_INTO_START",
                f"areaLinks[{i}] leads INTO area 0, where the run starts; links run "
                f"outward from it (the way back is built for every link), so write it "
                f"as fromArea 0", f"{lptr}/toArea")
            continue
        pair = (min(a, b), max(a, b))
        if pair in joined:
            err("LEVEL_AREA_LINK_DUPLICATE",
                f"areaLinks[{i}] joins areas {a} and {b}, which an earlier link already "
                f"joins; two areas are joined once", lptr)
            continue
        joined.add(pair)
        kind = link.get("kind")
        if kind not in ("door", "stair"):
            err("LEVEL_AREA_LINK_KIND_UNKNOWN",
                f"areaLinks[{i}] has kind '{kind}'; it is \"door\" (a co-planar seam) "
                f"or \"stair\" (into or out of a basement)", f"{lptr}/kind")
            continue
        lo, hi = link.get("stairRoomsMin") or 0, link.get("stairRoomsMax") or 0
        rooms = (1, 1)
        if kind == "door":
            if lo or hi:
                err("LEVEL_AREA_LINK_FIELD_MISPLACED",
                    f"areaLinks[{i}] is a door but sets stairRoomsMin/Max; a door is one "
                    f"seam, only a stair can hang off several rooms", lptr)
            key = link.get("keyId")
            if key and ds.get("ItemData", key) is None:
                err("DATA_REFERENCE_MISSING",
                    f"areaLinks[{i}]: keyId '{key}' is not an ItemData; the gate would "
                    f"drop nothing that opens it", f"{lptr}/keyId")
        else:
            if link.get("keyId"):
                err("LEVEL_AREA_LINK_FIELD_MISPLACED",
                    f"areaLinks[{i}] is a stair with keyId '{link['keyId']}'. Stairs have "
                    f"no lock at runtime (they open on room clear), so the key would gate "
                    f"nothing; put it on a door link", f"{lptr}/keyId")
            if lo or hi:
                if lo < 1 or hi < lo:
                    err("LEVEL_AREA_LINK_STAIR_ROOMS_INVALID",
                        f"areaLinks[{i}] has stairRoomsMin {lo} / stairRoomsMax {hi}; both "
                        f">= 1 and max >= min, or omit both for one room", lptr)
                else:
                    rooms = (lo, hi)
            # OZX-1338: a stair shows Up or Down from the two elevations.
            ea, eb = floors[a].get("elevation") or 0, floors[b].get("elevation") or 0
            if ea == eb:
                err("LEVEL_AREA_LINK_STAIR_LEVEL",
                    f"areaLinks[{i}] is a stair between areas {a} and {b}, which both "
                    f"stand at elevation {ea}. A stair climbs or descends: give a basement "
                    f"a negative elevation, an upper area a positive one — or join them "
                    f"with a door", lptr)
            else:
                base_a = _area_category(floors[a].get("themeId")) == "basement"
                base_b = _area_category(floors[b].get("themeId")) == "basement"
                if base_a != base_b:
                    basement, other = (a, b) if base_a else (b, a)
                    if (floors[basement].get("elevation") or 0) >= \
                            (floors[other].get("elevation") or 0):
                        err("LEVEL_AREA_LINK_STAIR_LEVEL",
                            f"areaLinks[{i}] is a stair between basement area {basement} "
                            f"and area {other}, but the basement does not stand lower; the "
                            f"stairs would read upside down at both ends", lptr)
        good.append({"index": i, "from": a, "to": b, "kind": kind, "rooms": rooms})

    for f, floor in enumerate(floors):
        fptr = f"/floors/{f}"
        if floor.get("generatorType") == "static":
            err("LEVEL_AREA_LINK_STATIC_FLOOR",
                f"floor {f} is generatorType \"static\", which areaLinks does not join — "
                f"links hang off the generator's trunk top and entrance room", 
                f"{fptr}/generatorType")
        if floor.get("transitionKeyId"):
            err("LEVEL_AREA_LINK_FLOOR_KEY",
                f"floor {f} sets transitionKeyId '{floor['transitionKeyId']}', but the plan "
                f"declares areaLinks; a key belongs on the door link it gates "
                f"(areaLinks[].keyId)", f"{fptr}/transitionKeyId")
        out_doors = [l for l in good if l["from"] == f and l["kind"] == "door"]
        in_doors = [l for l in good if l["to"] == f and l["kind"] == "door"]
        out_stairs = [l for l in good if l["from"] == f and l["kind"] == "stair"]
        for many, what, why in (
                (out_doors, "left by", "the seam door hangs on the top of the area's "
                                       "trunk top, which holds one"),
                (in_doors, "entered by", "the seam door lands on the bottom of the "
                                         "area's entrance room, which holds one")):
            if len(many) > 1:
                err("LEVEL_AREA_LINK_FAN_OUT",
                    f"floor {f} is {what} {len(many)} door links; {why}",
                    f"/areaLinks/{many[1]['index']}")
        if len(out_stairs) > 1:
            err("LEVEL_AREA_LINK_FAN_OUT",
                f"floor {f} is left by {len(out_stairs)} stair links; one per area, with "
                f"stairRoomsMin/Max saying how many rooms carry it",
                f"/areaLinks/{out_stairs[1]['index']}")
        if not out_doors and len(out_stairs) == 1 and out_stairs[0]["rooms"][1] > 1:
            err("LEVEL_AREA_LINK_FAN_OUT",
                f"floor {f} is left only by a stair, so that stair is its way on — the "
                f"room the key gate and the breather hang off — and must be one room; "
                f"it asks for {out_stairs[0]['rooms'][0]}–{out_stairs[0]['rooms'][1]}",
                f"/areaLinks/{out_stairs[0]['index']}")

    for l in good:
        if l["kind"] != "stair" or l["rooms"][1] <= 1:
            continue
        if not any(o["from"] == l["to"] and o["kind"] == "door" for o in good):
            err("LEVEL_AREA_LINK_LANDING_NEEDS_DOOR",
                f"areaLinks[{l['index']}] lands stairs in up to {l['rooms'][1]} rooms of "
                f"area {l['to']}, which no door link leaves; an area entered in several "
                f"rooms has to be left by a door, or its way on cannot be told from the "
                f"stairs back", f"/areaLinks/{l['index']}")

    reached = {0}
    grew = True
    while grew:
        grew = False
        for l in good:
            for x, y in ((l["from"], l["to"]), (l["to"], l["from"])):
                if x in reached and y not in reached:
                    reached.add(y)
                    grew = True
    for f in range(n):
        if f not in reached:
            err("LEVEL_AREA_UNREACHABLE",
                f"floor {f} is joined to nothing reachable from area 0 by areaLinks, so "
                f"no run can ever enter it", f"/floors/{f}")
    return issues


def _final_area_issues(plan_id: str, plan: dict, merged: list) -> list[dict]:
    """`endsRunOnClear` on a plan floor, and the boss-less ending (OZX-1442).

    Only the chapter's final area may end the run — the last floor without
    areaLinks, the one no link leaves with them (`LevelBasePlan.IsFinalArea`);
    elsewhere the generator throws. A final area with no `bossId` ends on
    its last peak room in BFS order, so `ValidateFinalPeak` asks that the
    floor lists a peak stage, that the portions buy at least one peak room
    at `roomCountMin` (largest remainder, as the generator deals), that the
    stage fields enemies (an empty room is cleared — and the run won — on
    entry), and that a peak second wave arrives only on `cleared`, since an
    `advance` wave can still be waiting at the first calm, which IS the
    victory. Static floors mark their own final rooms and are skipped.
    """
    links = plan.get("areaLinks") or []
    n = len(merged)
    issues: list[dict] = []
    for fi, (floor, source) in enumerate(merged):
        if not floor.get("endsRunOnClear") or floor.get("generatorType") == "static":
            continue
        fptr = f"/floors/{fi}"
        final = (not any(l.get("fromArea") == fi for l in links)) if links else fi == n - 1
        if not final:
            issues.append(_issue(
                "error", "LEVEL_ENDS_RUN_NOT_FINAL_AREA",
                f"floor {fi} sets endsRunOnClear, but the chapter continues past it; only the "
                f"final area may end the run", entity=plan_id, pointer=f"{fptr}/endsRunOnClear"))
            continue
        if floor.get("bossId"):
            continue
        where = f"floor {fi} ends the run with no bossId, so on its last peak room"
        stages = floor.get("stageTypes") or []
        peak = next((s for s in stages if s.get("stageType") == "peak"), None)
        sptr = plandefaults.field_base(fi, source, "stageTypes") + "/stageTypes"
        if peak is None:
            issues.append(_issue(
                "error", "LEVEL_FINAL_PEAK_INVALID", f"{where} — and it lists no peak stage",
                entity=plan_id, pointer=sptr))
            continue
        counts = plandefaults.resolve_portions([s.get("portion") or 0 for s in stages],
                                               floor.get("roomCountMin") or 0)
        if not sum(c for s, c in zip(stages, counts) if s.get("stageType") == "peak"):
            issues.append(_issue(
                "error", "LEVEL_FINAL_PEAK_INVALID",
                f"{where} — but at its smallest size ({floor.get('roomCountMin') or 0} rooms) "
                f"the portions buy no peak room; raise the peak portion or roomCountMin",
                entity=plan_id, pointer=sptr))
        pi = stages.index(peak)
        if (peak.get("enemyBudgetCut") or 0) >= 1:
            issues.append(_issue(
                "error", "LEVEL_FINAL_PEAK_INVALID",
                f"{where} — but that stage cuts its whole enemy budget, so the final room is "
                f"cleared, and the run won, on entry",
                entity=plan_id, pointer=f"{sptr}/{pi}/enemyBudgetCut"))
        for ti, trigger in enumerate((peak.get("secondWave") or {}).get("triggers") or []):
            if trigger and trigger.get("kind") != "cleared":
                issues.append(_issue(
                    "error", "LEVEL_FINAL_PEAK_INVALID",
                    f"{where} — but its secondWave.triggers[{ti}] is '{trigger.get('kind')}', "
                    f"which can still be waiting when the final room first goes calm, and that "
                    f"calm is the victory; a run-ending peak's second wave arrives on 'cleared'",
                    entity=plan_id, pointer=f"{sptr}/{pi}/secondWave/triggers/{ti}"))
    return issues


def _area_category(theme_id: str | None) -> str:
    """`AreaCategory.OfTheme`: `themes/X` → `X`, no theme → normal."""
    if not theme_id:
        return "normal"
    return theme_id.rsplit("/", 1)[-1]


def _arrival_order(links: list, n: int) -> list:
    """`LevelPlanValidator.ArrivalOrder` (OZX-1429): each area's earliest k.

    The fewest area-to-area links from the entry area (floors[0], k = 1),
    plus one, walking links both ways (every link has its way back). No
    links = the implicit chain. An area no route reaches gets None — that is
    LEVEL_AREA_UNREACHABLE's to report, and no tier bound applies to it.
    """
    adjacent: dict = {a: [] for a in range(n)}
    if links:
        for l in links:
            a, b = l.get("fromArea", 0), l.get("toArea", 0)
            if 0 <= a < n and 0 <= b < n:
                adjacent[a].append(b)
                adjacent[b].append(a)
    else:
        for a in range(n - 1):
            adjacent[a].append(a + 1)
            adjacent[a + 1].append(a)
    order = [None] * n
    if n:
        order[0] = 1
        queue = [0]
        while queue:
            a = queue.pop(0)
            for b in adjacent[a]:
                if order[b] is None:
                    order[b] = order[a] + 1
                    queue.append(b)
    return order


def _variant_tier_issues(ds: Dataset, plan_id: str, fi: int, fptr: str,
                         floor: dict, arrival: int | None) -> list[dict]:
    """A variant's tier ≤ the area's arrival order (OZX-1429).

    A level opens with only the weakest variant of each family and each
    further area unlocks one more tier: the k-th area reached deploys tier
    ≤ k, k being its EARLIEST arrival so the bound holds on every route.
    Every body the pool can place is checked — the entry's enemyId and each
    of its variantIds. Elite promotion is not tier-limited, and a rail train
    is not checked (its cars are the train's, not family variants).
    """
    if not arrival or floor.get("generatorType") == "static":
        return []
    issues: list[dict] = []
    for ei, entry in enumerate(floor.get("enemyPool") or []):
        if entry.get("railTrainId"):
            continue
        ids = [("enemyId", entry.get("enemyId"))] + [
            (f"variantIds/{vi}", v) for vi, v in enumerate(entry.get("variantIds") or [])]
        for suffix, enemy_id in ids:
            enemy = ds.get("EnemyData", enemy_id or "")
            if enemy is None:
                continue
            fid, tier = family_of(enemy.value, enemy.id)
            if tier > arrival:
                issues.append(_issue(
                    "error", "LEVEL_VARIANT_TIER_TOO_EARLY",
                    f"floor {fi}: enemyPool[{ei}] '{enemy_id}' is tier {tier} of family "
                    f"'{fid}', but this area can be reached as the k = {arrival} area of the "
                    f"run, which deploys tier ≤ {arrival}; use a weaker variant here, or move "
                    f"this one to an area reached later",
                    entity=plan_id, pointer=f"{fptr}/enemyPool/{ei}/{suffix}"))
    return issues


def _floor_plan_issues(ds: Dataset, plan_id: str, fi: int,
                       floor: dict, waive_coverage: bool = False,
                       arrival: int | None = None) -> list[dict]:
    """One merged floor. Pointers are written as if every field were the
    floor's own; `validate_plan` reroutes the inherited ones."""
    issues: list[dict] = []
    fptr = f"/floors/{fi}"
    issues.extend(_enemy_budget_cut_issues(plan_id, fi, fptr, floor))
    gen = floor.get("generatorType")
    # ParseGeneratorType swallows BOTH null and unknown strings into Tree.
    # Null is a legitimate default; a typo is not, and it is invisible.
    if gen is not None and gen not in GENERATOR_TYPES:
        issues.append(_issue(
            "error", "TYPE_VALUE_UNKNOWN",
            f"floor {fi}: generatorType '{gen}' is not recognised and will "
            f"silently fall back to 'tree'",
            entity=plan_id, pointer=f"{fptr}/generatorType"))

    stages = {s.get("stageType") for s in (floor.get("stageTypes") or [])}
    # New enemy types are only ever introduced in teaching/building rooms.
    # Part of upstream's pool coverage (`ValidatePoolCoverage`), so it holds
    # only where there is a pool to cover and a generator to deal stages,
    # and `waivePoolCoverage` (OZX-1366, test plans) switches it off.
    if (stages and not (stages & {"teaching", "building"}) and floor.get("enemyPool")
            and floor.get("generatorType") != "static" and not waive_coverage):
        issues.append(_issue(
            "error", "LEVEL_STAGE_INTRO_MISSING",
            f"floor {fi}: no teaching or building stage slice, so no room can "
            f"introduce a new enemy type (OZX-442)",
            entity=plan_id, pointer=f"{fptr}/stageTypes"))

    for ei, entry in enumerate(floor.get("enemyPool") or []):
        ref = entry.get("enemyId")
        if ref and ds.get("EnemyData", ref) is None:
            issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                                 f"floor {fi}: enemyPool '{ref}' does not exist",
                                 entity=plan_id,
                                 pointer=f"{fptr}/enemyPool/{ei}/enemyId"))
    issues.extend(_removed_plan_field_issues(plan_id, fi, fptr, floor))
    issues.extend(_rail_train_entry_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_spawner_pair_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_pool_variant_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_stage_weight_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_floor_loot_issues(ds, plan_id, fi, fptr, floor))

    # The same entries a room carries, applied to every generated room of
    # this floor whose own `staticPlacements` came out empty — and where
    # `chance` / `countMin` are actually authored today, since a
    # procedural floor has no hand-written rooms to put them on.
    loot = floor.get("floorLootPlan") or {}
    has_floor_table = bool(loot.get("probabilistic") or loot.get("probabilisticPresets"))
    for pi, p in enumerate(floor.get("defaultStaticPlacements") or []):
        pptr = f"{fptr}/defaultStaticPlacements/{pi}"
        issues.extend(_placement_count_issues(
            plan_id, f"floor {fi}", pi, pptr, p))
        issues.extend(_cargo_issues(plan_id, f"floor {fi}", pi, pptr, p, has_floor_table))
        issues.extend(_cargo_id_issues(ds, plan_id, f"floor {fi}", pi, pptr, p))
    boss = floor.get("bossId")
    if boss and ds.get("EnemyData", boss) is None:
        issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                             f"floor {fi}: bossId '{boss}' does not exist",
                             entity=plan_id, pointer=f"{fptr}/bossId"))
    # OZX-1555 `ValidateBossArrival`: a boss does not ambush. EnemyData has
    # no boss flag — a boss is whatever a floor names here — so the rule
    # lives on the plan: a teleporting boss would also count as late-arrivable
    # and could be drawn into any pool's second wave.
    elif boss and ds.get("EnemyData", boss).value.get("teleportArrival"):
        issues.append(_issue("error", "LEVEL_BOSS_TELEPORT_ARRIVAL",
                             f"floor {fi}: bossId '{boss}' has teleportArrival — a boss does "
                             f"not ambush. Drop the flag (and give it spawnPlacement 'initial' "
                             f"if it is a robot)",
                             entity=plan_id, pointer=f"{fptr}/bossId"))
    # bossId being EMPTY is deliberate and means "no boss on this floor"
    # (OZX-994), so there is no rule for it — the room that would have been
    # the boss room becomes an ordinary one.

    # The floor's cross-floor key. OZX-996 made this the single source for
    # which key the room channel drops — it is synthesised into
    # `loot_key_f{n}` at generation — so a name that resolves to nothing
    # now produces a key-room whose table drops a non-existent item, and
    # the floor is unopenable. Any key id is legal (it no longer has to be
    # key_silver); it does have to exist.
    key_id = floor.get("transitionKeyId")
    if key_id:
        key_item = ds.get("ItemData", key_id)
        if key_item is None:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"floor {fi}: transitionKeyId '{key_id}' is not an ItemData; "
                f"the synthesised key table would drop nothing and the floor "
                f"could not be unlocked",
                entity=plan_id, pointer=f"{fptr}/transitionKeyId"))
        elif key_item.value.get("type") != "key":
            issues.append(_issue(
                "warning", "TYPE_VALUE_UNKNOWN",
                f"floor {fi}: transitionKeyId '{key_id}' is type "
                f"'{key_item.value.get('type')}', not 'key'; only a key item "
                f"is consumed by a locked door",
                entity=plan_id, pointer=f"{fptr}/transitionKeyId"))

    issues.extend(_tile_part_issues(ds, plan_id, fi, fptr, floor))

    issues.extend(_stage_decoration_issues(ds, plan_id, fi, fptr, floor))

    issues.extend(_stage_tilemap_issues(ds, plan_id, fi, fptr, floor))

    issues.extend(_second_wave_issues(ds, plan_id, fi, fptr, floor))

    issues.extend(_depth_scale_issues(plan_id, fi, fptr, floor))

    issues.extend(_variant_tier_issues(ds, plan_id, fi, fptr, floor, arrival))
    return issues


def _slot_target_issues(ds: Dataset, plan_id: str, where: str, ptr: str,
                        item_id: str | None, table_id: str | None,
                        *, require_one: bool) -> list[dict]:
    """A loot slot that names an item OR a `pickOne` table, never both.

    OZX-1047 gave both `probabilistic` and `guaranteed` entries the choice, so
    one family can share a single small weight instead of each member needing
    its own and rounding to zero. The refusals are `LootTableDraw.Require`'s,
    and they are the same everywhere a slot points at a table: an ordinary
    table rolls each entry independently and so yields anywhere from nothing
    to everything, which makes the slot's count stop meaning anything.

    `require_one` separates the two callers. The assigner throws on a
    `guaranteed` entry naming neither (`hasItem == hasTable`); a
    `probabilistic` entry naming neither becomes a loot row that can never
    produce anything, which is quieter but not better.
    """
    issues: list[dict] = []
    if item_id and table_id:
        return [_issue(
            "error", "LOOT_SLOT_AMBIGUOUS",
            f"{where} names both itemId '{item_id}' and tableId '{table_id}'; a "
            f"slot drops one named thing or draws from one table, never both",
            entity=plan_id, pointer=ptr)]
    if not item_id and not table_id:
        if require_one:
            issues.append(_issue(
                "error", "LOOT_SLOT_EMPTY",
                f"{where} names neither itemId nor tableId, so nothing would drop",
                entity=plan_id, pointer=ptr))
        return issues

    if item_id:
        if ds.get("ItemData", item_id) is None:
            named_table = ds.get("LootTableData", item_id) is not None
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"{where}: itemId '{item_id}' is not an ItemData"
                + (" — it is a loot table; a slot that draws from a table uses "
                   "`tableId`" if named_table else ""),
                entity=plan_id, pointer=f"{ptr}/itemId"))
        return issues

    table = ds.get("LootTableData", table_id)
    if table is None:
        issues.append(_issue(
            "error", "LOOT_TABLE_MISSING",
            f"{where}: tableId '{table_id}' does not exist",
            entity=plan_id, pointer=f"{ptr}/tableId"))
    elif not table.value.get("pickOne"):
        issues.append(_issue(
            "error", "LOOT_SLOT_TABLE_NOT_PICKONE",
            f"{where}: tableId '{table_id}' is not pickOne; an ordinary table rolls "
            f"every entry independently, so it can yield none or all of them and "
            f"this slot's count stops meaning anything",
            entity=plan_id, pointer=f"{ptr}/tableId"))
    elif not (table.value.get("entries") or []):
        issues.append(_issue(
            "error", "LOOT_TABLE_EMPTY",
            f"{where}: tableId '{table_id}' has no entries; there is nothing for it "
            f"to draw",
            entity=plan_id, pointer=f"{ptr}/tableId"))
    return issues


# OZX-650 removed named waves from the encounter system; OZX-1380 removed
# the room binding (a guarantee is paid in a room the generator chooses, by
# the last kill of its opening wave). Neither field is declared any more, so
# JsonUtility drops them unread and the entry is left with no binding.
DEAD_BINDINGS = ("boundWaveLastEnemy", "boundRoomId")
GUARANTEED_BINDINGS = ("boundEnemyId", "boundStage")


def _floor_loot_issues(ds: Dataset, plan_id: str, fi: int,
                       fptr: str, floor: dict) -> list[dict]:
    """`floorLootPlan`'s reference edges — none of which were checked.

    `level_referenced_data.md` §0 lists every one of these as a required
    reference, and each fails quietly in its own way:

    - a dangling `probabilistic[].itemId` goes straight into the synthesised
      table as a `LootEntryData` (`RegisterScaledTable` does not look at it),
      so the table works and is simply missing one of its outcomes forever;
    - a `categoryCaps[].category` that is not a taxonomy node matches no item,
      so the cap reads as authored and does nothing — exactly what OZX-1044
      added a fail-fast for on `stageWeights.category`, which `categoryCaps`
      still lacks;
    - the `guaranteed[]` rules DO throw, but at generation time, which is the
      moment this tool exists to get ahead of.

    `probabilisticPresets` are not expanded here (OLE-10), so entries a preset
    contributes are not seen. That can only under-report.
    """
    plan = floor.get("floorLootPlan") or {}
    if not plan:
        return []
    lptr = f"{fptr}/floorLootPlan"
    stages = {s.get("stageType") for s in (floor.get("stageTypes") or [])}
    issues: list[dict] = []

    for pi, entry in enumerate(plan.get("probabilistic") or []):
        issues.extend(_slot_target_issues(
            ds, plan_id, f"floor {fi}: floorLootPlan.probabilistic[{pi}]",
            f"{lptr}/probabilistic/{pi}",
            entry.get("itemId") or None, entry.get("tableId") or None,
            require_one=False))

    for gi, entry in enumerate(plan.get("guaranteed") or []):
        gptr = f"{lptr}/guaranteed/{gi}"
        where = f"floor {fi}: floorLootPlan.guaranteed[{gi}]"
        issues.extend(_slot_target_issues(
            ds, plan_id, where, gptr,
            entry.get("itemId") or None, entry.get("tableId") or None,
            require_one=True))

        chance = entry.get("chance") or 0
        if chance < 0 or chance > 1:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_CHANCE_INVALID",
                f"{where}: chance is {chance}, outside (0, 1]. 0 means 'not "
                f"authored' (always); a negative or >1 value is a typo, not a "
                f"probability",
                entity=plan_id, pointer=f"{gptr}/chance"))

        dead = [b for b in DEAD_BINDINGS if entry.get(b)]
        for b in dead:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_BINDING_DEAD",
                f"{where} binds with `{b}`, which no longer exists"
                + (" — the encounter system has had no named waves since OZX-650"
                   if b == "boundWaveLastEnemy" else
                   " — OZX-1380 removed it; the generator picks the room") +
                ". Use boundEnemyId or boundStage",
                entity=plan_id, pointer=f"{gptr}/{b}"))
        bound = [b for b in GUARANTEED_BINDINGS if entry.get(b)]
        if len(bound) != 1 and not (dead and not bound):
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_BINDING_COUNT",
                f"{where} must have EXACTLY one binding "
                f"({' / '.join(GUARANTEED_BINDINGS)}); got {len(bound)}"
                + (f" ({', '.join(bound)})" if bound else ""),
                entity=plan_id, pointer=gptr))

        enemy = entry.get("boundEnemyId")
        if enemy and ds.get("EnemyData", enemy) is None:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"{where}: boundEnemyId '{enemy}' is not an EnemyData",
                entity=plan_id, pointer=f"{gptr}/boundEnemyId"))

        stage = entry.get("boundStage")
        if stage and stages and stage not in stages:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_STAGE_UNKNOWN",
                f"{where}: boundStage '{stage}' is not a stage this floor declares "
                f"({', '.join(sorted(s for s in stages if s)) or 'none'}), so no "
                f"room can carry it",
                entity=plan_id, pointer=f"{gptr}/boundStage"))


    known_categories = ds.taxonomy()
    for ci, entry in enumerate(plan.get("categoryCaps") or []):
        category = entry.get("category")
        cptr = f"{lptr}/categoryCaps/{ci}"
        if not category:
            issues.append(_issue(
                "error", "LOOT_CATEGORY_CAP_UNSCOPED",
                f"floor {fi}: floorLootPlan.categoryCaps[{ci}] names no category, so "
                f"the cap applies to nothing",
                entity=plan_id, pointer=cptr))
        elif known_categories["found"] and category not in known_categories["parentOf"]:
            # Read off the NODE list, not the item index: an interior node
            # holds no items of its own and is still a real node, which is
            # why `HasCategory` exists upstream as a separate question.
            issues.append(_issue(
                "error", "LOOT_CATEGORY_CAP_UNKNOWN",
                f"floor {fi}: floorLootPlan.categoryCaps[{ci}] names category "
                f"'{category}', which is not a node in item_taxonomy.json. It would "
                f"match no item, so the cap reads as authored and does nothing",
                entity=plan_id, pointer=f"{cptr}/category"))

    for si, entry in enumerate(plan.get("stageMustDrop") or []):
        sptr = f"{lptr}/stageMustDrop/{si}"
        stage = entry.get("stageType")
        if not stage:
            # InjectStageMustDrops throws on this one rather than skipping it.
            issues.append(_issue(
                "error", "LOOT_MUST_DROP_UNSCOPED",
                f"floor {fi}: floorLootPlan.stageMustDrop[{si}] names no stageType",
                entity=plan_id, pointer=sptr))
        elif stage not in STAGE_TYPES:
            issues.append(_issue(
                "warning", "TYPE_VALUE_UNKNOWN",
                f"floor {fi}: floorLootPlan.stageMustDrop[{si}] names stage "
                f"'{stage}', which is not a stage type; no room will match it",
                entity=plan_id, pointer=f"{sptr}/stageType"))
        for di, drop in enumerate(entry.get("drops") or []):
            dptr = f"{sptr}/drops/{di}"
            where = f"floor {fi}: floorLootPlan.stageMustDrop[{si}].drops[{di}]"
            # OZX-1109 gave a must-drop the same item-or-table choice as a
            # guaranteed entry: stageMustDrop pays EVERY room of its stage, so a
            # named itemId is the same thing in every room of every run.
            issues.extend(_slot_target_issues(
                ds, plan_id, where, dptr,
                drop.get("itemId") or None, drop.get("tableId") or None,
                require_one=True))
            chance = drop.get("chance") or 0
            if chance < 0 or chance > 1:
                issues.append(_issue(
                    "error", "LOOT_MUST_DROP_CHANCE_INVALID",
                    f"{where}: chance is {chance}, outside (0, 1]. 0 or omitted "
                    f"means every room of the stage gets it; a negative or >1 "
                    f"value is a typo, not a probability",
                    entity=plan_id, pointer=f"{dptr}/chance"))
    issues.extend(_depth_box_issues(ds, plan_id, fi, lptr, plan))
    issues.extend(_tip_skill_issues(ds, plan_id, fi, lptr, plan))
    issues.extend(_no_fight_stage_issues(plan_id, fi, fptr, floor, plan))
    return issues


# StageEnemyBudget.PathDepthCount: trunk 0, branch 1, sub-branch 2 — one
# entry per depth the tree generator makes (OZX-1558).
PATH_DEPTH_COUNT = 3


def _depth_scale_issues(plan_id: str, fi: int, fptr: str, floor: dict) -> list[dict]:
    """`pathDepthEnemyScale` — d(pathDepth), the share of a room's enemy budget
    it keeps at its depth (OZX-1558, `StageEnemyBudget.ValidateDepthScale`).

    It scales the stage budget, `spawnerMinionsPerRoom` and the rolled elite
    count. `LevelBasePlan.FromData` throws on any shape other than §5's:
    exactly one entry per depth, d(0) = 1 (enemiesPerRoom IS the spine's
    budget), each entry in (0, 1], never rising with depth. Empty = every
    depth keeps its whole budget.
    """
    scale = floor.get("pathDepthEnemyScale") or []
    if not scale:
        return []
    sptr = f"{fptr}/pathDepthEnemyScale"
    where = f"floor {fi}: pathDepthEnemyScale"

    def err(message, pointer=sptr):
        return [_issue("error", "LEVEL_DEPTH_SCALE_INVALID", f"{where} {message}",
                       entity=plan_id, pointer=pointer)]

    if len(scale) != PATH_DEPTH_COUNT:
        return err(f"has {len(scale)} entries; it lists one per path depth the generator "
                   f"makes (0 / 1 / 2), so exactly {PATH_DEPTH_COUNT}")
    if scale[0] != 1:
        return err(f"[0] is {scale[0]:g}; the spine is the reference (d(0) = 1) — "
                   f"enemiesPerRoom is the spine's budget", f"{sptr}/0")
    for d in range(1, len(scale)):
        if not (0 < scale[d] <= 1):
            return err(f"[{d}] is {scale[d]:g}; each depth keeps a share of the budget in "
                       f"(0, 1]", f"{sptr}/{d}")
        if scale[d] > scale[d - 1]:
            return err(f"rises from {scale[d - 1]:g} at depth {d - 1} to {scale[d]:g} at "
                       f"depth {d}; a far room is never harder than a nearer one",
                       f"{sptr}/{d}")
    return []


def _tip_skill_issues(ds: Dataset, plan_id: str, fi: int, lptr: str,
                      plan: dict) -> list[dict]:
    """`floorLootPlan.tipSkillTableId` — the tip skill (OZX-1561).

    One guaranteed draw from a pickOne skill roster (shipped: loot_skills),
    pinned to a branch tip the generator picks. `TipSkillTableFromData`
    refuses a blank or padded id; the table is otherwise looked up only when
    the pin pays, mid-fight, so it is held to the guaranteed-slot rules here.
    """
    table_id = plan.get("tipSkillTableId")
    if not table_id:
        return []
    tptr = f"{lptr}/tipSkillTableId"
    where = f"floor {fi}: floorLootPlan.tipSkillTableId"
    if not table_id.strip() or table_id.strip() != table_id:
        return [_issue("error", "LOOT_TIP_SKILL_INVALID",
                       f"{where} is {table_id!r}; name a pickOne skill roster with no "
                       f"surrounding whitespace, or leave it out for no tip skill",
                       entity=plan_id, pointer=tptr)]
    issues = _slot_target_issues(ds, plan_id, where, lptr, None, table_id, require_one=True)
    for issue in issues:
        if issue["pointer"] == f"{lptr}/tableId":
            issue["pointer"] = tptr
    return issues


def _depth_box_issues(ds: Dataset, plan_id: str, fi: int, lptr: str,
                      plan: dict) -> list[dict]:
    """`floorLootPlan.depthBoxTableId` + `depthRarityBumps` (OZX-1560).

    The depth box is one guaranteed draw from a pickOne equipment table,
    pinned to the floor's deepest off-spine room that can pay it; its
    equipment rises in rarity by that room's depth. Paid like a guaranteed
    pin, so the table is held to the same slot rules. `LevelBasePlan
    .DepthRarityBumpsFromData` throws unless the bumps name depth 1 and 2
    exactly once each, with non-negative weights summing above 0. Bumps
    with no table are not a fault: they are typically chapter-wide in
    floorDefaults while the table is per floor.
    """
    table_id = plan.get("depthBoxTableId") or None
    if not table_id:
        return []
    where = f"floor {fi}: floorLootPlan.depthBoxTableId"
    issues = _slot_target_issues(ds, plan_id, where, lptr, None, table_id, require_one=True)
    for issue in issues:
        if issue["pointer"] == f"{lptr}/tableId":
            issue["pointer"] = f"{lptr}/depthBoxTableId"

    bptr = f"{lptr}/depthRarityBumps"

    def err(message, pointer=bptr):
        issues.append(_issue("error", "LOOT_DEPTH_BUMPS_INVALID",
                             f"floor {fi}: floorLootPlan.depthRarityBumps {message}",
                             entity=plan_id, pointer=pointer))

    rows = plan.get("depthRarityBumps") or []
    if not rows:
        err("is missing, but depthBoxTableId names a table; say how far the box rises at "
            "depth 1 and depth 2 (a weight list over +0, +1, …)", f"{lptr}/depthBoxTableId")
        return issues
    seen: set[int] = set()
    for ri, row in enumerate(rows):
        rptr = f"{bptr}/{ri}"
        row = row or {}
        depth = row.get("pathDepth") or 0
        weights = row.get("weights") or []
        if not 1 <= depth < PATH_DEPTH_COUNT:
            err(f"[{ri}] names pathDepth {depth}; the box only ever lands off the spine, "
                f"at depth 1 or 2", f"{rptr}/pathDepth")
            continue
        if depth in seen:
            err(f"names pathDepth {depth} twice", f"{rptr}/pathDepth")
        seen.add(depth)
        if not weights:
            err(f"[{ri}] (pathDepth {depth}) has no weights", rptr)
        elif any(w < 0 for w in weights):
            err(f"[{ri}] (pathDepth {depth}) has a negative weight", f"{rptr}/weights")
        elif not sum(weights) > 0:
            err(f"[{ri}] (pathDepth {depth}) weights are all zero", f"{rptr}/weights")
    for depth in range(1, PATH_DEPTH_COUNT):
        if depth not in seen:
            err(f"has no entry for pathDepth {depth}")
    return issues


def _no_fight_stage_issues(plan_id: str, fi: int, fptr: str, floor: dict,
                           plan: dict) -> list[dict]:
    """A guarantee bound to a stage with no enemies cannot be paid (OZX-1109).

    `enemyBudgetCut: 1` (OZX-1089) makes the generator skip the stage's rooms,
    so not one launch exists there, and a `guaranteed` entry with
    `boundStage` is a pin on the stage's last-killed opening enemy —
    `ResolveStageRoom` throws when there is none. `stageMustDrop` lands a
    crate instead of a body, which is why it is the channel that works.

    `probabilistic` is NOT dead there, whatever the ticket feared: the
    stage's scaled table is stamped as `floorLootTableId` on every room, and
    `CargoSpawnAdapter` rolls a room's crates from it. Only the body half of
    it goes quiet, so a release `stageWeights` entry is live tuning of the
    crates and is not a finding.
    """
    dry = {s.get("stageType") for s in (floor.get("stageTypes") or [])
           if s.get("stageType") and (s.get("enemyBudgetCut") or 0) >= 1}
    if not dry:
        return []
    issues: list[dict] = []
    for gi, entry in enumerate(plan.get("guaranteed") or []):
        stage = entry.get("boundStage")
        if stage in dry:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_STAGE_NO_ENEMIES",
                f"floor {fi}: floorLootPlan.guaranteed[{gi}] binds to stage "
                f"'{stage}', which cuts its whole enemy budget (enemyBudgetCut 1). "
                f"A guaranteed entry is paid by the stage's last-killed opening "
                f"enemy and there is none, so generation throws. A stage with no "
                f"fight pays through stageMustDrop, which lands a crate",
                entity=plan_id,
                pointer=f"{fptr}/floorLootPlan/guaranteed/{gi}/boundStage"))
    return issues


def _stage_weight_issues(ds: Dataset, plan_id: str, fi: int,
                         fptr: str, floor: dict) -> list[dict]:
    """`floorLootPlan.stageWeights` — the per-stage loot multipliers.

    OZX-1044 added an optional `category` (a node of `item_taxonomy.json`,
    matched down its subtree), which is what lets a chapter say "peak drops
    1.4× of everything and 1.8× of gear". Before it, `stageWeights` could only
    say "peak drops more" — `RegisterScaledTable` multiplied every entry in
    the synthesised table by one scalar. The multiplier is now resolved per
    ENTRY, so weights inside one synthesised table are no longer in proportion
    to each other.

    `StageWeightResolver.Build` refuses three shapes at synthesis, and each is
    the kind that otherwise leaves a plan reading as tuned and behaving as
    untuned:

    - a negative weight;
    - a category that is not a taxonomy node — a typo matches no item and
      leaves the stage on its fallback weight without a word;
    - two entries for one `(stage, category)` pair, where the loser is simply
      unreachable and which of them wins would depend on authoring order.

    Resolution order, which anything recomputing these has to match: the
    item's own category, then each ancestor in turn, then the stage's
    category-free entry, then 1.0.
    """
    plan = floor.get("floorLootPlan") or {}
    entries = plan.get("stageWeights") or []
    if not entries:
        return []

    wptr = f"{fptr}/floorLootPlan/stageWeights"
    known = ds.taxonomy()
    issues: list[dict] = []
    seen: set[tuple] = set()

    for wi, entry in enumerate(entries):
        eptr = f"{wptr}/{wi}"
        stage = entry.get("stageType")
        if not stage:
            # The resolver skips these outright, so they are dead lines rather
            # than a failure — but a weight nobody applies is worth saying.
            issues.append(_issue(
                "warning", "LEVEL_STAGE_WEIGHT_UNSCOPED",
                f"floor {fi}: stageWeights[{wi}] has no stageType and is skipped",
                entity=plan_id, pointer=eptr))
            continue
        if stage not in STAGE_TYPES:
            issues.append(_issue(
                "warning", "TYPE_VALUE_UNKNOWN",
                f"floor {fi}: stageWeights[{wi}] names stage '{stage}', which is "
                f"not a stage type; no room will ever match it",
                entity=plan_id, pointer=f"{eptr}/stageType"))

        weight = entry.get("weight", 0)
        if weight < 0:
            issues.append(_issue(
                "error", "LEVEL_STAGE_WEIGHT_NEGATIVE",
                f"floor {fi}: stageWeights[{wi}] weight is {weight}; it must be "
                f">= 0 (0 means this stage drops nothing)",
                entity=plan_id, pointer=f"{eptr}/weight"))

        category = entry.get("category") or None
        if category:
            if not known["found"]:
                issues.append(_issue(
                    "warning", "LEVEL_STAGE_WEIGHT_UNCHECKED",
                    f"floor {fi}: stageWeights[{wi}] scopes its multiplier to "
                    f"category '{category}', but item_taxonomy.json is not loaded, "
                    f"so the name is unverified",
                    entity=plan_id, pointer=f"{eptr}/category"))
            elif category not in known["parentOf"]:
                issues.append(_issue(
                    "error", "LEVEL_STAGE_WEIGHT_CATEGORY_UNKNOWN",
                    f"floor {fi}: stageWeights[{wi}] names category '{category}', "
                    f"which is not a node in item_taxonomy.json. A misspelled "
                    f"category matches no item and leaves the stage on its fallback "
                    f"weight without a word",
                    entity=plan_id, pointer=f"{eptr}/category"))

        key = (stage, category or "")
        if key in seen:
            issues.append(_issue(
                "error", "LEVEL_STAGE_WEIGHT_DUPLICATE",
                f"floor {fi}: stageWeights has two entries for stage '{stage}'"
                + (f" and category '{category}'" if category
                   else " with no category")
                + "; one of them would never apply, and which one would depend on "
                  "authoring order",
                entity=plan_id, pointer=eptr))
        seen.add(key)
    return issues


def _skill_drop_cap_issues(ds: Dataset, plan_id: str, plan: dict) -> list[dict]:
    """`LevelBasePlanData.skillDropLevelCap` (OZX-1069).

    Once a skill reaches this level the chapter stops DROPPING it, on all four
    channels at once and per skill. It is not a level clamp: `UpgradeSkill` is
    unchanged and the ceiling is still `SkillData.maxLevel` — the cap only
    decides whether the game keeps handing the skill out.

    Two findings, and the second is the one nobody would work out unaided.

    `< 0` the engine throws on: 0 means "unauthored" and resolves to the
    shipped default, so a negative is a typo that would otherwise seal every
    skill in the chapter from the first room.

    A cap ABOVE every maxLevel this chapter can drop is silently inert —
    chapter_3's two skills top out at 2 and 3, so its filter never fires at
    all. Nothing distinguishes that from a chapter whose cap happens not to
    be reached yet, which is why it is worth saying out loud: the number is
    written, looks tuned, and does nothing.
    """
    cap = plan.get("skillDropLevelCap") or 0
    if cap < 0:
        return [_issue(
            "error", "LEVEL_SKILL_DROP_CAP_NEGATIVE",
            f"skillDropLevelCap is {cap}. It is a skill LEVEL, so the lowest "
            f"meaningful value is 1 (a skill stops dropping the moment it is "
            f"picked up); omit it, or write 0, for the default of "
            f"{SKILL_DROP_LEVEL_CAP_DEFAULT}",
            entity=plan_id, pointer="/skillDropLevelCap")]
    if not cap:
        # Unauthored resolves to the default, and calling the default inert
        # on every plan that never mentions the field would be noise.
        return []

    reachable = _droppable_skill_levels(ds, plan)
    if not reachable:
        return []
    highest = max(reachable.values())
    if cap > highest:
        top = ", ".join(f"{s} (maxLevel {lv})"
                        for s, lv in sorted(reachable.items(),
                                            key=lambda kv: -kv[1])[:4])
        return [_issue(
            "warning", "LEVEL_SKILL_DROP_CAP_UNREACHABLE",
            f"skillDropLevelCap is {cap}, above the highest maxLevel this chapter "
            f"can drop ({highest}), so the filter never fires — every skill keeps "
            f"dropping for the whole run. Droppable here: {top}",
            entity=plan_id, pointer="/skillDropLevelCap")]
    return []


def _droppable_skill_levels(ds: Dataset, plan: dict) -> dict:
    """skill id -> its `SkillData.maxLevel`, for every skill this plan drops.

    Walks each floor's `floorLootPlan.probabilistic` for entries whose
    `ItemData.type` is "skill", and resolves the SkillData the item installs.
    `ApplySkill` hands the ITEM's id to `AddPassive`, so `refId` is read here
    only as the fallback it is for authoring, never as the primary.

    Presets are not expanded — `probabilisticPresets` is OLE-10 and not
    implemented yet — so this under-counts on a plan that uses them. It is a
    hint either way: naming fewer skills can only make the cap look more
    reachable, never less, so it cannot produce a false finding.
    """
    out: dict = {}
    defaults = plan.get("floorDefaults") or {}
    for raw in plan.get("floors") or []:
        floor = plandefaults.merge_floor(defaults, raw)[0]
        for entry in ((floor.get("floorLootPlan") or {}).get("probabilistic") or []):
            item_id = entry.get("itemId")
            if not item_id:
                continue
            item = ds.get("ItemData", item_id)
            if item is None or item.value.get("type") != "skill":
                continue
            skill = (ds.get("SkillData", item_id)
                     or ds.get("SkillData", item.value.get("refId") or ""))
            if skill is None:
                continue
            level = skill.value.get("maxLevel") or 0
            if level:
                out[skill.id] = max(out.get(skill.id, 0), level)
    return out


def _enemy_budget_cut_issues(plan_id: str, fi: int, fptr: str,
                             floor: dict) -> list[dict]:
    """`stageTypes[].enemyBudgetCut` (OZX-1089).

    The SHARE of the floor's `enemiesPerRoom` this stage gives back — not a
    multiplier, and the direction is deliberate. JsonUtility reads an omitted
    float as 0, so a "0 means ×1" factor would read an authored `0` ("no
    enemies in this stage") as "take the full budget", which is the class of
    bug the field exists to fix. As a cut, omitted = 0 = nothing changes, and
    the dangerous value (1 = no enemies at all) has to be typed.

    The second rule is the one worth having. On a floor with an `enemyPool`
    the generator writes `inlineEncounter` into every room and the runner
    prefers it, so an authored `encounterId` there has always been dead text.
    A full cut makes the generator skip that room — which WAKES the dead text
    up and staffs the breather from a hand-written encounter. Two lines of one
    plan entry saying opposite things about one room is a mistake, not a
    precedence question.
    """
    has_pool = bool(floor.get("enemyPool"))
    issues: list[dict] = []
    for si, stage in enumerate(floor.get("stageTypes") or []):
        cut = stage.get("enemyBudgetCut") or 0
        sptr = f"{fptr}/stageTypes/{si}"
        if cut < 0 or cut > 1:
            issues.append(_issue(
                "error", "LEVEL_STAGE_BUDGET_CUT_INVALID",
                f"floor {fi}: stageTypes[{si}] ('{stage.get('stageType')}') sets "
                f"enemyBudgetCut {cut}. It is the SHARE of enemiesPerRoom this stage "
                f"gives back, so it lives in [0, 1]: 0 (or omit it) for the full "
                f"budget, 1 for a stage with no enemies",
                entity=plan_id, pointer=f"{sptr}/enemyBudgetCut"))
            continue
        if has_pool and cut >= 1 and stage.get("encounterId"):
            issues.append(_issue(
                "error", "LEVEL_STAGE_BUDGET_CUT_CONTRADICTS_ENCOUNTER",
                f"floor {fi}: stageTypes[{si}] ('{stage.get('stageType')}') cuts its "
                f"whole enemy budget but also names encounterId "
                f"'{stage['encounterId']}'. On a floor with an enemyPool the "
                f"generator normally overwrites that with a sampled inlineEncounter; "
                f"a full cut makes it skip the room, so the encounterId comes back to "
                f"life and staffs the very stage that asked for no enemies",
                entity=plan_id, pointer=f"{sptr}/encounterId"))
    return issues


def _cluster_issues(entity: str, where: str, ptr: str, source: dict,
                    *, is_spawner: bool = False,
                    via_spawner: bool = False) -> list[dict]:
    """OZX-1063 clumping — on a pool entry or on an encounter action.

    Both carry the same four fields (the sampler copies an entry's onto the
    step it synthesises), and both are validated the same way upstream, so
    one function serves `LevelPlanValidator.ValidateClusterShape` and
    `EncounterValidator.ValidateVariantsAndClump`.

    The radius is the load-bearing one. Inside a clump the executor's
    minimum-spacing guard is deliberately WAIVED — that waiver is what a
    clump is — so nothing else keeps the bodies off a single point.
    """
    lo = source.get("clusterSizeMin") or 0
    hi = source.get("clusterSizeMax") or 0
    radius = source.get("clusterRadius") or 0
    shared = bool(source.get("sharedRoot"))
    issues: list[dict] = []

    # OZX-1191: a shared-root tuft — every member ON the anchor, fanned over
    # an arc. `EncounterValidator.SharedRootProblem` is the one rule set for
    # both places the pair is authored (a pool entry is passed with
    # viaSpawner false, since its own bodies are placed directly).
    problem = _shared_root_problem(shared, source.get("fanArcDegrees") or 0, hi,
                                   radius, via_spawner)
    if problem:
        issues.append(_issue(
            "error", "CLUSTER_SHARED_ROOT_INVALID", f"{where}: {problem}",
            entity=entity, pointer=f"{ptr}/{'sharedRoot' if shared else 'fanArcDegrees'}"))

    if hi <= 1 and lo <= 1:
        if radius > 0:
            issues.append(_issue(
                "error", "CLUSTER_RADIUS_UNUSED",
                f"{where} sets clusterRadius {radius} but no clump size, so nothing "
                f"ever uses the radius. Set clusterSizeMin/Max, or drop it",
                entity=entity, pointer=f"{ptr}/clusterRadius"))
        return issues

    if lo > hi:
        issues.append(_issue(
            "error", "CLUSTER_SIZE_RANGE_INVALID",
            f"{where} has clusterSizeMin {lo} above clusterSizeMax {hi}",
            entity=entity, pointer=f"{ptr}/clusterSizeMin"))
    # A shared-root tuft stands on its anchor on purpose and has no ring, so
    # the radius rule is the one it is exempt from.
    if radius <= 0 and not shared:
        issues.append(_issue(
            "error", "CLUSTER_RADIUS_MISSING",
            f"{where} clumps up to {hi} bodies but clusterRadius is {radius}. "
            f"Inside a clump the launch's minimum-spacing guard is waived on "
            f"purpose, so the radius is the only thing keeping the bodies off one "
            f"point",
            entity=entity, pointer=f"{ptr}/clusterRadius"))

    if is_spawner:
        issues.append(_issue(
            "error", "CLUSTER_ON_SPAWNER_ANCHOR",
            f"{where} is a spawner anchor AND asks for clumps. The minions go out "
            f"through the viaSpawner channel, which places them at the spawner's own "
            f"emit anchors and never reads these fields, so the clump would apply to "
            f"the spawners themselves — say that deliberately or not at all",
            entity=entity, pointer=ptr))
    if via_spawner:
        issues.append(_issue(
            "error", "CLUSTER_ON_SPAWNER_CHANNEL",
            f"{where} routes through viaSpawner, which places bodies at the "
            f"spawner's emit anchors and never reads the clump fields",
            entity=entity, pointer=ptr))
    return issues


def _shared_root_problem(shared: bool, arc: float, size_max: int, radius: float,
                         via_spawner: bool) -> str | None:
    """`EncounterValidator.SharedRootProblem`, message for message."""
    if not shared:
        return (f"fanArcDegrees={arc:g} without sharedRoot — nothing reads the arc. "
                f"Set sharedRoot, or drop it") if arc else None
    if not (0 < arc <= 360):
        return (f"sharedRoot needs fanArcDegrees in (0, 360], got {arc:g}. At 0 every "
                f"member would grow in the same direction, one on top of the other")
    if size_max <= 1:
        return (f"sharedRoot needs clusterSizeMax > 1 (got {size_max}) — the flag groups "
                f"the members of a clump onto one root, and a clump of one has none to share")
    if radius > 0:
        return (f"sharedRoot and clusterRadius={radius:g} are exclusive: a shared-root "
                f"clump stands every member ON the anchor, a radius rings them around it")
    if via_spawner:
        return ("sharedRoot cannot ride a viaSpawner launch — the channel places what it "
                "produces at the spawner's emit anchors and never reads the flag")
    return None


def _variant_issues(ds: Dataset, entity: str, where: str, ptr: str,
                    representative: str | None, variants: list,
                    *, role_of=None, via_spawner: bool = False) -> list[dict]:
    """OZX-1063 variant groups — several forms of ONE creature in one entry.

    The entry is sampled, capped and counted as a single type; each body it
    places draws its concrete id from `{enemyId} + variantIds`, uniformly and
    with replacement. The three Blight-Sac plants were three entries with
    three weights and three caps, which made "2-3 sprouts in a patch"
    unsayable: the sampler counts entries, so a patch had to be one entry
    before it could have a size.

    `role_of` is passed only where the role rule applies (a pool entry). The
    group stands on ONE spawn layer, taken from the representative, so a
    variant with a different role would be dropped onto a layer that is not
    its own — silently, in whatever fraction of bodies the draw gave it.
    """
    issues: list[dict] = []
    if via_spawner and variants:
        issues.append(_issue(
            "error", "VARIANT_ON_SPAWNER_CHANNEL",
            f"{where} routes through viaSpawner and also names variantIds; the "
            f"channel emits the spawner's own minion type and never reads them",
            entity=entity, pointer=f"{ptr}/variantIds"))

    rep_role = role_of(representative) if (role_of and representative) else None
    for vi, variant in enumerate(variants):
        vptr = f"{ptr}/variantIds/{vi}"
        if not variant:
            issues.append(_issue(
                "error", "VARIANT_EMPTY_SLOT",
                f"{where}: variantIds[{vi}] is empty. The per-body draw is uniform "
                f"over the whole list, so an empty slot takes its share and that "
                f"share of the group fails to spawn",
                entity=entity, pointer=vptr))
            continue
        if variant == representative:
            issues.append(_issue(
                "error", "VARIANT_REPEATS_REPRESENTATIVE",
                f"{where}: variantIds[{vi}] repeats the entry's own enemyId "
                f"'{representative}'. The representative is already in the draw; "
                f"listing it again doubles its share, which is a weight expressed "
                f"by accident",
                entity=entity, pointer=vptr))
            continue
        if ds.get("EnemyData", variant) is None:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"{where}: variantIds[{vi}] names '{variant}', which is not an "
                f"EnemyData",
                entity=entity, pointer=vptr))
            continue
        if role_of is not None:
            variant_role = role_of(variant)
            if variant_role != rep_role:
                issues.append(_issue(
                    "error", "VARIANT_ROLE_MISMATCH",
                    f"{where}: representative '{representative}' has role "
                    f"'{rep_role}' but variant '{variant}' has role "
                    f"'{variant_role}'. The group takes ONE spawn layer, chosen from "
                    f"the representative, so the odd variant would stand on a layer "
                    f"that was never authored for it. Split it into its own pool "
                    f"entry, or give it the same role",
                    entity=entity, pointer=vptr))
    return issues


def _pool_variant_issues(ds: Dataset, plan_id: str, fi: int,
                         fptr: str, floor: dict) -> list[dict]:
    """Variant and clump fields on one floor's `enemyPool`."""
    def role_of(enemy_id):
        doc = ds.get("EnemyData", enemy_id)
        return doc.value.get("role") if doc else None

    issues: list[dict] = []
    for ei, entry in enumerate(floor.get("enemyPool") or []):
        eptr = f"{fptr}/enemyPool/{ei}"
        where = f"floor {fi}: enemyPool[{ei}] ('{entry.get('enemyId')}')"
        issues.extend(_variant_issues(
            ds, plan_id, where, eptr, entry.get("enemyId"),
            list(entry.get("variantIds") or []), role_of=role_of))
        issues.extend(_cluster_issues(
            plan_id, where, eptr, entry,
            is_spawner=bool(entry.get("minionId")
                            and (entry.get("minionsPerSpawner") or 0) > 0)))
    return issues


def _removed_plan_field_issues(plan_id: str, fi: int, fptr: str,
                               floor: dict) -> list[dict]:
    """Room-clear loot on a plan — gone with the channel (OZX-1381).

    `StageTypePlanData.lootPlanId`, `overflowLootPlanId` and
    `CavePlanData.lootPlanId` fed the room-clear table, which no longer
    exists. JsonUtility drops undeclared fields without a word, so each is a
    line that reads as tuned and does nothing.
    """
    found = [(f"{fptr}/stageTypes/{si}/lootPlanId", f"stageTypes[{si}].lootPlanId")
             for si, st in enumerate(floor.get("stageTypes") or []) if st.get("lootPlanId")]
    if floor.get("overflowLootPlanId"):
        found.append((f"{fptr}/overflowLootPlanId", "overflowLootPlanId"))
    found += [(f"{fptr}/caves/{ci}/lootPlanId", f"caves[{ci}].lootPlanId")
              for ci, cave in enumerate(floor.get("caves") or []) if (cave or {}).get("lootPlanId")]
    return [_issue(
        "error", "LEVEL_FIELD_REMOVED",
        f"floor {fi}: {name} feeds the room-clear loot channel, which OZX-1381 removed; the "
        f"field is dropped unread. Room loot comes from floorLootPlan (crates and bodies) and "
        f"guaranteed / stageMustDrop",
        entity=plan_id, pointer=pointer) for pointer, name in found]


def _rail_train_entry_issues(ds: Dataset, plan_id: str, fi: int,
                             fptr: str, floor: dict) -> list[dict]:
    """`enemyPool[].railTrainId` — a train as a pool type (OZX-1315).

    `LevelPlanValidator.ValidateRailTrainEntries`: the floor must run on rail
    (`railCart`, merged — every room then draws a rail tilemap), since a room
    that samples a train with no track throws as the player walks in; the
    entry is a train OR an enemy; and it carries none of the spawn-layer
    fields a rail launch never reads. The id must also name a train —
    upstream leaves that to the train data's own checks, but a typo here
    would otherwise surface only as a missing file at generation.
    """
    issues: list[dict] = []
    for ei, entry in enumerate(floor.get("enemyPool") or []):
        train = entry.get("railTrainId")
        if not train:
            continue
        eptr = f"{fptr}/enemyPool/{ei}"
        who = f"floor {fi}: enemyPool[{ei}] (railTrainId '{train}')"

        def err(code, message, pointer=eptr):
            issues.append(_issue("error", code, f"{who} {message}",
                                 entity=plan_id, pointer=pointer))

        if entry.get("enemyId"):
            err("LEVEL_RAIL_TRAIN_ENTRY_AMBIGUOUS",
                f"also names enemyId '{entry['enemyId']}'. An entry is one type: a train on "
                f"the rail or an enemy on a spawn layer — split it into two entries")
        if not floor.get("railCart"):
            err("LEVEL_RAIL_TRAIN_OFF_RAIL",
                "is on a floor without railCart, so its rooms may draw tilemaps with no rail "
                "and a room that samples this train has nowhere to stand it",
                f"{eptr}/railTrainId")
        if ds.get("RailTrainData", train) is None:
            err("DATA_REFERENCE_MISSING", "names no RailTrainData", f"{eptr}/railTrainId")
        unread = None
        if entry.get("variantIds"):
            unread = "variantIds"
        elif any(entry.get(k) for k in ("clusterSizeMin", "clusterSizeMax", "clusterRadius")):
            unread = "cluster fields"
        elif entry.get("sharedRoot") or entry.get("fanArcDegrees"):
            unread = "sharedRoot / fanArcDegrees"
        elif entry.get("minionId") or entry.get("minionsPerSpawner"):
            unread = "minionId / minionsPerSpawner"
        if unread:
            err("LEVEL_RAIL_TRAIN_FIELD_UNREAD",
                f"sets {unread}, which a train never reads: its cars are the ones its "
                f"RailTrainData lists, laid along the rail")
    return issues


def _minion_key(entry: dict) -> str | None:
    """`EnemyPoolEntry.MinionKey` (OZX-1504): the train for a train entry, else the enemy."""
    return entry.get("railTrainId") or entry.get("enemyId")


def _spawner_pair_issues(ds: Dataset, plan_id: str, fi: int,
                         fptr: str, floor: dict) -> list[dict]:
    """`enemyPool[].minionId` / `minionsPerSpawner` — the spawner channel.

    OZX-1032. A `category: "spawner"` enemy produces NOTHING on its own:
    `EnemyEmitController` only drains what an encounter queued into it through
    `EncounterAction.viaSpawner`, and `produceIntervalSeconds` merely paces
    that drain. A hand-authored encounter always writes the spawner and its
    minion step as a pair; a procedural floor had no way to say the second
    half, so a spawner dropped into an `enemyPool` came out as an inert body
    with the HP of a wall. These two fields are that second half, and every
    rule below guards a failure that is otherwise silent:

    - a half-declared pair reads as a spawner to a designer and as an ordinary
      enemy to the sampler, so the factory ships inert — the exact condition
      the channel was added to end;
    - a minion from outside the pool slips a type past every guarantee pool
      membership carries (one run meets it, its role has spawn cells on every
      tilemap the floor can draw, it carries an elite block), and surfaces as
      `EnemyService.Spawn` throwing mid-room;
    - a spawner producing itself, or another spawner, builds a chain whose
      population nothing bounds — `maxAlive` gates whole encounter steps and
      cannot stop it.
    """
    pool = floor.get("enemyPool") or []
    if not pool:
        return []

    spawner_ids = {e.get("enemyId") for e in pool
                   if e.get("minionId") and (e.get("minionsPerSpawner") or 0) > 0}
    # OZX-1504 `EnemyPoolEntry.MinionKey`: a minionId names a train entry by
    # its railTrainId, anything else by its enemyId.
    members = {_minion_key(e): e for e in reversed(pool)}
    member_ids = set(members)

    issues: list[dict] = []
    for ei, entry in enumerate(pool):
        eptr = f"{fptr}/enemyPool/{ei}"
        minion = entry.get("minionId") or None
        count = entry.get("minionsPerSpawner") or 0
        if not minion and count <= 0:
            continue
        who = f"floor {fi}: enemyPool[{ei}] ('{entry.get('enemyId')}')"

        if bool(minion) != (count > 0):
            issues.append(_issue(
                "error", "LEVEL_SPAWNER_PAIR_INCOMPLETE",
                f"{who} declares half a spawner pair: minionId={minion!r}, "
                f"minionsPerSpawner={count}. Both are required — with one missing "
                f"the entry is sampled as an ordinary enemy, and a spawner handed "
                f"no minions stands in the room producing nothing",
                entity=plan_id, pointer=eptr))
            continue

        if minion == entry.get("enemyId"):
            issues.append(_issue(
                "error", "LEVEL_SPAWNER_SELF_MINION",
                f"{who} names itself as its own minion. A spawner emitting itself "
                f"grows without bound; maxAlive gates whole encounter steps and "
                f"cannot stop it",
                entity=plan_id, pointer=f"{eptr}/minionId"))
        elif minion in spawner_ids:
            issues.append(_issue(
                "error", "LEVEL_SPAWNER_CHAINED",
                f"{who} has minionId '{minion}', which is itself a spawner on this "
                f"floor. Chained production has no population bound — keep a "
                f"spawner's minion an ordinary enemy",
                entity=plan_id, pointer=f"{eptr}/minionId"))
        elif minion not in member_ids:
            issues.append(_issue(
                "error", "LEVEL_SPAWNER_MINION_NOT_IN_POOL",
                f"{who} has minionId '{minion}', which is not in this floor's "
                f"enemyPool. The minion has to be a pool member so it inherits what "
                f"membership guarantees: one run meets it, its role has spawn cells "
                f"on every tilemap this floor can draw, and it carries an elite "
                f"block. Add it to the pool — weight 0 is not a way to hide it",
                entity=plan_id, pointer=f"{eptr}/minionId"))

        # OZX-1504: a train comes out of a rail factory and nothing else does —
        # `EnemyEntity.RequestProduceRailTrain` refuses a spawner without
        # `railFactory`, and a factory's emitter can only put a train on the rail.
        minion_entry = members.get(minion)
        minion_is_train = bool(minion_entry and minion_entry.get("railTrainId"))
        spawner = ds.get("EnemyData", entry.get("enemyId") or "")
        is_factory = bool(spawner and ((spawner.value.get("railFactory") or {}).get("enabled")))
        if minion_entry is not None and spawner is not None:
            if minion_is_train and not is_factory:
                issues.append(_issue(
                    "error", "LEVEL_SPAWNER_TRAIN_NOT_FROM_FACTORY",
                    f"{who} produces train '{minion}', but only a rail factory "
                    f"(EnemyData.railFactory.enabled) can put a train on the rail — the "
                    f"room throws when the line is requested",
                    entity=plan_id, pointer=f"{eptr}/minionId"))
            elif is_factory and not minion_is_train:
                issues.append(_issue(
                    "error", "LEVEL_SPAWNER_FACTORY_NOT_TRAIN",
                    f"{who} is a rail factory producing enemy '{minion}'. A rail factory only "
                    f"produces trains — name a train entry of this pool by its railTrainId",
                    entity=plan_id, pointer=f"{eptr}/minionId"))

        # Not a rule upstream (the pool check above already covers the pool),
        # but a minion naming nothing at all would be reported by neither.
        if minion and minion in member_ids and not minion_is_train \
                and ds.get("EnemyData", minion) is None:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"{who}: minionId '{minion}' does not exist",
                entity=plan_id, pointer=f"{eptr}/minionId"))

        # OZX-1064 made the line a request rather than a promise: it is now
        # charged to the floor's enemiesPerRoom AND capped by the MINION's
        # own maxPerRoom, because a minion is the same enemy arriving through
        # a second door. Whichever runs out first truncates it. So a
        # minionsPerSpawner above the minion's cap is a number that can never
        # be met — not an error (the spawner still shows up, producing fewer),
        # but the plan says one thing and the room does another, every time.
        #
        # OZX-1219 gives the lines their own allowance instead:
        # `spawnerMinionsPerRoom` > 0 is shared by every spawner in the room,
        # and the minion's maxPerRoom goes back to bounding only the bodies
        # placed directly. The cap above then says nothing about the line;
        # the allowance does — a spawner asking for more than the whole room's
        # allowance is truncated even when it is the only one drawn.
        quota = floor.get("spawnerMinionsPerRoom") or 0
        if quota > 0:
            if count > quota:
                issues.append(_issue(
                    "warning", "LEVEL_SPAWNER_LINE_ALWAYS_TRUNCATED",
                    f"{who} asks for {count} minions per spawner, above this floor's "
                    f"spawnerMinionsPerRoom of {quota} — the allowance every spawner in "
                    f"the room shares — so it is truncated every time, even alone",
                    entity=plan_id, pointer=f"{eptr}/minionsPerSpawner"))
            continue
        minion_cap = (minion_entry or {}).get("maxPerRoom") or 0
        if minion_cap and count > minion_cap:
            issues.append(_issue(
                "warning", "LEVEL_SPAWNER_LINE_ALWAYS_TRUNCATED",
                f"{who} asks for {count} minions per spawner, above '{minion}'s "
                f"maxPerRoom of {minion_cap}. Since OZX-1064 the line is capped by "
                f"the minion's own cap and charged to enemiesPerRoom, so it is "
                f"truncated every time — the room gets at most {minion_cap}",
                entity=plan_id, pointer=f"{eptr}/minionsPerSpawner"))
    return issues


DEFAULT_OVERFLOW_STAGE = "pressure"   # LevelBasePlanAssigner.DefaultOverflowStageType

# PoolSampler.StageCaps: distinct types one room of a stage may hold — both
# waves together (OZX-1342, OZX-1354). Other stages are uncapped.
DISTINCT_TYPE_CAP = {"teaching": 2, "building": 3, "pressure": 4, "peak": 5}


def _can_arrive_late(ds: Dataset, enemy_id: str | None) -> bool:
    """`EnemyData.CanArriveLate`: an emerge window or a teleport entrance
    (OZX-1552), and not spawnPlacement initial."""
    enemy = ds.get("EnemyData", enemy_id or "")
    if enemy is None:
        return False
    v = enemy.value
    return (((v.get("emergeDuration") or 0) > 0 or bool(v.get("teleportArrival")))
            and v.get("spawnPlacement") != "initial")


def _second_wave_issues(ds: Dataset, plan_id: str, fi: int,
                        fptr: str, floor: dict) -> list[dict]:
    """`stageTypes[].secondWave` (OZX-1354, `LevelPlanValidator.ValidateSecondWaves`).

    A wave on top of the opening one: rolled per room at `chance`, sized
    `ceil(share × the stage's budget)`, arriving by one of `triggers` —
    `cleared` (a follow-up) or `advance` (an ambush once the player is
    `distance` from the entry point; OZX-1353 replaced the early `depth`).
    Declared iff anything is written, since JsonUtility materialises an
    omitted block.

    The rule that matters is capacity. Only types that can arrive late (an
    emerge window or `teleportArrival`, not `spawnPlacement: initial`) may fill it, at most the
    stage's distinct cap of them, largest `maxPerRoom` first (0 = uncapped).
    Upstream throws rather than placing a smaller wave, because a wave cut to
    one body reads as a stray enemy. Spawner lines are not counted.
    """
    if floor.get("generatorType") == "static":
        return []
    issues: list[dict] = []
    for si, stage in enumerate(floor.get("stageTypes") or []):
        wave = stage.get("secondWave") or {}
        if not (wave.get("chance") or wave.get("share") or wave.get("triggers")):
            continue
        wptr = f"{fptr}/stageTypes/{si}/secondWave"
        where = f"floor {fi}: stageTypes[{si}] ('{stage.get('stageType')}') secondWave"

        def err(code, message, pointer=wptr):
            issues.append(_issue("error", code, f"{where}: {message}",
                                 entity=plan_id, pointer=pointer))

        chance, share = wave.get("chance") or 0, wave.get("share") or 0
        if not 0 < chance <= 1:
            err("LEVEL_SECOND_WAVE_INVALID", f"chance {chance:g} is outside (0, 1]", f"{wptr}/chance")
        if not 0 < share <= 1:
            err("LEVEL_SECOND_WAVE_INVALID", f"share {share:g} is outside (0, 1]", f"{wptr}/share")
        triggers = wave.get("triggers") or []
        if not triggers:
            err("LEVEL_SECOND_WAVE_INVALID",
                "no triggers — say how the wave arrives ('cleared' or 'advance')")
        for ti, trigger in enumerate(triggers):
            tptr = f"{wptr}/triggers/{ti}"
            trigger = trigger or {}
            kind, distance = trigger.get("kind"), trigger.get("distance") or 0
            if not (trigger.get("weight") or 0) > 0:
                err("LEVEL_SECOND_WAVE_INVALID", f"triggers[{ti}] weight must be > 0", tptr)
            if "depth" in trigger:
                err("LEVEL_SECOND_WAVE_INVALID",
                    f"triggers[{ti}] writes `depth`, which OZX-1353 replaced with `distance` "
                    f"(world units from the entry point); the old field is dropped unread",
                    f"{tptr}/depth")
            if kind == "advance":
                if not distance > 0:
                    err("LEVEL_SECOND_WAVE_INVALID",
                        f"triggers[{ti}]: 'advance' needs distance > 0 (world units from the "
                        f"entry point)", tptr)
            elif kind == "cleared":
                if distance:
                    err("LEVEL_SECOND_WAVE_INVALID",
                        f"triggers[{ti}]: distance {distance:g} on a 'cleared' trigger — only "
                        f"'advance' reads it", f"{tptr}/distance")
            else:
                err("LEVEL_SECOND_WAVE_INVALID",
                    f"triggers[{ti}]: kind '{kind}' — a second wave arrives by 'cleared' or "
                    f"'advance'", f"{tptr}/kind")

        pool = floor.get("enemyPool") or []
        cut = stage.get("enemyBudgetCut") or 0
        if not pool:
            err("LEVEL_SECOND_WAVE_NO_POOL", "the floor has no enemyPool, which a second wave is drawn from")
            continue
        if cut >= 1:
            err("LEVEL_SECOND_WAVE_NO_FIGHT",
                "the stage cuts its whole enemy budget, so there is no wave to follow")
            continue
        if not 0 < share <= 1:
            continue
        budget = _stage_budget(floor.get("enemiesPerRoom") or 0, cut)
        needed = math.ceil(share * budget - 1e-4)
        late = [e for e in pool if not e.get("railTrainId") and _can_arrive_late(ds, e.get("enemyId"))
                and all(_can_arrive_late(ds, v) for v in e.get("variantIds") or [])]
        caps = sorted(((e.get("maxPerRoom") or 0) or 10**9 for e in late), reverse=True)
        capacity = sum(caps[:DISTINCT_TYPE_CAP.get(stage.get("stageType"), len(caps))])
        if capacity < needed:
            err("LEVEL_SECOND_WAVE_CAPACITY",
                f"needs {needed} late-arriving bodies (ceil(share {share:g} × budget {budget})) "
                f"but the pool can hold only {capacity} — late-arrivable types: "
                f"{', '.join(e.get('enemyId') for e in late) or 'none'}. A type arrives late "
                f"with an emerge window or teleportArrival and no spawnPlacement 'initial'; lower share, raise "
                f"their maxPerRoom, or add such types")
    return issues


def _stage_tilemap_issues(ds: Dataset, plan_id: str, fi: int,
                          fptr: str, floor: dict) -> list[dict]:
    """Every stage an area deals has to have a tilemap to draw.

    `LevelPlanValidator.ValidateStageTilemaps`: a stage with no tilemap in
    the area's library is not an imbalance — `RoomTilemapQuery` throws the
    moment the player walks into such a room, naming the room and not the
    plan line. The generator also deals three stages the plan need not list:
    the overflow stage, `start` on area 0, and `boss` when the area names a
    boss — "exactly how a chapter shipped with no boss tilemap at all".

    `ValidateFullGroundTilemaps` (OZX-1340) on top: an area with
    `requireFullGround` draws only templates with no empty ground cell and
    falls back to a full-ground SUPERSET when its exact mask has none. A
    full-ground four-door template covers every mask, so one per dealt stage
    is what keeps any room from coming up empty.

    Static floors write their rooms by hand and are skipped, as upstream.
    """
    if floor.get("generatorType") == "static":
        return []
    library = tilemaps.for_theme(ds.tilemaps.facts(), floor.get("themeId"))
    if not library and not ds.tilemaps.facts():
        return []
    theme = floor.get("themeId")
    needed: list[tuple[str, str, str]] = [
        (s.get("stageType"), f"{fptr}/stageTypes/{si}", f"stageTypes[{si}] asks for")
        for si, s in enumerate(floor.get("stageTypes") or []) if s.get("stageType")]
    overflow = floor.get("overflowStageType") or DEFAULT_OVERFLOW_STAGE
    needed.append((overflow, f"{fptr}/overflowStageType", "falls back to overflow stage"))
    if fi == 0:
        needed.append(("start", fptr, "starts the run, so it deals"))
    if floor.get("bossId"):
        needed.append(("boss", f"{fptr}/bossId", "names a boss, so it deals"))

    issues: list[dict] = []
    offered = sorted({t["stageType"] or "any" for t in library})
    done: set = set()
    for stage, pointer, what in needed:
        if stage in done:
            continue
        done.add(stage)
        if not any(tilemaps.matches_stage(t["stageType"], stage) for t in library):
            issues.append(_issue(
                "error", "LEVEL_STAGE_TILEMAP_MISSING",
                f"floor {fi} {what} stage '{stage}', but theme '{theme}' has no tilemap for "
                f"it (it supplies: {', '.join(offered) or 'nothing'}); every such room throws "
                f"out of RoomTilemapQuery on entry",
                entity=plan_id, pointer=pointer))

    if floor.get("requireFullGround"):
        missing = [stage for stage in dict.fromkeys(s for s, _, _ in needed)
                   if not any(tilemaps.matches_stage(t["stageType"], stage)
                              and not t["hasEmptyGround"]
                              and t["openDoors"] == tilemaps.ALL_DOORS for t in library)]
        if missing:
            issues.append(_issue(
                "error", "LEVEL_FULL_GROUND_TILEMAP_MISSING",
                f"floor {fi} sets requireFullGround, but theme '{theme}' has no full-ground "
                f"four-door tilemap for stage(s) {', '.join(missing)}. A room whose exact mask "
                f"has no full-ground template falls back to a full-ground superset, and only "
                f"the four-door one covers every mask",
                entity=plan_id, pointer=f"{fptr}/requireFullGround"))
    return issues


def _stage_decoration_issues(ds: Dataset, plan_id: str, fi: int,
                             fptr: str, floor: dict) -> list[dict]:
    """`FloorPlanData.stageDecorations` (OZX-1291): dressing per stage.

    `AssignStageDecorations` copies an entry's `decorations` onto every
    generated non-cave room of that stage whose own list is empty (start and
    boss included), and throws on an entry with no stageType or a stage named
    twice. Each decoration then meets the room theme the same way a
    hand-placed one does, so the floor's effective `themeId` decides whether
    it draws. The list is an array, so a floor that writes one replaces the
    chapter's whole.
    """
    issues: list[dict] = []
    seen: set = set()
    theme = floor.get("themeId")
    for si, entry in enumerate(floor.get("stageDecorations") or []):
        sptr = f"{fptr}/stageDecorations/{si}"
        stage = (entry or {}).get("stageType")
        if not stage:
            issues.append(_issue(
                "error", "LEVEL_STAGE_DECORATION_UNSCOPED",
                f"floor {fi}: stageDecorations[{si}] names no stageType; every entry must "
                f"name the stage it dresses", entity=plan_id, pointer=sptr))
        elif stage in seen:
            issues.append(_issue(
                "error", "LEVEL_STAGE_DECORATION_DUPLICATE",
                f"floor {fi}: stageDecorations declares '{stage}' twice; one stage has one "
                f"dressing list", entity=plan_id, pointer=f"{sptr}/stageType"))
        elif stage not in STAGE_TYPES:
            issues.append(_issue(
                "warning", "TYPE_VALUE_UNKNOWN",
                f"floor {fi}: stageDecorations[{si}] names stage '{stage}', which is not a "
                f"stage type; no room will match it", entity=plan_id, pointer=f"{sptr}/stageType"))
        if stage:
            seen.add(stage)
        for di, deco in enumerate((entry or {}).get("decorations") or []):
            category = (deco or {}).get("decalCategory")
            if category:
                issues.extend(decal_category_issues(
                    ds, plan_id, f"floor {fi}: stageDecorations[{si}] ('{stage}') decoration {di}",
                    f"{sptr}/decorations/{di}/decalCategory", category, theme))
    return issues


def _tile_part_issues(ds: Dataset, plan_id: str, fi: int,
                      fptr: str, floor: dict) -> list[dict]:
    """`appearance.tilePartConfigs` — the floor's tile-group subset.

    Two different throws sit behind this one field:

    - `LevelBasePlanAssigner.AssignRoomAppearance` throws on an empty entry,
      at generation time.
    - the name is then stamped onto each room and resolved by
      `TileThemeConfig.FindTilePartConfig`; `RoomInstanceView` fail-fasts on a
      name the theme does not define, at room-load time.

    Omitting the field is the default (draw from the theme's full pool), so
    an absent or empty list is not a finding.
    """
    appearance = floor.get("appearance") or {}
    parts = appearance.get("tilePartConfigs")
    if not parts:
        return []

    aptr = f"{fptr}/appearance/tilePartConfigs"
    issues = []
    for pi, name in enumerate(parts):
        if not name or not str(name).strip():
            issues.append(_issue(
                "error", "LEVEL_TILEPART_EMPTY",
                f"floor {fi}: appearance.tilePartConfigs[{pi}] is empty; "
                f"AssignRoomAppearance throws on a blank entry",
                entity=plan_id, pointer=f"{aptr}/{pi}"))

    theme_id = floor.get("themeId")
    owned = ds.themes.tile_parts(theme_id)
    if not owned["found"]:
        # Could not walk themeId -> theme asset -> tileTheme. Saying nothing
        # would silently skip the check; claiming every name is wrong would be
        # worse. Report the inability instead.
        issues.append(_issue(
            "warning", "LEVEL_TILEPART_UNCHECKED",
            f"floor {fi}: cannot resolve themeId '{theme_id}' to a theme asset, "
            f"so its tilePartConfigs names are unverified",
            entity=plan_id, pointer=f"{fptr}/themeId"))
        return issues

    for pi, name in enumerate(parts):
        if not name or not str(name).strip():
            continue
        if name not in owned["all"]:
            issues.append(_issue(
                "error", "LEVEL_TILEPART_UNKNOWN",
                f"floor {fi}: '{name}' is not a TilePartConfig of theme "
                f"'{theme_id}'; RoomInstanceView throws when it cannot resolve "
                f"the name. Theme owns: {', '.join(owned['all']) or '(none)'}",
                entity=plan_id, pointer=f"{aptr}/{pi}"))
    return issues


def _skill_drop_issues(ds: Dataset, table_id: str, index: int,
                       eptr: str, item_id: str) -> list[dict]:
    """A dropped skill resolves by the ITEM's id, not by its `refId`.

    `ItemService.ApplySkill` hands `item.id` straight to
    `SkillService.AddPassive`, which throws when no SkillData carries that id.
    `refId` is declared on ItemData and read nowhere in the runtime, so an
    author who points it at the real skill and lets the ids diverge gets a
    drop that crashes on pickup — which is `sprint_engine` today.
    """
    item = ds.get("ItemData", item_id)
    if item is None or item.value.get("type") != "skill":
        return []
    # An inventory item is stashed rather than applied, so it never reaches
    # ApplySkill and none of this binds.
    if item.value.get("inventoryItem"):
        return []

    issues = []
    skill = ds.get("SkillData", item_id)
    if skill is None:
        ref = item.value.get("refId")
        hint = (f"; its refId names '{ref}', but refId is not read by the runtime"
                if ref and ref != item_id else "")
        issues.append(_issue("error", "LOOT_SKILL_ITEM_UNRESOLVED",
                             f"entry {index}: '{item_id}' is a skill item but no "
                             f"SkillData has that id, so picking it up throws in "
                             f"AddPassive{hint}",
                             entity=table_id, pointer=f"{eptr}/itemId"))
    elif skill.value.get("type") != "passive":
        # ApplySkill calls AddPassive unconditionally, so an active skill is
        # installed into the passive list. It does not throw, which is exactly
        # why this is worth saying out loud.
        issues.append(_issue("warning", "LOOT_SKILL_ITEM_NOT_PASSIVE",
                             f"entry {index}: '{item_id}' resolves to an active "
                             f"skill, but a drop is always added as a passive",
                             entity=table_id, pointer=f"{eptr}/itemId"))
    return issues


def _nested_table_issues(ds: Dataset, table_id: str, index: int,
                         eptr: str, nested: str) -> list[dict]:
    """A loot entry that draws its identity from another table (OZX-1047).

    Same two refusals `LootTableDraw.Require` makes everywhere a slot points
    at a table: it has to be `pickOne`, because an ordinary table rolls each
    entry independently and so yields anywhere from nothing to everything, and
    it has to have entries to draw from. Self-reference is checked here rather
    than upstream because the runtime resolves one level at a time and a cycle
    only shows up as a stack overflow at drop time.
    """
    doc = ds.get("LootTableData", nested)
    if doc is None:
        return [_issue("error", "LOOT_TABLE_MISSING",
                       f"entry {index}: tableId '{nested}' does not exist",
                       entity=table_id, pointer=f"{eptr}/tableId")]
    if nested == table_id:
        return [_issue("error", "LOOT_TABLE_CYCLE",
                       f"entry {index}: tableId '{nested}' is this table",
                       entity=table_id, pointer=f"{eptr}/tableId")]
    if not doc.value.get("pickOne"):
        return [_issue("error", "LOOT_ENTRY_TABLE_NOT_PICKONE",
                       f"entry {index}: tableId '{nested}' is not pickOne; an "
                       f"ordinary table rolls every entry independently, so this "
                       f"entry's count would stop meaning anything",
                       entity=table_id, pointer=f"{eptr}/tableId")]
    if not (doc.value.get("entries") or []):
        return [_issue("error", "LOOT_TABLE_EMPTY",
                       f"entry {index}: tableId '{nested}' has no entries; there "
                       f"is nothing for it to draw",
                       entity=table_id, pointer=f"{eptr}/tableId")]
    return []


def validate_loot(ds: Dataset, table_id: str) -> list[dict]:
    doc = ds.get("LootTableData", table_id)
    if doc is None:
        return [_issue("error", "LOOT_TABLE_MISSING", f"loot table '{table_id}' not found")]
    issues: list[dict] = []
    entries = doc.value.get("entries") or []
    pick_one = bool(doc.value.get("pickOne"))

    if not entries:
        issues.append(_issue("warning", "LOOT_TABLE_EMPTY",
                             "table has no entries; it will drop nothing",
                             entity=table_id, pointer="/entries"))

    for index, entry in enumerate(entries):
        eptr = f"/entries/{index}"
        item_id = entry.get("itemId")
        nested = entry.get("tableId")
        if item_id and nested:
            issues.append(_issue(
                "error", "LOOT_ENTRY_AMBIGUOUS",
                f"entry {index} names both itemId '{item_id}' and tableId "
                f"'{nested}'; an entry drops one named item or draws from one "
                f"table, never both",
                entity=table_id, pointer=eptr))
        elif nested:
            # OZX-1047: the entry's weight is still its own chance; what it
            # draws is one item from a nested pickOne table. The same two
            # refusals as a pin — see LootTableDraw.Require.
            issues.extend(_nested_table_issues(ds, table_id, index, eptr, nested))
        elif not item_id:
            issues.append(_issue("error", "LOOT_ITEM_MISSING",
                                 f"entry {index} has no itemId",
                                 entity=table_id, pointer=eptr))
        elif ds.get("ItemData", item_id) is None:
            issues.append(_issue("error", "LOOT_ITEM_MISSING",
                                 f"entry {index}: item '{item_id}' does not exist",
                                 entity=table_id, pointer=f"{eptr}/itemId"))
        else:
            issues.extend(_skill_drop_issues(ds, table_id, index, eptr, item_id))

        lo, hi = entry.get("minCount", 0), entry.get("maxCount", 0)
        if not (0 < lo <= hi):
            issues.append(_issue("error", "LOOT_COUNT_RANGE_INVALID",
                                 f"entry {index}: count range {lo}-{hi} is invalid "
                                 f"(needs 0 < min <= max)",
                                 entity=table_id, pointer=eptr))

        weight = entry.get("weight", 0)
        # Two different meanings: an independent table's weight IS a
        # percentage and must fit 0-100; a pickOne table's is a relative
        # share and may be any positive number.
        if pick_one:
            if weight <= 0:
                issues.append(_issue("error", "LOOT_WEIGHT_INVALID",
                                     f"entry {index}: pickOne weight must be positive",
                                     entity=table_id, pointer=f"{eptr}/weight"))
        elif not 0 <= weight <= 100:
            issues.append(_issue("error", "LOOT_WEIGHT_INVALID",
                                 f"entry {index}: weight {weight} is outside 0-100; on a "
                                 f"non-pickOne table each weight is its own percentage",
                                 entity=table_id, pointer=f"{eptr}/weight"))

    if pick_one and entries and sum(max(0, e.get("weight", 0)) for e in entries) <= 0:
        issues.append(_issue("error", "LOOT_WEIGHT_INVALID",
                             "pickOne table has no positive weight to choose from",
                             entity=table_id, pointer="/entries"))
    return issues


def validate_enemy(ds: Dataset, enemy_id: str) -> list[dict]:
    """`EnemyData`'s hatch channel (OZX-1063).

    An egg holds the room's doors shut until it opens, so everything about it
    fails late and expensively: the engine's own checks
    (`Game.Character.AI.HatchBrood.Validate`) run from `HatchState.Enter`,
    which is three seconds of incubation into a fight the player is already
    in. Every rule here is one of those, moved to the moment the file is
    edited — plus the cross-reference the engine cannot make, since it holds
    one enemy's data and not the set of ids that exist.

    There are two channels and they are the same decision said twice:
    `hatchEnemyId` is a single-entry pool, and `hatchPool` is the general
    form. Neither may quietly win — the reason is `dropTableIdOverride`
    (OZX-1041), where one field carrying two meanings took years to untangle.
    """
    doc = ds.get("EnemyData", enemy_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING",
                       f"enemy '{enemy_id}' not found")]
    return (_wall_clearance_issues(enemy_id, doc.value) + _hatch_issues(ds, enemy_id, doc.value)
            + _family_issues(ds, enemy_id, doc.value))


def family_of(enemy: dict, enemy_id: str) -> tuple[str, int]:
    """`EnemyData.FamilyId` / `Tier` (OZX-1427): own id and tier 1 when no family."""
    return (enemy.get("family") or enemy_id, (enemy.get("tier") or 0) if enemy.get("family") else 1)


def _family_issues(ds: Dataset, enemy_id: str, v: dict) -> list[dict]:
    """`family` / `tier` (OZX-1427): variants of one creature, weakest = 1.

    `EnemyFamily.ValidateAll` runs over the whole enemy library at boot and
    throws on any of these. Per enemy: the two come as a pair or not at all,
    and a tier is ≥ 1. Per family: its tiers are exactly 1..n, each on one
    variant (two on one tier leaves "which is stronger" undecided; a gap is
    a variant nobody wrote), and a family of more than one has every member
    write both — a member leaning on the own-id default while another names
    its id would be joined by accident. A family finding is reported on each
    member it involves, since each file is half of the mistake.
    """
    family, tier = v.get("family") or None, v.get("tier") or 0
    if tier < 0 or (family and tier == 0) or (not family and tier != 0):
        return [_issue(
            "error", "ENEMY_FAMILY_HALF_DECLARED",
            f"family {family!r} / tier {tier}: write both (tier ≥ 1, 1 the weakest variant) "
            f"or neither", entity=enemy_id, pointer="/family" if family else "/tier")]
    fid, _ = family_of(v, enemy_id)
    members = [d for d in ds.of_type("EnemyData") if family_of(d.value, d.id)[0] == fid]
    if len(members) == 1 and not family:
        return []
    issues: list[dict] = []
    if not family:
        issues.append(_issue(
            "error", "ENEMY_FAMILY_INCONSISTENT",
            f"other enemies name '{fid}' as their family, but '{enemy_id}' does not write "
            f"family/tier itself — every member of a family declares both",
            entity=enemy_id, pointer="/family"))
    tiers = sorted(family_of(d.value, d.id)[1] for d in members)
    if tiers != list(range(1, len(members) + 1)):
        roster = ", ".join(f"{d.id} tier {family_of(d.value, d.id)[1]}"
                           for d in sorted(members, key=lambda d: (family_of(d.value, d.id)[1], d.id)))
        issues.append(_issue(
            "error", "ENEMY_FAMILY_INCONSISTENT",
            f"family '{fid}' ({roster}): tiers must be exactly 1..{len(members)}, each on one "
            f"variant — no gaps, no shared tier", entity=enemy_id, pointer="/tier"))
    return issues


# The smallest single room: 16×10 cells, a cell one world unit, so its
# half-extents are (8, 5) — `EncounterActionExecutor` passes
# `Template.width/height * 0.5`.
ROOM_HALF_EXTENT_MIN = 5.0


def _wall_clearance_issues(enemy_id: str, v: dict) -> list[dict]:
    """`spawnMinWallDistance` / `wallRepelDistance` (OZX-1263), world units.

    0 / omitted is off, which is every enemy but broodwing. Placement admits
    only a spot this far from every room edge (`LaunchPlacement` →
    `RoomWallClearance.IsClear`) and clamps jitter into the inner rect,
    which THROWS when the room has none (`ClampInside`). A clearance at or
    past the smallest room's half-extent therefore leaves no spot there at
    all: every directly placed body is dropped like "no clear spot", and a
    jittered one throws. A larger composite room could still fit it, so this
    is a warning; a negative distance is an error.
    """
    issues: list[dict] = []
    for field in ("spawnMinWallDistance", "wallRepelDistance"):
        value = v.get(field) or 0
        if value < 0:
            issues.append(_issue(
                "error", "ENEMY_WALL_DISTANCE_NEGATIVE",
                f"{field} is {value:g}; it is a distance in world units, 0 for off",
                entity=enemy_id, pointer=f"/{field}"))
    clearance = v.get("spawnMinWallDistance") or 0
    if clearance >= ROOM_HALF_EXTENT_MIN:
        issues.append(_issue(
            "warning", "ENEMY_WALL_CLEARANCE_UNPLACEABLE",
            f"spawnMinWallDistance {clearance:g} reaches the middle of a standard 16×10 "
            f"room (half-height {ROOM_HALF_EXTENT_MIN:g}), so no spot there is that far from "
            f"every wall: each directly placed body is dropped, and a jittered one throws",
            entity=enemy_id, pointer="/spawnMinWallDistance"))
    return issues


def _hatch_issues(ds: Dataset, enemy_id: str, v: dict) -> list[dict]:
    single = v.get("hatchEnemyId") or None
    pool = [x for x in (v.get("hatchPool") or [])] if v.get("hatchPool") else []
    # An enemy that names neither is not an egg, and saying so about the other
    # 67 enemies in the corpus would bury the ones that are.
    if not single and not pool:
        return []

    issues: list[dict] = []
    if single and pool:
        issues.append(_issue(
            "error", "ENEMY_HATCH_CHANNEL_AMBIGUOUS",
            f"sets both hatchEnemyId '{single}' and a hatchPool of {len(pool)}; "
            f"they are the same decision said twice and neither wins — keep the "
            f"pool (a single-entry pool is what hatchEnemyId means) and clear "
            f"hatchEnemyId",
            entity=enemy_id, pointer="/hatchPool"))

    for i, entry in enumerate(v.get("hatchPool") or []):
        eptr = f"/hatchPool/{i}"
        if not entry:
            issues.append(_issue(
                "error", "ENEMY_HATCH_POOL_EMPTY_SLOT",
                f"hatchPool[{i}] is empty. The draw is uniform over the whole "
                f"list, so an empty slot still takes its share and that share of "
                f"the clutch silently fails to spawn",
                entity=enemy_id, pointer=eptr))
        elif ds.get("EnemyData", entry) is None:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"hatchPool[{i}] names '{entry}', which is not an EnemyData",
                entity=enemy_id, pointer=eptr))

    if single and ds.get("EnemyData", single) is None:
        issues.append(_issue(
            "error", "DATA_REFERENCE_MISSING",
            f"hatchEnemyId '{single}' is not an EnemyData",
            entity=enemy_id, pointer="/hatchEnemyId"))

    # `RollCount` clamps rather than throwing, so max below min is tuning
    # noise, not a contradiction — the max it actually uses is the min.
    lo = v.get("hatchCountMin") or 0
    hi = v.get("hatchCountMax") or 0
    effective_max = max(lo if lo > 1 else 1, hi if hi > 1 else 1)
    if hi and lo and hi < lo:
        issues.append(_issue(
            "warning", "ENEMY_HATCH_COUNT_RANGE_REVERSED",
            f"hatchCountMax {hi} is below hatchCountMin {lo}; the roll clamps to "
            f"{lo} rather than throwing, so the max is dead text",
            entity=enemy_id, pointer="/hatchCountMax"))

    if effective_max > 1 and (v.get("hatchSpreadRadius") or 0) <= 0:
        issues.append(_issue(
            "error", "ENEMY_HATCH_NO_SPREAD",
            f"hatches up to {effective_max} occupants but hatchSpreadRadius is "
            f"{v.get('hatchSpreadRadius') or 0}. The first occupant takes the egg's "
            f"own spot and the rest ring it, so with no radius the clutch comes out "
            f"inside itself — and these are rooted bodies, which never shove apart",
            entity=enemy_id, pointer="/hatchSpreadRadius"))

    return issues


def validate_all(ds: Dataset) -> list[dict]:
    issues = list(ds.problems)
    for doc in ds.of_type("LevelData"):
        issues += validate_level(ds, doc.id)
    for doc in ds.of_type("LevelBasePlanData"):
        issues += validate_plan(ds, doc.id)
    for doc in ds.of_type("EncounterData"):
        issues += validate_encounter(ds, doc.id)
    for doc in ds.of_type("EnemyData"):
        issues += validate_enemy(ds, doc.id)
    for doc in ds.of_type("LootTableData"):
        issues += validate_loot(ds, doc.id)
    return issues
