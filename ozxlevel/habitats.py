"""Habitats and the props that carry spawn points — the Python twin of
`Game.Contracts/Data/HabitatData.cs` (`Habitats`, OZX-1788 / OZX-1789 /
OZX-1802) and of the rule lookups `Game.Level/PropSpawnRuleValidator.cs`
(OZX-1622) makes against `GameData/prop_spawn_rules.json`.

The libraries are three GameData config files, indexed by the Dataset like
everything else (`(dataType, id)`):

- `prop_habitats.json` (`PropHabitatsData`) — the elements each environment
  prop gives the room it stands in;
- `theme_habitats.json` (`ThemeHabitatsData`) — the elements a theme's ground
  gives every room of a floor on it, and `dwellerWeightScale`;
- `prop_spawn_rules.json` (`PropSpawnRulesData`) — where a spawn-point prop
  (WaterPool) may stand, and the encounter `at` its points answer to.

An enemy's `habitats` names the elements it DWELLS in (OZX-1802): it never
restricts where the enemy goes. A room offering one favours the dweller, only
a dweller may come out of a prop providing it, and a floor that can offer an
element some enemy dwells in must list a dweller. The validator and the views
both read these helpers, so the rules are written once.
"""

from __future__ import annotations

# Habitats.NonEnvironmentKinds: pickups and logic, never mapped.
NON_ENVIRONMENT_KINDS = ("equipment", "skill", "upgrade", "ally", "trigger")

# FloorData.DefaultThemeId — what a LevelData floor naming no theme is drawn
# with, and what its habitats are read under.
DEFAULT_THEME_ID = "theme_default"
# FloorPlanData.GeneratedDefaultThemeId — what LevelBuilder stamps onto a plan
# floor that names none (OZX-1790). Not the same default as a LevelData floor.
GENERATED_DEFAULT_THEME_ID = "themes/factory"
# FloorData.BasementThemeId (OZX-1919: no ground lasers there).
BASEMENT_THEME_ID = "themes/basement"

# PropSpawnCategory: encounter `at` values whose points come from a placed prop.
PROP_CATEGORIES = ("waterpool",)

PROP_HABITATS = ("PropHabitatsData", "prop_habitats")
THEME_HABITATS = ("ThemeHabitatsData", "theme_habitats")
PROP_SPAWN_RULES = ("PropSpawnRulesData", "prop_spawn_rules")


def generated_theme_id(theme_id: str | None) -> str:
    """`FloorPlanData.GeneratedThemeIdOf`."""
    return theme_id or GENERATED_DEFAULT_THEME_ID


def is_prop_category(at) -> bool:
    """`PropSpawnCategory.IsPropCategory`, after the validator's ToLowerInvariant."""
    return isinstance(at, str) and at.lower() in PROP_CATEGORIES


def prop_id(entry: dict) -> str | None:
    """`Habitats.PropIdOf`: the kind, or a `kind: "prop"` entry's prefabKey."""
    return entry.get("prefabKey") if entry.get("kind") == "prop" else entry.get("kind")


def is_environment(entry) -> bool:
    return isinstance(entry, dict) and entry.get("kind") not in NON_ENVIRONMENT_KINDS


def surely_places(entry) -> bool:
    """`Habitats.SurelyPlaces`: pinned cells or count ≥ 1, and no chance roll."""
    if not isinstance(entry, dict):
        return False
    chance = entry.get("chance") or 0
    if 0 < chance < 1:
        return False
    return bool(entry.get("cells")) or (entry.get("count") or 0) >= 1


def may_place(entry) -> bool:
    """`Habitats.MayPlace`: pinned cells or count ≥ 1, a chance roll included."""
    return isinstance(entry, dict) and (bool(entry.get("cells")) or (entry.get("count") or 0) >= 1)


def dwells_in(enemy: dict, elements) -> bool:
    """`Habitats.DwellsIn` (and `EmergesFrom`, which asks the same of the
    elements a prop provides): the enemy names one of them itself."""
    return any(h in elements for h in (enemy.get("habitats") or []))


emerges_from = dwells_in


class Libraries:
    """The three config files, read once per check. A file that is missing
    leaves its accessor answering None; the config check reports it."""

    def __init__(self, ds):
        props = ds.get(*PROP_HABITATS)
        themes = ds.get(*THEME_HABITATS)
        rules = ds.get(*PROP_SPAWN_RULES)
        self.props_doc = props.value if props else None
        self.themes_doc = themes.value if themes else None
        self.rules_doc = rules.value if rules else None
        self._provides: dict = {}
        for e in (self.props_doc or {}).get("props") or []:
            if isinstance(e, dict) and e.get("prop") and e["prop"] not in self._provides:
                self._provides[e["prop"]] = list(e.get("provides") or [])
        self._ground: dict = {}
        for t in (self.themes_doc or {}).get("themes") or []:
            if isinstance(t, dict) and t.get("themeId") and t["themeId"] not in self._ground:
                self._ground[t["themeId"]] = list(t.get("ground") or [])
        self.rules = [r for r in (self.rules_doc or {}).get("rules") or []
                      if isinstance(r, dict)]

    @property
    def complete(self) -> bool:
        return None not in (self.props_doc, self.themes_doc, self.rules_doc)

    def provides(self, pid) -> list | None:
        """`Habitats.Provides`; None where upstream throws (no entry)."""
        return self._provides.get(pid)

    def ground(self, theme_id) -> list | None:
        """`Habitats.GroundOf`; None where upstream throws (no entry)."""
        return self._ground.get(theme_id)

    def provided_anywhere(self) -> set:
        out: set = set()
        for v in self._provides.values():
            out.update(v)
        for v in self._ground.values():
            out.update(v)
        return out

    def rule_for_prefab(self, prefab_key) -> dict | None:
        if not prefab_key:
            return None
        return next((r for r in self.rules if r.get("prefabKey") == prefab_key), None)

    def rule_for_category(self, category) -> dict | None:
        return next((r for r in self.rules if r.get("category") == category), None)

    def possible_on_floor(self, theme_id, placements) -> set:
        """`Habitats.PossibleOnFloor`, minus its throws (the caller reports
        an unmapped theme or prop first): ground ∪ what each environment
        entry that may place something provides."""
        out = set(self.ground(theme_id or DEFAULT_THEME_ID) or [])
        for entry in placements or []:
            if is_environment(entry) and may_place(entry):
                out.update(self.provides(prop_id(entry)) or [])
        return out


def placement_brief(libs: Libraries, entry: dict) -> list:
    """The habitat elements a placement gives its room, for the views."""
    if not is_environment(entry):
        return []
    return list(libs.provides(prop_id(entry)) or [])
