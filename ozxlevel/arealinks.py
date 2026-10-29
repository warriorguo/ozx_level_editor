"""Which area a linked plan ends in, and which areas every run walks through.

The Python twins of `AreaLinkPlan.FinalArea` (OZX-1518) and
`AreaLinkPlan.MustPassAreas` (OZX-1568), in `Game.Level/LevelBasePlan.cs`.
The validator and the plan view both read them, so they live here rather
than in either — two readings of "the end" would disagree about the plan.

Both read the raw `areaLinks` list; a malformed link is the area-link
checks' to report, and is skipped here rather than guessed at.
"""

from __future__ import annotations


def _pairs(links: list, n: int) -> list:
    out = []
    for l in links or []:
        a, b = (l or {}).get("fromArea", 0), (l or {}).get("toArea", 0)
        if isinstance(a, int) and isinstance(b, int) and 0 <= a < n and 0 <= b < n:
            out.append((a, b))
    return out


def dead_ends(links: list, n: int) -> list:
    """The areas no link leaves (`AreaLinkPlan.Leaves` is false). Without
    links the areas are a chain, and only the last one leads nowhere."""
    if not links:
        return [n - 1] if n else []
    leaving = {a for a, _ in _pairs(links, n)}
    return [f for f in range(n) if f not in leaving]


def final_area(plan: dict, n: int) -> tuple:
    """`AreaLinkPlan.FinalArea`: (area index or None, problem or None).

    The problem is (code, message) — every case upstream throws on. Without
    links the last area ends the run and `finalArea` may not be written.
    With them it is the area `finalArea` names, which must be a dead end, or
    — when it is omitted (0: area 0 always leads on in a linked plan) — the
    one dead end there is.
    """
    links = plan.get("areaLinks") or []
    declared = plan.get("finalArea") or 0
    if not links:
        if declared:
            return n - 1, ("LEVEL_FINAL_AREA_WITHOUT_LINKS",
                           f"sets finalArea {declared} but declares no areaLinks; without links "
                           f"the areas are a chain and the last one is the end — finalArea "
                           f"only picks between the dead ends a set of links leaves")
        return n - 1, None
    ends = dead_ends(links, n)
    listed = ", ".join(map(str, ends))
    if declared:
        if not isinstance(declared, int) or not 0 <= declared < n:
            return None, ("LEVEL_FINAL_AREA_OUT_OF_RANGE",
                          f"sets finalArea {declared}, but the plan has {n} area(s) "
                          f"(floors[0..{n - 1}])")
        if declared not in ends:
            return None, ("LEVEL_FINAL_AREA_LEADS_ON",
                          f"sets finalArea {declared}, but a link leaves that area; the run "
                          f"ends in an area with no way on, and the areas no link leaves are "
                          f"[{listed}]")
        return declared, None
    if len(ends) != 1:
        return None, ("LEVEL_FINAL_AREA_AMBIGUOUS",
                      f"leaves areas [{listed}] with no way on; name the one the run ends in "
                      f"with finalArea, and the others become optional side areas")
    return ends[0], None


def must_pass(links: list, n: int, final) -> list:
    """`AreaLinkPlan.MustPassAreas`: per area, whether every run walks it.

    Without links, all of them. With links, area 0 and the final area, and
    any other area exactly when taking it out of the link graph (walked both
    ways) cuts area 0 off from the end. Keys are ignored: they can only make
    more areas compulsory. `final` None (unresolved) takes every dead end as
    an end, as upstream does for a plan assembled in code.
    """
    if not links:
        return [True] * n
    pairs = _pairs(links, n)
    ends = {final} if final is not None else set(dead_ends(links, n))

    def reaches_an_end(skip: int) -> bool:
        reached, queue = {0}, [0]
        while queue:
            a = queue.pop(0)
            if a in ends:
                return True
            for x, y in pairs:
                other = y if x == a else x if y == a else None
                if other is None or other == skip or other in reached:
                    continue
                reached.add(other)
                queue.append(other)
        return False

    return [f == 0 or (len(ends) == 1 and f in ends) or not reaches_an_end(f)
            for f in range(n)]
