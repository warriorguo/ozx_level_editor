"""Validation rules, weighted toward the failures that produce no error.

A broken level usually does not crash — it just quietly plays wrong. A door
whose twin is missing is a one-way door. An unknown `generatorType` becomes
`tree`. A `start` room past the first area cannot load. Those are the rules
worth having, and they are Errors, not Warnings.

Issue codes come from the TD's Appendix B; they are stable machine ids.
"""

from __future__ import annotations

from . import plandefaults, tilemaps
from .dataset import (DIRECTION_TWIN, DIRECTIONS, SKILL_DROP_LEVEL_CAP_DEFAULT,
                      Dataset)

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
            issues.extend(_pinned_door_issues(ds, level_id, rid, rptr, room))
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

            for name, dtype in (("encounterId", "EncounterData"),
                                ("lootPlanId", "LootTableData"),
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

                issues.extend(_placement_count_issues(level_id, rid, pi, pptr, p))

                # SkillPlatformSpawnAdapter throws on both of these when the
                # room spawns, so an unfinished skill platform takes the whole
                # room down rather than quietly placing nothing.
                if p.get("kind") == "skill":
                    skill = ds.get("SkillData", p.get("skillId") or "")
                    if not p.get("skillId"):
                        issues.append(_issue("error", "ROOM_PLACEMENT_SKILL_MISSING",
                                             f"{rid}: placement {pi} is kind 'skill' with no "
                                             f"skillId; the spawn adapter throws",
                                             entity=level_id, pointer=f"{pptr}/skillId"))
                    elif skill is not None and skill.value.get("type") != "passive":
                        issues.append(_issue("error", "ROOM_PLACEMENT_SKILL_NOT_PASSIVE",
                                             f"{rid}: placement {pi} grants '{p['skillId']}', which "
                                             f"is type '{skill.value.get('type')}'; a SkillPlatform "
                                             f"only grants passive skills",
                                             entity=level_id, pointer=f"{pptr}/skillId"))
                    elif skill is not None:
                        # The platform draws the skill's level-1 icon in its
                        # centre, and ResolveIcon throws twice over: once when
                        # iconKeys is empty, and again when the key it names
                        # will not load. Both are legal SkillData and illegal
                        # on a platform, so both are caught here.
                        icon = (skill.value.get("iconKeys") or [None])[0]
                        why = None
                        if not icon:
                            why = ("has no iconKeys; a SkillPlatform needs an icon to "
                                   "display and throws without one")
                        elif ds.sprites.guid_for_key(icon) is None:
                            # Verbatim, because ResourcesAssetService does no
                            # `@`-suffix fallback — a key that resolves only
                            # by dropping the suffix still throws in game.
                            why = (f"names icon '{icon}', which is not in ResourcesDB; "
                                   f"the platform cannot load it and throws")
                        if why:
                            issues.append(_issue("error", "ROOM_PLACEMENT_SKILL_NO_ICON",
                                                 f"{rid}: placement {pi} grants "
                                                 f"'{p['skillId']}', which {why}",
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
    room load. When the name resolves to several copies, a door finding is
    only made if every copy has it.
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
        return [_issue(
            "error", "LEVEL_PIN_TEMPLATE_MISSING",
            f"{rid}: templateId '{template}' names no tilemap; RoomLoadManager "
            f"throws when the room loads",
            entity=level_id, pointer=f"{rptr}/templateId")]
    per_copy = [tilemaps.pin_door_findings(room, t) for t in copies]
    common = [f for f in per_copy[0] if all(f in other for other in per_copy[1:])]
    return [_issue("error", code, f"{rid}: pinned '{template}' {message}",
                   entity=level_id, pointer=f"{rptr}{suffix}")
            for code, suffix, message in common]


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


# OZX-1057: only these adapters read `chance`; anything else throws rather
# than silently appearing every time. `countMin` is narrower still — see below.
CHANCE_HONOURING_KINDS = {"prop"}
# The side-wall fill is the only path that passes `countMin` through to its
# anchor chooser (`PropSpawnAdapter` → `SideWallPlacementLogic.ChooseAnchors`).
# The plain random fill next to it does not, so a floor is read there and then
# dropped — which is the failure this file exists for.
COUNTMIN_HONOURING_PLACEMENTS = {"sidewall"}


def _placement_count_issues(level_id: str, rid: str, pi: int,
                            pptr: str, p: dict) -> list[dict]:
    """`chance` and `countMin` on one static placement (OZX-1057).

    `count` has always been a ceiling — random fill places what the room can
    hold. These are the two ends that grew around it:

    - `countMin` is the floor below which the entry declines ENTIRELY, so a
      wall-mounted set-piece that would stand alone reads as a room rather
      than as clutter, and the cells go to whatever runs next.
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
        if count and count_min > count:
            issues.append(_issue(
                "error", "ROOM_PLACEMENT_COUNT_MIN_ABOVE_MAX",
                f"{rid}: placement {pi} has countMin {count_min} above count {count}. "
                f"count is the ceiling random fill takes what it can of, so a floor "
                f"above it can never be met and the entry is never placed",
                entity=level_id, pointer=f"{pptr}/countMin"))
        elif pinned:
            issues.append(_issue(
                "warning", "ROOM_PLACEMENT_COUNT_MIN_UNREAD",
                f"{rid}: placement {pi} pins its cells, so countMin is not read — a "
                f"pinned entry already names exactly what it wants",
                entity=level_id, pointer=f"{pptr}/countMin"))
        elif p.get("placement") not in COUNTMIN_HONOURING_PLACEMENTS:
            # Narrower than the field's own documentation, which says "random
            # fill". Verified against PropSpawnAdapter: the plain fill branch
            # never passes countMin on.
            issues.append(_issue(
                "warning", "ROOM_PLACEMENT_COUNT_MIN_UNREAD",
                f"{rid}: placement {pi} sets countMin {count_min} but is not "
                f"placement 'sidewall'; only the side-wall fill passes a floor to its "
                f"anchor chooser, so this one is read and then dropped",
                entity=level_id, pointer=f"{pptr}/countMin"))

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
            if not enemy_id:
                issues.append(_issue("error", "ENCOUNTER_ENEMY_MISSING",
                                     f"step {index}: launch with no enemyId",
                                     entity=encounter_id, pointer=f"{sptr}/action"))
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
            issues.extend(_variant_issues(
                ds, encounter_id, f"step {index}", aptr, action.get("enemyId"),
                list(action.get("variantIds") or []),
                via_spawner=bool(action.get("viaSpawner"))))
            issues.extend(_cluster_issues(
                encounter_id, f"step {index}", aptr, action,
                via_spawner=bool(action.get("viaSpawner"))))

    return issues


def _pin_issues(ds: Dataset, encounter_id: str, index: int,
                sptr: str, action: dict) -> list[dict]:
    """`EncounterAction.guaranteedDrop` — the prize pinned to a launch.

    OZX-1041 split the one `dropTableIdOverride` string into two fields
    because the two meanings kept overwriting each other: pinning a prize used
    to mean replacing the launch's drop table, which handed the prize to every
    enemy the launch spawned and cost them the loot they would have dropped.

    Every rule here is `EncounterValidator` / `LootTableDraw.Require`, moved
    forward to editing time, and every one of them guards a failure that is
    otherwise silent or arrives mid-room:

    - a half-filled pin pays nobody, and JsonUtility materialises the object
      whether or not it was authored, so `guaranteedDrop != null` proves
      nothing — a pin exists iff it names an item or a table;
    - an `itemId` that is really a loot table id drops nothing;
    - a `tableId` that is not `pickOne` rolls each entry independently, so the
      pin's count stops meaning anything;
    - a pin on a `viaSpawner` launch can be discarded with the request (the
      spawner dies, the room changes) — and the pin can be the floor's key.
    """
    pin = action.get("guaranteedDrop")
    if not isinstance(pin, dict):
        return []
    pptr = f"{sptr}/action/guaranteedDrop"
    item_id = pin.get("itemId") or None
    table_id = pin.get("tableId") or None
    count = pin.get("count") or 0
    chance = pin.get("chance") or 0
    issues: list[dict] = []

    if item_id and table_id:
        return [_issue(
            "error", "ENCOUNTER_PIN_AMBIGUOUS",
            f"step {index}: guaranteedDrop names both itemId '{item_id}' and "
            f"tableId '{table_id}'; a pin drops one named thing or draws from "
            f"one table, never both",
            entity=encounter_id, pointer=pptr)]

    if not item_id and not table_id:
        if count or chance:
            issues.append(_issue(
                "error", "ENCOUNTER_PIN_EMPTY",
                f"step {index}: guaranteedDrop names neither itemId nor tableId, "
                f"so nothing would be dropped",
                entity=encounter_id, pointer=pptr))
        return issues

    if item_id:
        if ds.get("ItemData", item_id) is None:
            named_table = ds.get("LootTableData", item_id) is not None
            issues.append(_issue(
                "error", "ENCOUNTER_PIN_ITEM_MISSING",
                f"step {index}: guaranteedDrop.itemId '{item_id}' is not an ItemData"
                + (" — it is a loot table; a pin that draws from a table uses "
                   "`tableId`" if named_table else ""),
                entity=encounter_id, pointer=f"{pptr}/itemId"))
    else:
        table = ds.get("LootTableData", table_id)
        if table is None:
            issues.append(_issue(
                "error", "LOOT_TABLE_MISSING",
                f"step {index}: guaranteedDrop.tableId '{table_id}' does not exist",
                entity=encounter_id, pointer=f"{pptr}/tableId"))
        elif not table.value.get("pickOne"):
            issues.append(_issue(
                "error", "ENCOUNTER_PIN_TABLE_NOT_PICKONE",
                f"step {index}: guaranteedDrop.tableId '{table_id}' is not pickOne; "
                f"an ordinary table rolls every entry independently, so it can yield "
                f"none or all of them and the pin's count stops meaning anything",
                entity=encounter_id, pointer=f"{pptr}/tableId"))
        elif not (table.value.get("entries") or []):
            issues.append(_issue(
                "error", "ENCOUNTER_PIN_TABLE_EMPTY",
                f"step {index}: guaranteedDrop.tableId '{table_id}' has no entries; "
                f"there is nothing for the pin to draw",
                entity=encounter_id, pointer=f"{pptr}/tableId"))

    if count < 1:
        issues.append(_issue(
            "error", "ENCOUNTER_PIN_COUNT_INVALID",
            f"step {index}: guaranteedDrop pins '{item_id or table_id}' with count "
            f"{count}; a pin that reaches no enemy is never what was meant",
            entity=encounter_id, pointer=f"{pptr}/count"))

    # 0 is "not authored" and fires every time. Outside (0, 1] is a mistake:
    # a literal 0 almost certainly means "remove this pin", and reading it as
    # "always" is the opposite of what was asked.
    if chance < 0 or chance > 1:
        issues.append(_issue(
            "error", "ENCOUNTER_PIN_CHANCE_INVALID",
            f"step {index}: guaranteedDrop.chance is {chance}; it must be in (0, 1], "
            f"or omitted for a pin that always fires. To switch a pin off, delete it",
            entity=encounter_id, pointer=f"{pptr}/chance"))

    if action.get("viaSpawner"):
        issues.append(_issue(
            "error", "ENCOUNTER_PIN_ON_SPAWNER_CHANNEL",
            f"step {index}: guaranteedDrop cannot ride a viaSpawner launch. A channel "
            f"request can be legitimately discarded — the spawner is killed, "
            f"emitMaxTotal is spent, the room changes — so the pin would be lost in "
            f"silence, and a pin can be the key the floor is gated on",
            entity=encounter_id, pointer=pptr))
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
    by_pointer: dict = {}
    for fi, (floor, source) in enumerate(merged):
        for issue in _floor_plan_issues(ds, plan_id, fi, floor):
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


def _area_category(theme_id: str | None) -> str:
    """`AreaCategory.OfTheme`: `themes/X` → `X`, no theme → normal."""
    if not theme_id:
        return "normal"
    return theme_id.rsplit("/", 1)[-1]


def _floor_plan_issues(ds: Dataset, plan_id: str, fi: int,
                       floor: dict) -> list[dict]:
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
    if stages and not (stages & {"teaching", "building"}):
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
    issues.extend(_spawner_pair_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_pool_variant_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_stage_weight_issues(ds, plan_id, fi, fptr, floor))
    issues.extend(_floor_loot_issues(ds, plan_id, fi, fptr, floor))

    # The same entries a room carries, applied to every generated room of
    # this floor whose own `staticPlacements` came out empty — and where
    # `chance` / `countMin` are actually authored today, since a
    # procedural floor has no hand-written rooms to put them on.
    for pi, p in enumerate(floor.get("defaultStaticPlacements") or []):
        pptr = f"{fptr}/defaultStaticPlacements/{pi}"
        issues.extend(_placement_count_issues(
            plan_id, f"floor {fi}", pi, pptr, p))
    boss = floor.get("bossId")
    if boss and ds.get("EnemyData", boss) is None:
        issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                             f"floor {fi}: bossId '{boss}' does not exist",
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


# OZX-650 removed named waves from the encounter system, and the assigner
# throws on sight rather than trying to resolve one.
DEAD_BINDING = "boundWaveLastEnemy"
GUARANTEED_BINDINGS = ("boundEnemyId", DEAD_BINDING, "boundRoomId", "boundStage")


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
    # Only a hand-authored floor has room ids anyone could bind to; a
    # procedural floor's are invented at generation and are not authorable.
    authored_rooms = {r.get("roomId") for r in (floor.get("rooms") or [])}
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

        bound = [b for b in GUARANTEED_BINDINGS if entry.get(b)]
        if len(bound) != 1:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_BINDING_COUNT",
                f"{where} must have EXACTLY one binding "
                f"({' / '.join(GUARANTEED_BINDINGS)}); got {len(bound)}"
                + (f" ({', '.join(bound)})" if bound else ""),
                entity=plan_id, pointer=gptr))
        elif bound[0] == DEAD_BINDING:
            issues.append(_issue(
                "error", "LOOT_GUARANTEED_BINDING_DEAD",
                f"{where} binds with `{DEAD_BINDING}`, which is no longer supported "
                f"— the encounter system has had no named waves since OZX-650. Use "
                f"boundEnemyId",
                entity=plan_id, pointer=f"{gptr}/{DEAD_BINDING}"))

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

        room = entry.get("boundRoomId")
        if room and authored_rooms and room not in authored_rooms:
            issues.append(_issue(
                "error", "DATA_REFERENCE_MISSING",
                f"{where}: boundRoomId '{room}' is not a room on this floor",
                entity=plan_id, pointer=f"{gptr}/boundRoomId"))

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
    issues.extend(_no_fight_stage_issues(plan_id, fi, fptr, floor, plan))
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
    issues: list[dict] = []

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
    if radius <= 0:
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
    member_ids = {e.get("enemyId") for e in pool}

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

        # Not a rule upstream (the pool check above already covers the pool),
        # but a minion naming nothing at all would be reported by neither.
        if minion and minion in member_ids and ds.get("EnemyData", minion) is None:
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
        minion_entry = next((e for e in pool if e.get("enemyId") == minion), None)
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
    v = doc.value
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
