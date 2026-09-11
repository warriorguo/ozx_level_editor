"""Validation rules, weighted toward the failures that produce no error.

A broken level usually does not crash — it just quietly plays wrong. A door
whose twin is missing is a one-way door. An unknown `generatorType` becomes
`tree`. A cross-floor door with no `stairLinks` is a dead end. Those are the
rules worth having, and they are Errors, not Warnings.

Issue codes come from the TD's Appendix B; they are stable machine ids.
"""

from __future__ import annotations

from .dataset import DIRECTION_TWIN, DIRECTIONS, Dataset

GENERATOR_TYPES = {"tree", "cycle", "mixed", "static"}
STAGE_TYPES = {"start", "teaching", "building", "default",
               "pressure", "peak", "release", "boss", "exit"}
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
                issues.append(_issue("error", "LEVEL_ROOM_ID_DUPLICATE",
                                     f"duplicate roomId '{rid}'", entity=level_id,
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
                for name, dtype in (("lootTableId", "LootTableData"),
                                    ("mountedEnemyId", "EnemyData"),
                                    ("itemId", "ItemData")):
                    ref = p.get(name)
                    if ref and ds.get(dtype, ref) is None:
                        issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                                             f"{rid}: placement {pi} {name} '{ref}' does not exist",
                                             entity=level_id,
                                             pointer=f"{rptr}/staticPlacements/{pi}/{name}"))

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

                # A door to another floor is a dead end without paired stairs.
                if t_fi != fi:
                    stairs = room.get("stairLinks") or []
                    paired = any(s.get("toRoomId") == target for s in stairs)
                    if not paired:
                        issues.append(_issue(
                            "error", "LEVEL_CROSS_FLOOR_DOOR_NO_STAIR",
                            f"{rid}: door to '{target}' crosses from floor {fi} to {t_fi} "
                            f"with no matching stairLink — silently a dead end at runtime",
                            entity=level_id, pointer=dptr))

                if door.get("locked") and not door.get("keyId"):
                    issues.append(_issue("warning", "LEVEL_DOOR_LOCKED_NO_KEY",
                                         f"{rid}: door {di} is locked but names no keyId",
                                         entity=level_id, pointer=dptr))

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


def validate_encounter(ds: Dataset, encounter_id: str) -> list[dict]:
    doc = ds.get("EncounterData", encounter_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING",
                       f"encounter '{encounter_id}' not found")]
    issues: list[dict] = []
    for index, step in enumerate(doc.value.get("steps") or []):
        sptr = f"/steps/{index}"
        action = step.get("action")
        if action:
            verb = action.get("verb")
            # EncounterValidator accepts exactly one verb. "wait" is not a
            # verb — a wait step is one with no action at all.
            if verb != "launch":
                issues.append(_issue(
                    "error", "ENCOUNTER_ACTION_VERB_INVALID",
                    f"step {index}: verb '{verb}' is invalid; the only legal verb is "
                    f"'launch' (a wait step omits `action` entirely)",
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

            drop = action.get("dropTableIdOverride")
            if drop and ds.get("LootTableData", drop) is None:
                issues.append(_issue("error", "LOOT_TABLE_MISSING",
                                     f"step {index}: dropTableIdOverride '{drop}' does not exist",
                                     entity=encounter_id,
                                     pointer=f"{sptr}/action/dropTableIdOverride"))

        condition = step.get("condition")
        if condition:
            kind = condition.get("kind")
            if kind not in {"time", "cleared", "touch", "killed", "custom"}:
                issues.append(_issue("warning", "TYPE_VALUE_UNKNOWN",
                                     f"step {index}: unknown condition kind '{kind}'",
                                     entity=encounter_id, pointer=f"{sptr}/condition/kind"))
    return issues


def validate_plan(ds: Dataset, plan_id: str) -> list[dict]:
    doc = ds.get("LevelBasePlanData", plan_id)
    if doc is None:
        return [_issue("error", "DATA_REFERENCE_MISSING", f"plan '{plan_id}' not found")]
    issues: list[dict] = []
    for fi, floor in enumerate(doc.value.get("floors") or []):
        fptr = f"/floors/{fi}"
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
        boss = floor.get("bossId")
        if boss and ds.get("EnemyData", boss) is None:
            issues.append(_issue("error", "DATA_REFERENCE_MISSING",
                                 f"floor {fi}: bossId '{boss}' does not exist",
                                 entity=plan_id, pointer=f"{fptr}/bossId"))
    return issues


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
        if not item_id:
            issues.append(_issue("error", "LOOT_ITEM_MISSING",
                                 f"entry {index} has no itemId",
                                 entity=table_id, pointer=eptr))
        elif ds.get("ItemData", item_id) is None:
            issues.append(_issue("error", "LOOT_ITEM_MISSING",
                                 f"entry {index}: item '{item_id}' does not exist",
                                 entity=table_id, pointer=f"{eptr}/itemId"))

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


def validate_all(ds: Dataset) -> list[dict]:
    issues = list(ds.problems)
    for doc in ds.of_type("LevelData"):
        issues += validate_level(ds, doc.id)
    for doc in ds.of_type("LevelBasePlanData"):
        issues += validate_plan(ds, doc.id)
    for doc in ds.of_type("EncounterData"):
        issues += validate_encounter(ds, doc.id)
    for doc in ds.of_type("LootTableData"):
        issues += validate_loot(ds, doc.id)
    return issues
