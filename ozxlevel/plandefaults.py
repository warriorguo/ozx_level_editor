"""`LevelBasePlanData.floorDefaults` (OZX-1011): a chapter-level default floor.

A plan's floors write only what differs from their siblings; everything they
leave out comes from `floorDefaults`, which has the same shape as a floor.
This is the Python twin of `Game.Level.FloorPlanDefaults.Merge` — the only
implementation of the rule upstream, and this has to be the only one here,
because a validator and a view that merged differently would disagree about
what a floor IS.

The rule, from `FloorPlanDefaults.cs`:

- **per field, one level deep, arrays are leaves.** Every field inherits on
  its own; `floorLootPlan` is entered and its five lists inherit on their
  own; `treeShape` / `appearance` / `elitePlan` are inherited or authored
  WHOLE, each by its own discriminator, because their point is that a meant
  zero stays zero.
- **an authored array replaces, never appends.**
- **"did the floor write it" is read off the value**, never off key presence:
  JsonUtility materialises omitted fields, so 0, "" and [] mean "not written".
  Python could see more (whether the key is there), and must not use it — the
  two readings only differ on an explicit 0 / "" / [], which the runtime
  reads as absent.
- `endsRunOnClear`, `elevation` and `rooms` are never inherited, and a
  default that sets one is refused (`FloorPlanDefaults.Validate`).
- `railCart` / `requireFullGround` are bools, which cannot be "unset": a floor
  can switch one ON over the default, never off.

Every merged field is tagged with where it came from, because a finding (or
an edit) on an inherited value belongs to `/floorDefaults`, not to a floor
that never wrote it.
"""

from __future__ import annotations

# The three blocks inherited whole, and the discriminator that says the floor
# declared one (OZX-1006 / OZX-1013).
WHOLE_BLOCKS = {
    "treeShape": lambda b: (b.get("trunkLengthMax") or 0) > 0,
    "appearance": lambda b: bool(b.get("tilePartConfigs")),
    "elitePlan": lambda b: bool(b.get("perStage")),
}

# Never taken from the defaults; `refused_defaults` says why each one is
# refused when it is written there.
NEVER_INHERITED = ("endsRunOnClear", "elevation", "rooms")

# Bools: a floor turns them on, or inherits.
OR_FLAGS = ("railCart", "requireFullGround")

LOOT_PLAN = "floorLootPlan"
LOOT_LISTS = ("probabilisticPresets", "probabilistic", "guaranteed",
              "categoryCaps", "stageWeights", "stageMustDrop")

FLOOR = "floor"
DEFAULTS = "defaults"


def written(value) -> bool:
    """The runtime's reading of "the author wrote this": not 0, "", [] or null."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def _declared(key: str, value) -> bool:
    test = WHOLE_BLOCKS.get(key)
    if test is not None:
        return isinstance(value, dict) and test(value)
    return written(value)


def merge_floor(defaults: dict | None, floor: dict) -> tuple[dict, dict]:
    """(merged floor, source) — `source[key]` is FLOOR or DEFAULTS.

    Loot lists are tagged `"floorLootPlan/<list>"`. A key neither side wrote
    is absent from `source`. The merged dict shares its values with the
    inputs; it is a projection for reading, never something to write back.
    """
    defaults = defaults or {}
    merged: dict = {}
    source: dict = {}
    for key in list(floor) + [k for k in defaults if k not in floor]:
        if key == LOOT_PLAN:
            continue
        mine = floor.get(key)
        if key in NEVER_INHERITED:
            if key in floor:
                merged[key] = mine
                source[key] = FLOOR
            continue
        if key in OR_FLAGS:
            if mine:
                merged[key], source[key] = True, FLOOR
            elif defaults.get(key):
                merged[key], source[key] = True, DEFAULTS
            elif key in floor:
                merged[key], source[key] = mine, FLOOR
            continue
        if _declared(key, mine):
            merged[key] = mine
            source[key] = FLOOR
        elif _declared(key, defaults.get(key)):
            merged[key] = defaults[key]
            source[key] = DEFAULTS
        elif key in floor:
            merged[key] = mine
            source[key] = FLOOR

    loot, loot_source = merge_loot_plan(defaults.get(LOOT_PLAN), floor.get(LOOT_PLAN))
    if loot is not None:
        merged[LOOT_PLAN] = loot
        source.update({f"{LOOT_PLAN}/{k}": v for k, v in loot_source.items()})
    return merged, source


def merge_loot_plan(defaults: dict | None, floor: dict | None) -> tuple[dict | None, dict]:
    """The one block the merge enters: five independent authoring channels.

    `probabilisticPresets` travels with `probabilistic` rather than on its
    own. Upstream expands presets into `probabilistic` BEFORE merging, so a
    floor that names a preset and no items still "wrote items"; reading the
    unexpanded JSON, that means either list being written decides for both.
    Inheriting them apart would let a floor that lists its own items claim
    the chapter's consumable tier.
    """
    if not defaults and not floor:
        return (floor if floor is not None else defaults), {}
    defaults = defaults or {}
    floor = floor or {}
    merged: dict = {}
    source: dict = {}
    floor_wrote_items = (written(floor.get("probabilistic"))
                         or written(floor.get("probabilisticPresets")))
    for key in list(floor) + [k for k in defaults if k not in floor]:
        if key in ("probabilistic", "probabilisticPresets"):
            side = floor if floor_wrote_items else defaults
            if key in side:
                merged[key] = side[key]
                source[key] = FLOOR if side is floor else DEFAULTS
            continue
        mine = floor.get(key)
        if written(mine):
            merged[key] = mine
            source[key] = FLOOR
        elif written(defaults.get(key)):
            merged[key] = defaults[key]
            source[key] = DEFAULTS
        elif key in floor:
            merged[key] = mine
            source[key] = FLOOR
    return merged, source


def field_base(fi: int, source: dict, key: str) -> str:
    """Where a merged field lives on disk: `/floors/{fi}` or `/floorDefaults`.

    `key` is a top-level floor key, or `floorLootPlan/<list>`.
    """
    return "/floorDefaults" if source.get(key) == DEFAULTS else f"/floors/{fi}"


def reroute(pointer: str | None, fi: int, source: dict) -> str | None:
    """A pointer into merged floor `fi`, moved to the file node it came from."""
    prefix = f"/floors/{fi}"
    if not pointer or not (pointer == prefix or pointer.startswith(prefix + "/")):
        return pointer
    rest = pointer[len(prefix):]
    parts = rest.split("/")
    if len(parts) < 2:
        return pointer
    key = parts[1]
    if key == LOOT_PLAN and len(parts) >= 3:
        key = f"{LOOT_PLAN}/{parts[2]}"
    if source.get(key) == DEFAULTS:
        return "/floorDefaults" + rest
    return pointer


def fan_out(plan: dict) -> dict:
    """Default key -> how many floors inherit it.

    Exactly "the floors that did not write it", by the same reading the merge
    uses — so changing a default touches this many floors.
    """
    defaults = plan.get("floorDefaults") or {}
    floors = plan.get("floors") or []
    out: dict = {}
    for key, value in defaults.items():
        if key == LOOT_PLAN:
            continue
        if key in NEVER_INHERITED or not _declared(key, value):
            continue
        out[key] = sum(1 for f in floors if not _declared(key, f.get(key)))
    loot = defaults.get(LOOT_PLAN) or {}
    for key, value in loot.items():
        if not written(value):
            continue
        tag = f"{LOOT_PLAN}/{key}"
        out[tag] = sum(1 for f in floors
                       if merge_floor(defaults, f)[1].get(tag) == DEFAULTS)
    return out


def refused_defaults(defaults: dict | None) -> list[tuple[str, str]]:
    """(key, reason) for each default `FloorPlanDefaults.Validate` throws on."""
    defaults = defaults or {}
    out = []
    if defaults.get("endsRunOnClear"):
        out.append(("endsRunOnClear",
                    "a floor says 'not written' by leaving a bool false, so a "
                    "default of true could never be turned off — declare it on "
                    "the floor that ends the run"))
    if defaults.get("elevation"):
        out.append(("elevation",
                    "height belongs to each area (OZX-1338); a default would "
                    "stand every area on one level, and a stair between two "
                    "areas of one height has no direction"))
    if defaults.get("rooms"):
        out.append(("rooms",
                    "rooms carry roomIds, so every floor inheriting them would "
                    "declare the same rooms"))
    return out


def _f32(x: float) -> float:
    """Round to IEEE single precision, as the C# `float` arithmetic does."""
    import struct
    return struct.unpack("f", struct.pack("f", x))[0]


def resolve_portions(portions: list, total_rooms: int) -> list[int]:
    """`PortionResolver.Resolve`: rooms per stage, by largest remainder.

    Floats are kept to single precision step by step, because the C# does
    its sums in `float`, and the one place this is used — "does the peak
    stage get a room at the smallest size" — turns on a tie at the
    remainder. The sort is a stable insertion sort descending by fraction,
    so an exact tie goes to the earlier stage, as upstream.
    """
    n = len(portions)
    if not n or total_rooms <= 0:
        return [0] * n
    total = 0.0
    for portion in portions:
        total = _f32(total + _f32(portion or 0))
    counts = [0] * n
    if total <= 0:
        return counts
    raw = [_f32(_f32(_f32(portion or 0) / total) * total_rooms) for portion in portions]
    counts = [int(r) for r in raw]
    remainder = total_rooms - sum(counts)
    if remainder > 0:
        order = list(range(n))
        for i in range(1, n):
            key = order[i]
            frac = _f32(raw[key] - counts[key])
            j = i - 1
            while j >= 0 and _f32(raw[order[j]] - counts[order[j]]) < frac:
                order[j + 1] = order[j]
                j -= 1
            order[j + 1] = key
        for r in range(min(remainder, n)):
            counts[order[r]] += 1
    return counts
