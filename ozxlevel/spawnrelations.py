"""Who may spawn whom, and what a generated floor lets an enemy bring in on its
own — the Python twin of `Game.Contracts/Data/EnemyData.cs` (`SpawnRelations`
OZX-1779 / OZX-1792, `FloorPoolProducts` / `HatchProducts` OZX-1781 /
OZX-1794, `EnemyData.FamilyId` / `EmitsMinions` / `IsRailFactory`) and of
`Game.Level/LevelBasePlan.cs` `SpawnerMinions.Candidates` (OZX-1780).

Every helper reads raw EnemyData JSON values (dicts), so the validator and the
plan view share one reading of each rule. Static floors and hand-written
levels are never narrowed: only a generated floor stamps `poolEnemyIds`.
"""

from __future__ import annotations


def family_of(enemy: dict, enemy_id: str) -> tuple[str, int]:
    """`EnemyData.FamilyId` / `Tier` (OZX-1427): own id and tier 1 when no family."""
    return (enemy.get("family") or enemy_id, (enemy.get("tier") or 0) if enemy.get("family") else 1)


def family_problem(enemy: dict) -> bool:
    """`EnemyFamily.ProblemOf` is non-null: a half or negative declaration."""
    family, tier = enemy.get("family") or None, enemy.get("tier") or 0
    return tier < 0 or (bool(family) and tier == 0) or (not family and tier != 0)


def emits_minions(enemy: dict) -> bool:
    """`EnemyData.EmitsMinions`: a spawner, a mobileSpawner or a producesMinions enemy."""
    return (str(enemy.get("category") or "").lower() == "spawner"
            or bool(enemy.get("mobileSpawner")) or bool(enemy.get("producesMinions")))


def is_rail_factory(enemy: dict) -> bool:
    """`EnemyData.IsRailFactory`."""
    return bool((enemy.get("railFactory") or {}).get("enabled"))


def can_emit(producer: dict, minion: dict, minion_id: str) -> bool:
    """`SpawnRelations.CanEmit`: the minion's family is in spawnableFamilies, or
    (OZX-1792) its own id is in spawnableEnemies. Never for a rail factory."""
    if not emits_minions(producer) or is_rail_factory(producer):
        return False
    return (family_of(minion, minion_id)[0] in (producer.get("spawnableFamilies") or [])
            or minion_id in (producer.get("spawnableEnemies") or []))


def can_emit_rail_unit(producer: dict, unit_id: str) -> bool:
    """`SpawnRelations.CanEmitRailUnit` (OZX-1792): a train or rail oil tank."""
    return is_rail_factory(producer) and unit_id in (producer.get("spawnableRailUnits") or [])


def lists(producer: dict) -> str:
    """`spawnableFamilies are [..], spawnableEnemies [..]`, as upstream words it."""
    return (f"its spawnableFamilies are [{', '.join(producer.get('spawnableFamilies') or [])}], "
            f"spawnableEnemies [{', '.join(producer.get('spawnableEnemies') or [])}]")


def relation_problem(enemy: dict, enemy_id: str, families: set, ids: set) -> tuple | None:
    """`SpawnRelations.ProblemOf`: (field, message) for a malformed declaration, or None."""
    fams = list(enemy.get("spawnableFamilies") or [])
    mons = list(enemy.get("spawnableEnemies") or [])
    rail = list(enemy.get("spawnableRailUnits") or [])

    def duplicate(field, items):
        seen = set()
        for x in items:
            if not x:
                return field, f"'{enemy_id}' {field} has an empty entry."
            if x in seen:
                return field, f"'{enemy_id}' {field} lists '{x}' twice."
            seen.add(x)
        return None

    if not emits_minions(enemy):
        if fams or mons or rail:
            field = ("spawnableFamilies" if fams else "spawnableEnemies" if mons
                     else "spawnableRailUnits")
            return field, (f"'{enemy_id}' declares spawnable products but emits nothing (not a "
                           f"spawner, mobileSpawner or producesMinions) — the list would never be read.")
        return None
    if is_rail_factory(enemy):
        if fams or mons:
            return ("spawnableFamilies" if fams else "spawnableEnemies",
                    f"'{enemy_id}' is a rail factory — it emits rail units, not enemies; drop "
                    f"spawnableFamilies / spawnableEnemies.")
        if rail:
            return duplicate("spawnableRailUnits", rail)
        return ("spawnableRailUnits", f"'{enemy_id}' is a rail factory but declares no "
                f"spawnableRailUnits — nothing it is handed could pass.")
    if rail:
        return "spawnableRailUnits", f"'{enemy_id}' declares spawnableRailUnits but is no rail factory."
    if not fams and not mons:
        return ("spawnableFamilies", f"'{enemy_id}' emits minions but declares neither "
                f"spawnableFamilies nor spawnableEnemies — nothing it is handed could pass "
                f"SpawnRelations.CanEmit.")
    dup = duplicate("spawnableFamilies", fams) or duplicate("spawnableEnemies", mons)
    if dup:
        return dup
    for f in fams:
        if f not in families:
            return ("spawnableFamilies", f"'{enemy_id}' spawnableFamilies names '{f}', which is no "
                    f"enemy's family (a family id, or the id of an enemy without a family).")
    for m in mons:
        if m not in ids:
            return "spawnableEnemies", f"'{enemy_id}' spawnableEnemies names '{m}', which is no enemy."
    return None


def is_spawner_entry(entry: dict) -> bool:
    """`EnemyPoolEntry.IsSpawner` (OZX-1780): minionsPerSpawner > 0, minionId or not."""
    return (entry.get("minionsPerSpawner") or 0) > 0


def minion_candidates(pool: list, spawner: int, enemy_of) -> list | None:
    """`SpawnerMinions.Candidates` (OZX-1780): indices of the floor's pool
    entries the producer may emit — not itself, not a train, not another
    spawner. `enemy_of(id)` returns the EnemyData value or None; None comes
    back when the producer or a pool member has no EnemyData (upstream throws,
    and DATA_REFERENCE_MISSING already reports it)."""
    producer = enemy_of(pool[spawner].get("enemyId") or "")
    if producer is None:
        return None
    out = []
    for m, entry in enumerate(pool):
        if m == spawner or entry.get("railTrainId") or is_spawner_entry(entry):
            continue
        minion = enemy_of(entry.get("enemyId") or "")
        if minion is None:
            return None
        if can_emit(producer, minion, entry.get("enemyId")):
            out.append(m)
    return out


def narrow(own: list, allowed: list | None) -> list:
    """`FloorPoolProducts.Narrow`: None allowed (hand-written floor) narrows nothing."""
    if allowed is None:
        return list(own)
    return [i for i in own if i in allowed]


def hatch_products(egg: dict) -> list:
    """`HatchProducts.Of`: hatchPool, else the one hatchEnemyId."""
    if egg.get("hatchPool"):
        return list(egg["hatchPool"])
    return [egg["hatchEnemyId"]] if egg.get("hatchEnemyId") else []


def death_heir(enemy: dict) -> list:
    """`FloorPoolProducts.DeathHeirOf`."""
    return [enemy["deathSpawnEnemyId"]] if enemy.get("deathSpawnEnemyId") else []

