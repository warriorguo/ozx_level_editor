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


def _stamp(path: pathlib.Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


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
        # key -> (result, {asset path: stat stamp}). The assets live in the
        # Unity tree, outside GameData, so `Dataset.refresh` never sees them
        # change; each entry carries the stamps of the files it was read
        # from and is recomputed when any of them moves.
        self._cache: dict[str, tuple] = {}
        self._reading: dict | None = None

    def invalidate(self) -> None:
        self._cache.clear()

    def _cached(self, key: str):
        hit = self._cache.get(key)
        if hit is None:
            return None
        result, stamps = hit
        if any(_stamp(path) != stamp for path, stamp in stamps.items()):
            del self._cache[key]
            return None
        return result

    def _store(self, key: str, result: dict) -> dict:
        self._cache[key] = (result, self._reading or {})
        self._reading = None
        return result

    def _read(self, guid: str | None) -> str | None:
        if not guid:
            return None
        path = self.sprites.path_for(guid)
        if path is None or not path.is_file():
            return None
        if self._reading is not None:
            self._reading[path] = _stamp(path)
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
        cached = self._cached(key)
        if cached is not None:
            return cached

        self._reading = {}
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
        return self._store(key, result)

    def decal_categories(self, theme_id: str | None) -> dict:
        """The decal categories `theme_id`'s `stainConfig` can draw.

        `RoomNodeData.decorations[].decalCategory` names a category, and the
        room theme decides what it looks like — `DecalSurface` asks
        `stainConfig.HasCategory` / `TryGetCategory` and draws NOTHING when
        the theme has no such category (no fallback sprite, on purpose). The
        BattleField props (OZX-1268 / OZX-1274) are only in
        `BattleFieldDecalConfig`, so the same name is a decoration on one
        floor and dead text on the next.

        Returns ``{"found": bool, "names": [...]}``; a category with an empty
        sprite list is left out, since `TryGetCategory` skips it.
        """
        key = f"decals:{theme_id or ''}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        self._reading = {}
        result = {"found": False, "names": []}
        theme_text = self._read(self.sprites.guid_for_key(theme_id)) if theme_id else None
        config = self._read(_guid_in(theme_text, "stainConfig")) if theme_text else None
        if config is not None:
            result = {"found": True, "names": _category_names(config)}
        return self._store(key, result)


def _category_names(text: str) -> list[str]:
    """`categories:` entries of a DecalStampConfig that carry sprites.

    Each entry is a `  - name: X` line followed by its fields at a deeper
    indent; `sprites: []` (inline empty) or a `sprites:` key with no `- `
    lines under it is an entry `TryGetCategory` will not return.
    """
    names: list[str] = []
    current = None
    has_sprites = False
    in_sprites = False
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("categories:"):
            in_block = True
            continue
        if not in_block:
            continue
        if line.startswith("  - name:"):
            if current and has_sprites:
                names.append(current)
            current = stripped.split(":", 1)[1].strip()
            has_sprites = in_sprites = False
            continue
        if not line.startswith("    ") and not line.startswith("  - "):
            break
        if stripped.startswith("sprites:"):
            in_sprites = stripped != "sprites: []" and stripped.endswith(":")
            continue
        if in_sprites and stripped.startswith("- "):
            has_sprites = True
        elif not stripped.startswith("- "):
            in_sprites = False
    if current and has_sprites:
        names.append(current)
    return names
