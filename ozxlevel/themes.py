"""Which TilePartConfigs a theme owns.

`FloorPlanData.appearance.tilePartConfigs` names Unity assets by their asset
name, and nothing in `GameData/` lists what those names may be — the answer
lives in `Assets/Prefabs/Room/Theme/`, outside the JSON index this tool is
otherwise built on. So this module walks the one chain the runtime walks:

    themeId ("themes/factory")
      → ResourcesDB                     → FactoryTheme.asset
      → its `tileTheme` reference       → FactoryTileTheme.asset
      → `tilePartConfigs` + `startRoomTilePartConfig`

Globbing ``*TilePart*.asset`` in the theme folder would be easier and wrong:
the folder also holds `FactoryTileTheme.asset`, which is the theme itself, and
a part that no theme references would look addressable when it is not.

Two names are addressable but not equivalent (`TileThemeConfig.cs:29-38`):

- ``tilePartConfigs`` — the random pool a room rolls from.
- ``startRoomTilePartConfig`` — owned by the theme, deliberately kept OUT of
  the pool, reachable only by a room that names it.

`FindTilePartConfig` searches both, so both are legal in level data; only the
first is what an unpinned room can roll.
"""

from __future__ import annotations

import pathlib
import re

# `tileTheme: {fileID: 11400000, guid: b052…, type: 2}` — one reference on the
# theme asset. The fileID is irrelevant here; a ScriptableObject asset holds a
# single object and the guid alone identifies the file.
_REF = re.compile(r"guid:\s*([0-9a-f]{32})")


def _guid_in(text: str, field: str) -> str | None:
    """The guid of a single-reference field, e.g. `tileTheme: {…}`."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{field}:"):
            found = _REF.search(stripped)
            return found.group(1) if found else None
    return None


def _guid_list_in(text: str, field: str) -> list[str]:
    """The guids of a YAML sequence field, in order.

    Reads the block by indentation rather than parsing YAML: the entries are
    `  - {fileID: …, guid: …, type: 2}` lines directly under the key, and the
    block ends at the next key at the same level.
    """
    out: list[str] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{field}:"):
            collecting = True
            continue
        if collecting:
            if stripped.startswith("- "):
                found = _REF.search(stripped)
                if found:
                    out.append(found.group(1))
                continue
            break
    return out


class ThemeIndex:
    """Theme id -> the TilePartConfig names that theme makes addressable."""

    def __init__(self, project_root: pathlib.Path, sprites):
        self.root = pathlib.Path(project_root)
        # Reuse the sprite index purely for its guid<->path and ResourcesDB
        # tables; nothing here is a sprite.
        self.sprites = sprites
        self._cache: dict[str, dict] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    def _read(self, guid: str | None) -> str | None:
        if not guid:
            return None
        path = self.sprites.path_for(guid)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    def _name_of(self, guid: str) -> str | None:
        """An asset's name is its filename stem — what `part.name` returns."""
        path = self.sprites.path_for(guid)
        return path.stem if path else None

    def tile_parts(self, theme_id: str | None) -> dict:
        """What `theme_id` owns.

        Returns ``{"found": bool, "pool": [...], "named": [...], "all": [...]}``.
        ``found`` is False when the chain breaks anywhere — an unknown themeId,
        a theme with no tileTheme, an asset that is not on disk. Callers must
        not read an empty ``all`` as "this theme owns nothing", or a missing
        Unity checkout turns every name into a validation error.
        """
        key = theme_id or ""
        if key in self._cache:
            return self._cache[key]

        result = {"found": False, "pool": [], "named": [], "all": []}
        theme_text = self._read(self.sprites.guid_for_key(key)) if key else None
        tile_text = self._read(_guid_in(theme_text, "tileTheme")) if theme_text else None
        if tile_text is not None:
            pool = [n for n in (self._name_of(g)
                                for g in _guid_list_in(tile_text, "tilePartConfigs")) if n]
            named = [n for n in (self._name_of(g)
                                 for g in [_guid_in(tile_text, "startRoomTilePartConfig")]
                                 if g) if n]
            result = {"found": True, "pool": pool, "named": named,
                      "all": pool + [n for n in named if n not in pool]}
        self._cache[key] = result
        return result
