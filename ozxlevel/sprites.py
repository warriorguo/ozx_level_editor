"""Resolve a game id to the actual pixels that represent it.

Enemies and items carry a key, not an image. Getting from one to the other is
five hops through Unity's asset graph::

    EnemyData.animConfigKey            "anim/husk"
      -> ResourcesDB.asset             key   -> guid
      -> <Name>AnimConfig.asset        spriteAnimStates[name="idle"].data -> guid
      -> <Name>Anim.asset              frames[0] -> {fileID, guid}
      -> <sheet>.png + <sheet>.png.meta   the sprite whose internalID == fileID
                                          -> rect {x, y, width, height}

Nothing is cropped here. The rect is handed to the browser and the sheet is
served whole, so CSS `background-position` does the cutting — which is what a
sprite sheet is for, and what lets this stay standard-library only.

One wrinkle worth stating: **Unity sprite rects have their origin at the
bottom-left of the texture, CSS at the top-left.** The flip happens once, in
:func:`_css_offset`, and nowhere else.

Everything is cached against the project root; `Dataset.revision` is not
consulted because these assets live outside GameData and change far less often
than level data. :meth:`SpriteIndex.invalidate` exists for when they do.
"""

from __future__ import annotations

import pathlib
import re
import struct
from typing import Any

# `key: anim/husk` followed by `asset: {fileID: …, guid: …, type: …}`
_RESOURCE_ENTRY = re.compile(
    r"-\s+key:\s*(?P<key>\S+)\s*\n\s*asset:\s*\{fileID:\s*(?P<file_id>-?\d+),"
    r"\s*guid:\s*(?P<guid>[0-9a-f]{32})",
    re.M)

# a `name:` / `data: {… guid: …}` pair inside spriteAnimStates
_ANIM_STATE = re.compile(
    r"-\s+name:\s*(?P<name>\S+)\s*\n\s*data:\s*\{[^}]*guid:\s*(?P<guid>[0-9a-f]{32})",
    re.M)

_FRAME = re.compile(
    r"-\s*\{fileID:\s*(?P<file_id>-?\d+),\s*guid:\s*(?P<guid>[0-9a-f]{32})", re.M)

_META_GUID = re.compile(r"^guid:\s*([0-9a-f]{32})", re.M)

_MODEL_PREFAB = re.compile(
    r"modelPrefab:\s*\{[^}]*guid:\s*(?P<guid>[0-9a-f]{32})")

# A SpriteRenderer's sprite inside a prefab. fileID 0 means "no sprite", which
# is common on a body whose visual is entirely head and leg parts.
_PREFAB_SPRITE = re.compile(
    r"m_Sprite:\s*\{fileID:\s*(?P<file_id>-?\d+),\s*guid:\s*(?P<guid>[0-9a-f]{32})")

_PART_PREFAB = re.compile(r"prefab:\s*\{[^}]*guid:\s*(?P<guid>[0-9a-f]{32})")


class SpriteIndex:
    """Lazily-built guid → path map plus the lookups layered on it."""

    def __init__(self, project_root: pathlib.Path):
        self.root = pathlib.Path(project_root)
        self.assets = self.root / "Assets"
        self._guid_to_path: dict[str, pathlib.Path] | None = None
        self._resource_keys: dict[str, str] | None = None
        self._sheet_cache: dict[str, dict[int, dict]] = {}
        self._resolved: dict[Any, dict | None] = {}

    def invalidate(self) -> None:
        self._guid_to_path = None
        self._resource_keys = None
        self._sheet_cache.clear()
        self._resolved.clear()

    # ── the guid index ───────────────────────────────────────────────────

    @property
    def guid_to_path(self) -> dict[str, pathlib.Path]:
        """Every .meta in Assets/, mapped guid → the file it describes.

        ~3.7k files on this project. Built once on first use rather than at
        startup, so a session that never looks at a sprite never pays for it.
        """
        if self._guid_to_path is None:
            index: dict[str, pathlib.Path] = {}
            if self.assets.is_dir():
                for meta in self.assets.rglob("*.meta"):
                    try:
                        head = meta.read_text(encoding="utf-8", errors="ignore")[:400]
                    except OSError:
                        continue
                    found = _META_GUID.search(head)
                    if found:
                        index[found.group(1)] = meta.with_suffix("")
            self._guid_to_path = index
        return self._guid_to_path

    def path_for(self, guid: str) -> pathlib.Path | None:
        return self.guid_to_path.get(guid)

    # ── ResourcesDB ──────────────────────────────────────────────────────

    @property
    def resource_keys(self) -> dict[str, str]:
        """The `anim/husk` → guid table the runtime resolves keys through."""
        if self._resource_keys is None:
            db = self.assets / "Prefabs/System/ResourcesDB.asset"
            try:
                text = db.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self._resource_keys = {}
            else:
                self._resource_keys = {
                    m.group("key"): (int(m.group("file_id")), m.group("guid"))
                    for m in _RESOURCE_ENTRY.finditer(text)
                }
        return self._resource_keys

    def guid_for_key(self, key: str) -> str | None:
        entry = self.resource_keys.get(key)
        return entry[1] if entry else None

    def sprite_for_resource_key(self, key: str | None) -> dict | None:
        """A picture for a key that points straight at a sprite.

        Items work this way — `skill/crimson_barrage@icon` resolves to a
        texture and a sprite fileID in one hop, with no animation graph in
        between. The `@suffix` is part of the key, not a modifier, so it is
        looked up verbatim before falling back to the bare name.
        """
        if not key:
            return None
        cached = self._resolved.get(("res", key))
        if cached is not None or ("res", key) in self._resolved:
            return cached

        entry = self.resource_keys.get(key)
        if entry is None and "@" in key:
            entry = self.resource_keys.get(key.split("@", 1)[0])
        result = self.sprite_rect(entry[1], entry[0]) if entry else None
        self._resolved[("res", key)] = result
        return result

    # ── the chain ────────────────────────────────────────────────────────

    def idle_sprite_for_key(self, anim_config_key: str | None) -> dict | None:
        """The first frame of the `idle` state for an animConfigKey."""
        if not anim_config_key:
            return None
        if anim_config_key in self._resolved:
            return self._resolved[anim_config_key]

        result = self._resolve_idle(anim_config_key)
        self._resolved[anim_config_key] = result
        return result

    def _resolve_idle(self, key: str) -> dict | None:
        config_path = self.path_for(self.guid_for_key(key) or "")
        if config_path is None or not config_path.is_file():
            return None
        try:
            config_text = config_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        states = {m.group("name"): m.group("guid")
                  for m in _ANIM_STATE.finditer(config_text)}
        # `idle` is the resting pose and what a catalog should show. Fall back
        # to whatever state exists so an enemy animated only while walking
        # still gets a picture rather than nothing.
        anim_guid = states.get("idle") or next(iter(states.values()), None)
        if not anim_guid:
            # 23 of the 68 enemies have `spriteAnimStates: []` — they are rigged
            # from a model prefab (legs and heads) rather than a flipbook. Their
            # body sprite sits on the prefab's SpriteRenderer.
            return self._sprite_from_model_prefab(config_text)

        anim_path = self.path_for(anim_guid)
        if anim_path is None or not anim_path.is_file():
            return None
        try:
            anim_text = anim_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        frame = _FRAME.search(anim_text.split("frames:", 1)[-1])
        if not frame:
            return None
        return self.sprite_rect(frame.group("guid"), int(frame.group("file_id")))

    def _sprite_from_model_prefab(self, config_text: str) -> dict | None:
        """A picture of a prefab-rigged enemy.

        Tries the body first, then the head and leg parts. Some bodies carry no
        sprite at all — `demolition`'s is `m_Sprite: {fileID: 0}` because the
        whole silhouette is its head — so falling through to the parts is what
        gets those a picture instead of a letter.
        """
        candidates = []
        model = _MODEL_PREFAB.search(config_text)
        if model:
            candidates.append(model.group("guid"))
        # heads[] and leg.prefab, in the order they appear after the body
        tail = config_text.split("modelPrefab:", 1)[-1]
        candidates.extend(m.group("guid") for m in _PART_PREFAB.finditer(tail))

        seen = set()
        for guid in candidates:
            if guid in seen:
                continue
            seen.add(guid)
            rect = self._first_sprite_in_prefab(guid)
            if rect:
                return rect
        return None

    def _first_sprite_in_prefab(self, guid: str) -> dict | None:
        prefab = self.path_for(guid)
        if prefab is None or not prefab.is_file():
            return None
        try:
            text = prefab.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        for match in _PREFAB_SPRITE.finditer(text):
            file_id = int(match.group("file_id"))
            if file_id == 0:
                continue            # an empty slot, not a sprite
            rect = self.sprite_rect(match.group("guid"), file_id)
            if rect:
                return rect
        return None

    def sprite_for_prefab_key(self, key: str | None) -> dict | None:
        """A picture for something that is a prefab rather than a sprite.

        Static placements name a `kind`, not an asset; the convention is
        `prefab/<kind>` in ResourcesDB. The prefab's first real SpriteRenderer
        sprite is close enough to a thumbnail for a catalog.
        """
        if not key:
            return None
        cache_key = ("prefab", key)
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        guid = self.guid_for_key(key)
        result = self._first_sprite_in_prefab(guid) if guid else None
        self._resolved[cache_key] = result
        return result

    # ── sheets ───────────────────────────────────────────────────────────

    def sprite_rect(self, texture_guid: str, file_id: int) -> dict | None:
        """Locate one sliced sprite within its sheet."""
        sheet = self._sheet(texture_guid)
        if not sheet:
            return None
        entry = sheet.get(file_id)
        if entry is None:
            # A sheet sliced as a single sprite has no per-sprite entry; the
            # whole texture is the image.
            entry = sheet.get(0)
        if entry is None:
            return None
        return entry

    def _sheet(self, texture_guid: str) -> dict[int, dict]:
        if texture_guid in self._sheet_cache:
            return self._sheet_cache[texture_guid]

        texture = self.path_for(texture_guid)
        result: dict[int, dict] = {}
        if texture is None or not texture.is_file():
            self._sheet_cache[texture_guid] = result
            return result

        size = png_size(texture)
        if size is None:
            self._sheet_cache[texture_guid] = result
            return result
        sheet_w, sheet_h = size
        rel = texture.relative_to(self.root).as_posix()

        meta = texture.with_name(texture.name + ".meta")
        blocks = []
        if meta.is_file():
            try:
                blocks = _parse_sprite_blocks(
                    meta.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                blocks = []

        for block in blocks:
            result[block["internalID"]] = {
                "texture": rel,
                "sheetWidth": sheet_w,
                "sheetHeight": sheet_h,
                "name": block["name"],
                "x": block["x"],
                "y": _css_offset(block["y"], block["height"], sheet_h),
                "width": block["width"],
                "height": block["height"],
            }

        # Unsliced texture: the sprite is the whole image.
        result.setdefault(0, {
            "texture": rel,
            "sheetWidth": sheet_w,
            "sheetHeight": sheet_h,
            "name": texture.stem,
            "x": 0, "y": 0, "width": sheet_w, "height": sheet_h,
        })
        self._sheet_cache[texture_guid] = result
        return result


def _css_offset(unity_y: int, height: int, sheet_height: int) -> int:
    """Unity measures sprite rects from the bottom, CSS from the top."""
    return sheet_height - unity_y - height


_SPRITE_BLOCK = re.compile(
    r"-\s+serializedVersion:\s*2\s*\n"
    r"\s+name:\s*(?P<name>.+?)\s*\n"
    r"(?P<body>(?:\s+.*\n)+?)"
    r"(?=\s*-\s+serializedVersion:|\Z)", re.M)


def _parse_sprite_blocks(meta_text: str) -> list[dict[str, Any]]:
    """Pull name / rect / internalID out of a TextureImporter's sprite list."""
    section = meta_text.split("sprites:", 1)
    if len(section) < 2:
        return []
    out = []
    for match in _SPRITE_BLOCK.finditer(section[1]):
        body = match.group("body")
        rect = re.search(
            r"rect:\s*\n\s*serializedVersion:\s*\d+\s*\n"
            r"\s*x:\s*(-?\d+)\s*\n\s*y:\s*(-?\d+)\s*\n"
            r"\s*width:\s*(\d+)\s*\n\s*height:\s*(\d+)", body)
        internal = re.search(r"internalID:\s*(-?\d+)", body)
        if not rect or not internal:
            continue
        out.append({
            "name": match.group("name").strip(),
            "x": int(rect.group(1)),
            "y": int(rect.group(2)),
            "width": int(rect.group(3)),
            "height": int(rect.group(4)),
            "internalID": int(internal.group(1)),
        })
    return out


def png_size(path: pathlib.Path) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR, without decoding the image."""
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)
