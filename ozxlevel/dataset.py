"""Loads GameData and projects it into the shape the editor UI needs.

Two rules govern everything here, both from the TD:

1. The DOM is the only writable truth. We keep each file's original text and
   its span map (see :mod:`ozxlevel.jsonspan`); the dicts handed to the UI are
   a read-only projection. Nothing is ever re-serialised wholesale.
2. Several fields fail silently in production. Those are surfaced explicitly
   rather than smoothed over — see :mod:`ozxlevel.validate`.
"""

from __future__ import annotations

import math
import pathlib
from typing import Any

from . import jsonspan
from .sprites import SpriteIndex
from .themes import ThemeIndex

# DoorDirection in Game.Contracts/Enums/GameEnums.cs:58 — the integer IS the
# enum, so 0 means Up and never "absent".
DIRECTIONS = {0: "N", 1: "S", 2: "W", 3: "E"}
DIRECTION_IDS = {v: k for k, v in DIRECTIONS.items()}
# Twin of each direction, for generating the other side of a door.
DIRECTION_TWIN = {0: 1, 1: 0, 2: 3, 3: 2}

# EventBindings.cs:141,144 — the two ids the drop chain reaches for on its
# own. Neither is authored anywhere, so a drop view that omitted them would
# show "nothing" for the many enemies that name no table.
FALLBACK_DROP_ITEM = "item_exp_small"
ELITE_BONUS_DROP_ITEM = "item_exp_large"

# StaticPlacementPolicy.OilTankKind — the one placement kind a generated room
# can be refused on the strength of which room it is.
OIL_TANK_KIND = "oiltank"

# ISkillService.DefaultDropLevelCap — what `skillDropLevelCap` resolves to
# when a plan leaves it at 0. There is no "no cap" value; a plan that wants
# the filter out of the way writes a number above every maxLevel it can drop.
SKILL_DROP_LEVEL_CAP_DEFAULT = 5

# The five families Level Studio owns; everything else is read-only closure.
OWNED = ("levels", "level_plans", "encounters", "enemies", "loot_tables")

# dataType -> the GameData subdirectory it lives in. `levels/` holds two
# types, told apart by dataType (never by filename).
SUBDIR = {
    "LevelData": "levels",
    "LevelBasePlanData": "levels",
    "EncounterData": "encounters",
    "EnemyData": "enemies",
    "LootTableData": "loot_tables",
    "ItemData": "items",
}


class ExternallyModified(RuntimeError):
    """The file changed on disk since we parsed it."""


def _stamp(path: pathlib.Path) -> tuple[int, int]:
    """Cheap change token for a file: (mtime_ns, size)."""
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


class Document:
    """One JSON file: its path, parsed value, and span map for editing."""

    __slots__ = ("path", "doc", "data_type", "id", "stamp")

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._read()

    def _read(self) -> None:
        # Stamp BEFORE reading, so a write landing between the two is caught by
        # the next staleness check rather than silently adopted.
        self.stamp = _stamp(self.path)
        text = self.path.read_bytes().decode("utf-8")
        self.doc = jsonspan.parse(text)
        value = self.doc.value
        self.data_type = value.get("dataType") if isinstance(value, dict) else None
        self.id = value.get("id") if isinstance(value, dict) else None

    @property
    def value(self) -> Any:
        return self.doc.value

    def is_stale(self) -> bool:
        try:
            return _stamp(self.path) != self.stamp
        except OSError:
            return True

    def reload(self) -> None:
        self._read()

    def apply(self, edits: list[dict]) -> None:
        """Patch the file on disk, preserving every byte we did not edit.

        Refuses when the file changed underneath us. The patch is computed
        against the text parsed earlier and written back whole, so applying it
        to a file someone else has edited would not merge — it would restore
        our stale copy and take their work with it.
        """
        if self.is_stale():
            raise ExternallyModified(
                f"{self.path.name} changed on disk since it was loaded; "
                f"reload before editing")
        new_text = jsonspan.patch(self.doc, edits)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(new_text.encode("utf-8"))
        tmp.replace(self.path)
        self._read()


class Dataset:
    """All of GameData, indexed by (dataType, id)."""

    @classmethod
    def empty(cls) -> "Dataset":
        """A Dataset with nothing mounted.

        The app starts before a project folder is chosen, so every read path
        has to work against an empty index rather than guard for None.
        """
        ds = cls.__new__(cls)
        ds.root = None
        ds.game_data = None
        ds.tilemap_data = None
        ds.docs = {}
        ds.problems = [{
            "severity": "info",
            "code": "DATA_NO_PROJECT",
            "message": "No project folder set — pick your ozx_base checkout.",
        }]
        ds.revision = 0
        ds.sprites = SpriteIndex(pathlib.Path("/nonexistent"))
        ds.themes = ThemeIndex(pathlib.Path("/nonexistent"), ds.sprites)
        return ds

    def __init__(self, project_root: str | pathlib.Path):
        self.root = pathlib.Path(project_root).expanduser().resolve()
        self.game_data = self.root / "Assets/StreamingAssets/GameData"
        self.tilemap_data = self.root / "Assets/StreamingAssets/TilemapData"
        self.docs: dict[tuple[str, str], Document] = {}
        self.problems: list[dict] = []
        # Bumped whenever the indexed set changes, so a client holding derived
        # data (catalogs, id lists) can tell it has gone stale without
        # re-fetching to find out.
        self.revision = 0
        # Sprite resolution walks Assets/, not GameData, so it is built lazily
        # and separately — a session that never opens a catalog never pays for
        # the ~3.7k .meta files it indexes.
        self.sprites = SpriteIndex(self.root)
        # TilePartConfig names live in Assets/Prefabs/Room/Theme/, not in
        # GameData, so a floor plan's appearance can only be checked against
        # the Unity tree. Lazy for the same reason as the sprite index.
        self.themes = ThemeIndex(self.root, self.sprites)
        self.load()

    # ── loading ──────────────────────────────────────────────────────────

    def load(self) -> None:
        self.docs.clear()
        self.problems.clear()
        self.revision += 1
        if not self.game_data.is_dir():
            self.problems.append({
                "severity": "error",
                "code": "DATA_ROOT_MISSING",
                "message": f"GameData not found under {self.root}",
            })
            return

        for path in sorted(self.game_data.rglob("*.json")):
            try:
                doc = Document(path)
            except Exception as exc:  # malformed JSON must not be silent
                self.problems.append({
                    "severity": "error",
                    "code": "DATA_JSON_INVALID",
                    "message": f"{path.name}: {exc}",
                    "path": str(path),
                })
                continue
            if not doc.data_type:
                self.problems.append({
                    "severity": "error", "code": "DATA_TYPE_UNKNOWN",
                    "message": f"{path.name}: no dataType", "path": str(path)})
                continue
            if not doc.id:
                self.problems.append({
                    "severity": "error", "code": "DATA_ID_MISSING",
                    "message": f"{path.name}: no id", "path": str(path)})
                continue
            key = (doc.data_type, doc.id)
            if key in self.docs:
                # JsonDataRepository silently lets the last file win. Don't.
                self.problems.append({
                    "severity": "error", "code": "DATA_ID_DUPLICATE",
                    "message": f"duplicate {doc.data_type} id '{doc.id}': "
                               f"{self.docs[key].path.name} and {path.name}",
                    "path": str(path)})
                continue
            self.docs[key] = doc


    def refresh(self) -> bool:
        """Pick up edits made outside the editor. Returns True if anything moved.

        Called before every read, so the page reflects the files as they are
        now. A browser refresh re-fetches the API but cannot re-read disk on
        its own, and stale data here is not merely wrong on screen — an edit
        computed against it would write the old file back.

        Cheap: one stat() per file. Only changed documents are reparsed.
        """
        if self.game_data is None or not self.game_data.is_dir():
            return False

        # rglob yields absolute paths and Documents were built from them, so
        # compare directly — resolve() raises on a path just deleted.
        on_disk = set(self.game_data.rglob("*.json"))
        known = {d.path: key for key, d in self.docs.items()}
        changed = False

        # Reparse what changed; drop what vanished.
        for path, key in list(known.items()):
            if path not in on_disk:
                self.docs.pop(key, None)
                changed = True
                self.revision += 1
                continue
            doc = self.docs.get(key)
            if doc is not None and doc.is_stale():
                try:
                    doc.reload()
                except Exception:
                    # A half-written or broken file: rebuild wholesale so the
                    # problem surfaces through the normal load path.
                    return self._reload_all()
                changed = True
                self.revision += 1
                # An id or dataType change moves the document's key.
                if (doc.data_type, doc.id) != key:
                    return self._reload_all()

        # Anything new showing up needs the full load path for its duplicate
        # and dataType checks.
        if on_disk - set(known):
            return self._reload_all()
        return changed

    def _reload_all(self) -> bool:
        self.load()
        return True

    # ── lookup ───────────────────────────────────────────────────────────

    def get(self, data_type: str, id_: str) -> Document | None:
        return self.docs.get((data_type, id_))

    def of_type(self, data_type: str) -> list[Document]:
        return [d for (t, _), d in sorted(self.docs.items()) if t == data_type]

    def ids_of(self, data_type: str) -> list[str]:
        return [d.id for d in self.of_type(data_type)]

    # ── projections the UI consumes ──────────────────────────────────────

    def level_summaries(self) -> list[dict]:
        out = []
        for doc in self.of_type("LevelData"):
            floors = doc.value.get("floors") or []
            out.append({
                "id": doc.id,
                "displayName": doc.value.get("displayName") or doc.id,
                "file": doc.path.name,
                "floors": len(floors),
                "rooms": sum(len(f.get("rooms") or []) for f in floors),
            })
        return out

    def enemy_catalog(self) -> list[dict]:
        out = []
        for doc in self.of_type("EnemyData"):
            v = doc.value
            meta = " · ".join(x for x in (
                v.get("movementType"), v.get("attackType"), v.get("role")) if x)
            out.append({
                "id": doc.id,
                "meta": meta or "enemy",
                "code": _code(doc.id),
                # The first frame of the idle state — what the thing looks like
                # standing still, which is what a picker wants.
                "sprite": self.sprites.idle_sprite_for_key(v.get("animConfigKey")),
                "category": v.get("category"),
                "role": v.get("role"),
                "hasElite": bool(v.get("elite")),
                "dropTableId": v.get("dropTableId"),
                # OZX-1063: an egg's clutch. It never passes through the
                # sampler, so a room that draws N eggs ends up with N ×
                # [min, max] bodies on top of `enemiesPerRoom` — and a
                # picker that shows this as an ordinary enemy understates
                # the room by that much.
                "hatch": self.hatch_brief(v),
            })
        return out

    def hatch_brief(self, value: dict) -> dict | None:
        """What one egg opens into, or None when the enemy is not an egg.

        Two channels say the same thing: `hatchEnemyId` is a single-entry pool
        and `hatchPool` is the general form. `IsEgg` is "either is set", so
        that is the discriminator here too — and both being set is a
        contradiction the validator owns, not something to pick a winner for.

        The count range is the pre-clutch behaviour when unset: exactly one.
        """
        single = value.get("hatchEnemyId") or None
        pool = [x for x in (value.get("hatchPool") or []) if x]
        if not single and not (value.get("hatchPool") or []):
            return None
        lo = value.get("hatchCountMin") or 0
        hi = value.get("hatchCountMax") or 0
        lo = lo if lo > 1 else 1
        hi = hi if hi > 1 else 1
        return {
            "enemyId": single,
            "pool": pool,
            # Both channels set at once; the validator reports it, and the
            # view must not silently pick one.
            "ambiguous": bool(single and (value.get("hatchPool") or [])),
            "countMin": lo,
            "countMax": max(lo, hi),
            "spreadRadius": value.get("hatchSpreadRadius") or 0,
        }

    def loot_catalog(self) -> list[dict]:
        out = []
        for doc in self.of_type("LootTableData"):
            entries = doc.value.get("entries") or []
            items = ", ".join(e.get("itemId", "?") for e in entries[:3])
            if len(entries) > 3:
                items += f", +{len(entries) - 3}"
            out.append({
                "id": doc.id,
                "meta": (f"{'pick one' if doc.value.get('pickOne') else 'independent'}"
                         f" · {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
                         + (f" · {items}" if items else " · EMPTY")),
                "code": _code(doc.id),
                "pickOne": bool(doc.value.get("pickOne")),
                "entryCount": len(entries),
                "empty": not entries,
            })
        return out

    def item_brief(self, item_id: str | None) -> dict:
        """Resolve an itemId to what a designer needs to recognise it.

        Filenames do not carry the id — 22 of the 47 items are stored under a
        different basename (`item_exp_large` lives in `exp_large.json`) — so
        this always goes through the (dataType, id) index, never the path.
        """
        if not item_id:
            return {"missing": True, "displayName": None, "itemType": None,
                    "rarity": None, "value": None, "spriteKey": None}
        doc = self.get("ItemData", item_id)
        if doc is None:
            return {"missing": True, "displayName": None, "itemType": None,
                    "rarity": None, "value": None, "spriteKey": None}
        v = doc.value
        return {
            "missing": False,
            "sprite": self.sprites.sprite_for_resource_key(v.get("spriteKey")),
            # nameTemplate wins at generation time when set; displayName is the
            # fixed-literal fallback (OZX-586).
            "displayName": v.get("nameTemplate") or v.get("displayName") or item_id,
            "itemType": v.get("type"),
            "rarity": v.get("rarity"),
            "value": v.get("value"),
            "spriteKey": v.get("spriteKey"),
        }

    def taxonomy(self) -> dict:
        """`item_taxonomy.json` as name -> parent, plus name -> its items.

        Two questions get asked of it, and `GetItemsIn` returning empty cannot
        answer the first: does this node EXIST (a typo has to be told apart
        from a real node that holds no items of its own), and what is an
        item's category chain, leaf first, so the most specific authored
        multiplier wins.

        Returns `found: False` rather than raising when the file is absent —
        a plan that names no category never consults it at all, which is the
        back-compat gate `StageWeightResolver` is built around.
        """
        doc = self.get("ItemTaxonomyData", "item_taxonomy")
        if doc is None:
            return {"found": False, "parentOf": {}, "categoryOfItem": {}}
        parent_of: dict[str, str] = {}
        category_of_item: dict[str, str] = {}
        for node in doc.value.get("nodes") or []:
            name = node.get("name")
            if not name:
                continue
            parent_of[name] = node.get("parent") or ""
            for item_id in node.get("items") or []:
                if item_id:
                    category_of_item[item_id] = name
        return {"found": True, "parentOf": parent_of,
                "categoryOfItem": category_of_item}

    def categories_of(self, item_id: str | None) -> list[str]:
        """An item's taxonomy chain, leaf first. Empty if it has no node."""
        tax = self.taxonomy()
        current = tax["categoryOfItem"].get(item_id or "")
        chain: list[str] = []
        while current and current not in chain:
            chain.append(current)
            current = tax["parentOf"].get(current) or ""
        return chain

    def _table_doc(self, table_id: str | None,
                   tables: dict | None = None) -> dict | None:
        """One loot table, generated-first then GameData. None if unknown."""
        if not table_id:
            return None
        table = (tables or {}).get(table_id)
        if table is not None:
            return table
        doc = self.get("LootTableData", table_id)
        return doc.value if doc else None

    def table_items(self, table_id: str | None, tables: dict | None = None,
                    _seen: frozenset = frozenset()) -> list[str]:
        """Every itemId a table can yield, following table-valued entries.

        The flattened roster, not the rolled one — `LootTableDraw.PossibleItems`
        answers the same question upstream, and for the same reason: some
        decisions are about the outcomes a slot could have rather than about
        the slot. Here it is "does a skill reach the player through this",
        which a one-level read of a table whose entries are themselves tables
        would answer `no` to.

        Self-reference would be an authoring bug rather than a shape, but this
        walks user data, so `_seen` keeps it from recursing forever.
        """
        table = self._table_doc(table_id, tables)
        if table is None or table_id in _seen:
            return []
        seen = _seen | {table_id}
        out: list[str] = []
        for e in table.get("entries") or []:
            if e.get("itemId"):
                out.append(e["itemId"])
            elif e.get("tableId"):
                out.extend(self.table_items(e["tableId"], tables, seen))
        return out

    def _entry_brief(self, item_id: str | None, table_id: str | None,
                     tables: dict | None = None) -> dict:
        """What one loot-table row shows, whether it names an item or a table.

        A table-valued entry has no single item to describe, so it is labelled
        by the table and carries the roster it could draw from. `missing` then
        means what it means for an item — the thing this row names cannot be
        resolved — plus the two shapes `LootTableDraw.Require` refuses: a
        table that is not `pickOne` (its count would stop meaning anything)
        and one with no entries.
        """
        if not table_id:
            return self.item_brief(item_id)
        table = self._table_doc(table_id, tables)
        entries = (table or {}).get("entries") or []
        drawable = bool(table) and bool(table.get("pickOne")) and bool(entries)
        return {
            "missing": not drawable,
            "sprite": None,
            "displayName": table_id,
            # Deliberately not an item type: the row is a family, and a family
            # of skills and a family of weapons must not look alike here.
            "itemType": None,
            "rarity": None,
            "value": None,
            "spriteKey": None,
            "drawsFrom": self.table_items(table_id, tables),
        }

    def skill_brief(self, skill_id: str | None) -> dict:
        """Resolve a kind="skill" placement's skillId to what it will grant.

        This is SkillData, not ItemData, and the two id spaces overlap —
        `shield_blue` is both an item and a skill — so it must never go
        through :meth:`item_brief`. SkillPlatformSpawnAdapter throws on a
        missing id and on a skill whose `type` is not "passive", so both are
        reported here rather than left to a playtest.
        """
        if not skill_id:
            return {"missing": True, "displayName": None, "skillType": None,
                    "rarity": None, "sprite": None, "iconKey": None}
        doc = self.get("SkillData", skill_id)
        if doc is None:
            return {"missing": True, "displayName": None, "skillType": None,
                    "rarity": None, "sprite": None, "iconKey": None}
        v = doc.value
        # SkillIndicatorView.ResolveIndicatorKey(iconKeys, level) with level 1
        # — the platform always shows the level-1 icon — is iconKeys[0].
        icons = v.get("iconKeys") or []
        icon_key = icons[0] if icons else None
        return {
            "missing": False,
            "displayName": v.get("displayName") or skill_id,
            "skillType": v.get("type"),
            "rarity": v.get("rarity"),
            "iconKey": icon_key,
            "sprite": self.sprites.sprite_for_resource_key(icon_key),
        }

    def skill_catalog(self) -> list[dict]:
        """Skills a SkillPlatform may grant — passive, with a loadable icon.

        SkillPlatformSpawnAdapter throws on an active skill, on one with no
        `iconKeys`, and again when the key it does name will not load, so
        offering any of the three would only let a designer author a crash.
        Whatever is already in the data is still shown and validated; this
        list governs what can be chosen, not what can be displayed.
        """
        out = []
        for doc in self.of_type("SkillData"):
            v = doc.value
            if v.get("type") != "passive":
                continue
            # ResourcesDB, looked up verbatim: ResourcesAssetService does no
            # `@`-suffix fallback, so a key that only resolves through this
            # module's bare-name fallback would draw art here and still throw
            # in the game. `skill/speed_boost@icon` is exactly that case.
            icon_key = (v.get("iconKeys") or [None])[0]
            if not icon_key or self.sprites.guid_for_key(icon_key) is None:
                continue
            out.append({
                "id": doc.id,
                "displayName": v.get("displayName") or doc.id,
                # The display name leads the meta line because that is what a
                # designer knows the skill by, and `meta` is what the library
                # search matches on alongside the id.
                "meta": " · ".join(x for x in (
                    v.get("displayName"),
                    v.get("type"),
                    f"rarity {v['rarity']}" if v.get("rarity") is not None else None,
                ) if x),
                "code": _code(doc.id),
                "sprite": self.sprites.sprite_for_resource_key(icon_key),
            })
        return out

    def rarity_palette(self) -> dict:
        """The game's own rarity -> text colour map, from box_rarity.json.

        Read rather than hardcoded: this data has `uncommon` blue and `rare`
        green, which inverts the usual convention, so a guessed palette would
        mislabel every item.
        """
        doc = self.get("BoxRarityConfigData", "box_rarity")
        if doc is None:
            return {}
        return {e.get("rarity"): e.get("textColor")
                for e in doc.value.get("entries") or [] if e.get("rarity")}

    def loot_table_view(self, table_id: str | None,
                        tables: dict | None = None) -> dict | None:
        """Resolve a loot table into rows with their effective drop chance.

        The two weight semantics are genuinely different and the UI must not
        blur them (``DropTableResolver``):

        - ``pickOne`` — exactly one entry is chosen, weighted. A row's chance
          is its share of the total weight.
        - default — every entry rolls independently, and ``weight`` IS the
          percentage. Weights do not sum to 100 and must not be normalised.
        """
        if not table_id:
            return None
        # A generated level references tables the assigner synthesised in
        # memory; they exist nowhere on disk, so they have to win over the
        # GameData lookup or every one of them reads as a missing table.
        table = (tables or {}).get(table_id)
        if table is None:
            doc = self.get("LootTableData", table_id)
            if doc is None:
                return {"id": table_id, "missing": True, "entries": [], "pickOne": False}
            table = doc.value
        entries = table.get("entries") or []
        pick_one = bool(table.get("pickOne"))
        total = sum(max(0, e.get("weight", 0)) for e in entries)
        rows = []
        for index, e in enumerate(entries):
            weight = e.get("weight", 0)
            if pick_one:
                chance = (weight / total * 100) if total else 0.0
            else:
                chance = min(100.0, max(0.0, float(weight)))
            item_id = e.get("itemId")
            nested = e.get("tableId")
            row = {
                "index": index,
                "itemId": item_id,
                # OZX-1047: an entry may name a `pickOne` TABLE instead of an
                # item. The weight is still this entry's own chance; what
                # changes is that hitting it draws the identity from there.
                "tableId": nested,
                "weight": weight,
                "minCount": e.get("minCount", 0),
                "maxCount": e.get("maxCount", 0),
                "chance": round(chance, 1),
                "pointer": f"/entries/{index}",
            }
            row.update(self._entry_brief(item_id, nested, tables))
            rows.append(row)
        return {
            "id": table_id,
            "missing": False,
            "pickOne": pick_one,
            "entries": rows,
            "empty": not entries,
        }

    def static_catalog(self) -> list[dict]:
        """Placement kinds, from what the data actually uses.

        StaticPlacementEntryData.kind is a controlled discriminator; the
        adapters switch on it and throw on anything unrecognised, so the
        library offers exactly the kinds in use plus the documented set.
        """
        seen: dict[str, int] = {}
        for doc in self.of_type("LevelData"):
            for floor in doc.value.get("floors") or []:
                for room in floor.get("rooms") or []:
                    for p in room.get("staticPlacements") or []:
                        kind = p.get("kind")
                        if kind:
                            seen[kind] = seen.get(kind, 0) + 1
        known = ["cargo", "oiltank", "toxicbarrel", "pinballhammer", "prop",
                 "skill", "device", "trigger", "equipment"]
        for k in known:
            seen.setdefault(k, 0)
        return [{"id": k, "meta": f"placement · used {seen[k]}×",
                 "code": _code(k), "uses": seen[k],
                 "sprite": self.sprites.sprite_for_prefab_key(f"prefab/{k}")}
                for k in sorted(seen, key=lambda k: (-seen[k], k))]

    def wave_pin(self, action: dict, tables: dict | None = None) -> dict | None:
        """OZX-1041: the prize pinned to this launch, or None.

        A pin is paid ON TOP of whatever the table rolled, to the first
        `count` enemies of the launch — exactly parallel to `eliteCount`
        promoting the first k. It is a second channel, not a variant of the
        first: `dropTableId` answers "what does this group roll",
        `guaranteedDrop` answers "what is pinned here". They were one string
        (`dropTableIdOverride`) taking turns overwriting itself, which handed
        the prize to every enemy in the launch and cost the carriers the loot
        they would otherwise have dropped.

        JsonUtility materialises an omitted `[Serializable]` field, so the
        object being present means nothing upstream and nothing here: a pin
        exists iff it names an item or a table.
        """
        pin = action.get("guaranteedDrop") or {}
        item_id = pin.get("itemId") or None
        table_id = pin.get("tableId") or None
        if not item_id and not table_id:
            return None
        brief = self._entry_brief(item_id, table_id, tables)
        items = [item_id] if item_id else brief.get("drawsFrom") or []
        chance = pin.get("chance") or 0
        return {
            "itemId": item_id,
            "tableId": table_id,
            "count": pin.get("count", 0),
            # 0 is "not authored", which fires every time. The UI must not
            # render that as a 0% pin, so the resolved figure is separate
            # from the authored one.
            "chance": chance,
            "chancePercent": round((chance or 1) * 100, 1),
            "displayName": brief["displayName"],
            "itemType": brief["itemType"],
            "missing": brief["missing"],
            "drawsFrom": brief.get("drawsFrom") or [],
            "hasSkill": any(self.item_brief(i)["itemType"] == "skill"
                            for i in items),
        }

    def wave_drop(self, action: dict, tables: dict | None = None) -> dict:
        """What one launch wave drops when it dies — the Effective Drop view.

        `EventBindings.OnEnemyDied` resolves exactly one table, in this order
        (OZX-289/1041), and the editor must not approximate it:

            action.dropTableId  →  EnemyData.dropTableId  →  item_exp_small

        and then pays `action.guaranteedDrop` on top of it, to the first
        `count` enemies of the launch (`wave_pin`).

        This is the channel skills reach the player through: an ItemData of
        `type: "skill"` in the rolled table is picked up by `ItemService`,
        which hands `item.id` to `SkillService.AddPassive`. It is separate
        from, and additive to, the room-clear and cargo tables the Loot column
        already shows — an enemy drop is authored on the encounter, not on the
        room, so nothing in that column would ever reveal it. A pinned skill
        is invisible in exactly the same way, so it counts too.

        The elite bonus is `item_exp_large` on top of whatever rolled, never
        instead of it.
        """
        enemy_id = action.get("enemyId")
        # OZX-1041 renamed this field. The two meanings that used to share it
        # are now `dropTableId` (what this group rolls) and `guaranteedDrop`
        # (what is pinned to it), and they no longer overwrite each other.
        override = action.get("dropTableId") or None
        enemy = self.get("EnemyData", enemy_id) if enemy_id else None
        intrinsic = (enemy.value.get("dropTableId") or None) if enemy else None

        if override:
            table_id, source = override, "override"
        elif intrinsic:
            table_id, source = intrinsic, "enemy"
        else:
            table_id, source = None, "fallback"

        view = self.loot_table_view(table_id, tables) if table_id else None
        items = []
        if view and not view.get("missing"):
            items = [{"itemId": e["itemId"], "tableId": e["tableId"],
                      "displayName": e["displayName"],
                      "itemType": e["itemType"], "chance": e["chance"],
                      "missing": e["missing"]}
                     for e in view["entries"]]
        pin = self.wave_pin(action, tables)
        rolled_skill = any(
            self.item_brief(i)["itemType"] == "skill"
            for i in self.table_items(table_id, tables)) if table_id else False
        return {
            "tableId": table_id,
            "source": source,
            "missing": bool(view and view.get("missing")),
            "empty": bool(view and view.get("empty")),
            "items": items,
            "pin": pin,
            # Named rather than inlined so the UI never has to hardcode them.
            "fallbackItemId": FALLBACK_DROP_ITEM if source == "fallback" else None,
            "eliteBonusItemId": (ELITE_BONUS_DROP_ITEM
                                 if action.get("eliteCount") else None),
            "hasSkill": rolled_skill or bool(pin and pin["hasSkill"]),
        }

    def encounter_enemies(self, encounter_id: str | None,
                          inline: dict | None = None,
                          tables: dict | None = None) -> list[dict]:
        """The Effective Enemy view for a room.

        Room enemies are not authored on the room — they come from the
        EncounterData its `encounterId` points at. Each `launch` step
        contributes its own count; a step with no `action` is a pure wait.
        """
        # A generated room carries its waves inline and leaves `encounterId`
        # empty, so the inline copy is the encounter when there is one. Same
        # steps, same shape — only the source differs.
        if inline:
            value = inline
            encounter_id = encounter_id or inline.get("id") or "(inline)"
        elif not encounter_id:
            return []
        else:
            doc = self.get("EncounterData", encounter_id)
            if doc is None:
                return [{"id": encounter_id, "count": 0, "missing": True,
                         "meta": "encounter not found"}]
            value = doc.value
        out = []
        # A pure wait (a step with no action) blocks the playhead, so the
        # launches after it inherit that gate. Carrying the waits forward is
        # the difference between "immediately" and "when cleared, then +2s" —
        # the step's own condition alone would claim the former.
        pending_gates: list[str] = []

        for index, step in enumerate(value.get("steps") or []):
            action = step.get("action")
            condition = step.get("condition")

            if not action:
                # Absent action == pure wait, not an enemy source.
                pending_gates.append(describe_condition(condition))
                continue

            enemy_id = action.get("enemyId")
            if not enemy_id:
                continue

            own = describe_condition(condition)
            gates = list(pending_gates)
            # "immediately" says nothing once something precedes it.
            if own != "immediately" or not gates:
                gates.append(own)
            pending_gates = []

            # OZX-1063: an egg's clutch never passes through the sampler, so
            # it is charged to no budget at all — not `enemiesPerRoom`, not
            # the encounter's `maxAlive`. A room that draws three eggs holds
            # six to nine more bodies than any number on it says.
            enemy = self.get("EnemyData", enemy_id)
            hatch = self.hatch_brief(enemy.value) if enemy else None
            bodies = action.get("max", action.get("min", 0)) or 0

            out.append({
                "id": enemy_id,
                "count": bodies,
                "min": action.get("min", 0),
                "max": action.get("max", 0),
                "hatch": hatch,
                "hatchedMin": (hatch["countMin"] * bodies) if hatch else 0,
                "hatchedMax": (hatch["countMax"] * bodies) if hatch else 0,
                # OZX-1063: the sampler copies a pool entry's variant and
                # clump fields onto the step it synthesises, so a wave can
                # carry them too. The count is the GROUP's; which variant
                # each body is is drawn at placement time.
                "variantIds": [v for v in (action.get("variantIds") or [])],
                "cluster": _cluster_brief(action),
                "stepIndex": index,
                "stepId": step.get("id"),
                "eliteCount": action.get("eliteCount", 0),
                "viaSpawner": action.get("viaSpawner"),
                "pointer": f"/steps/{index}/action",
                "encounterId": encounter_id,
                "missing": enemy is None,
                # When this wave appears, including the waits it sits behind.
                "appearsWhen": " → ".join(gates),
                "drop": self.wave_drop(action, tables),
                "conditionKind": (condition or {}).get("kind"),
                "gatedByWait": len(gates) > 1 or (
                    bool(gates) and gates[0] != "immediately"),
            })
        return out

    def _stage_weights(self, floor: dict, base: str) -> list[dict]:
        """`floorLootPlan.stageWeights` — per-stage loot multipliers.

        OZX-1044 gave an entry an optional `category`, so a stage can say
        "×1.4 of everything and ×1.8 of gear" in two lines. That makes the
        list a sparse (stage × category) table rather than one row per stage,
        and the resolution order — the item's own category, then each ancestor,
        then the stage's category-free entry, then 1.0 — is the part that
        fails quietly: a rule picking the wrong entry still produces a number,
        so the table looks fine and only the loot feels wrong.

        The rows are returned in authored order. Sorting them would hide the
        one shape the validator cares about, two entries for the same pair.
        """
        plan = floor.get("floorLootPlan") or {}
        known = self.taxonomy()["parentOf"]
        out = []
        for wi, entry in enumerate(plan.get("stageWeights") or []):
            category = entry.get("category") or None
            out.append({
                "index": wi,
                "stageType": entry.get("stageType"),
                # Empty means "everything this stage drops" — which is what
                # every entry meant before the field existed, and is still the
                # fallback when no scoped entry matches an item.
                "category": category,
                "weight": entry.get("weight", 0),
                "unknownCategory": bool(category) and category not in known,
                "pointer": f"{base}/floorLootPlan/stageWeights/{wi}",
            })
        return out

    def plan_view(self, plan_id: str) -> dict:
        """A `LevelBasePlanData` as authored — no generation involved.

        A plan has no rooms: they are computed at generation time from
        `roomCountMin/Max`, `stageTypes` portions and the generator. Rendering
        a *sample* of that needs the headless entry point (OLE-1 / OZX-991).
        What is on disk, and what this returns, is the recipe — every field
        here is hand-authored and editable without running anything.

        The distinction matters for the UI: nothing in this view is "what the
        level looks like", so it must never be drawn as a floor map.
        """
        doc = self.get("LevelBasePlanData", plan_id)
        if doc is None:
            return {"id": plan_id, "missing": True, "floors": []}
        floors = []
        for fi, floor in enumerate(doc.value.get("floors") or []):
            base = f"/floors/{fi}"
            appearance = floor.get("appearance") or {}
            theme_id = floor.get("themeId")
            owned = self.themes.tile_parts(theme_id)
            boss_id = floor.get("bossId")
            stages = [
                {"stageType": s.get("stageType"), "portion": s.get("portion"),
                 "encounterId": s.get("encounterId"),
                 "lootPlanId": s.get("lootPlanId"),
                 # OZX-1089: the SHARE of the floor's enemiesPerRoom this
                 # stage gives back, not a multiplier. The direction is
                 # deliberate — JsonUtility reads an omitted float as 0, so a
                 # "0 = ×1" factor would read an authored `0` ("no enemies
                 # here") as "take the full budget", which is the bug the
                 # field exists to fix. Omitted = 0 = nothing changes.
                 "enemyBudgetCut": s.get("enemyBudgetCut") or 0,
                 # None when the floor's own `enemiesPerRoom` is absent —
                 # chapter_3 keeps it in `floorDefaults`, which is not merged
                 # here yet (OLE-9). Reporting 0 there would read as "no
                 # enemies", which is the one thing a cut of 1 means.
                 "enemyBudget": (
                     _stage_budget(floor["enemiesPerRoom"],
                                   s.get("enemyBudgetCut") or 0)
                     if floor.get("enemiesPerRoom") else None),
                 "pointer": f"{base}/stageTypes/{si}"}
                for si, s in enumerate(floor.get("stageTypes") or [])
            ]
            floors.append({
                "index": fi,
                "pointer": base,
                "themeId": theme_id,
                "generatorType": floor.get("generatorType"),
                "roomCountMin": floor.get("roomCountMin"),
                "roomCountMax": floor.get("roomCountMax"),
                "enemiesPerRoom": floor.get("enemiesPerRoom"),
                "transitionKeyId": floor.get("transitionKeyId"),
                # Empty is a real choice (OZX-994): this floor has no boss and
                # the room that would have been one becomes ordinary. The UI
                # must not render it as an unfilled field.
                "bossId": boss_id,
                "hasBoss": bool(boss_id),
                "bossMissing": bool(boss_id) and self.get("EnemyData", boss_id) is None,
                "stageTypes": stages,
                "stagePortionTotal": round(
                    sum(s.get("portion") or 0 for s in floor.get("stageTypes") or []), 4),
                "enemyPool": [
                    {"index": ei, "enemyId": e.get("enemyId"),
                     "weight": e.get("weight"), "maxPerRoom": e.get("maxPerRoom"),
                     "dropTableId": e.get("dropTableId"),
                     "missing": self.get("EnemyData", e.get("enemyId")) is None,
                     # OZX-1032: the spawner channel. A `category: "spawner"`
                     # enemy produces nothing on its own — it only drains what
                     # an encounter queued into it — so a spawner dropped into
                     # a pool without these two fields is an inert body with
                     # the HP of a wall. Declared iff BOTH are set.
                     "minionId": e.get("minionId") or None,
                     "minionsPerSpawner": e.get("minionsPerSpawner") or 0,
                     "isSpawner": bool(e.get("minionId")
                                       and (e.get("minionsPerSpawner") or 0) > 0),
                     # OZX-1063: variants of ONE creature folded into one
                     # entry. `enemyId` stays the representative — the entry
                     # is sampled, capped and counted as one type, and each
                     # body draws its concrete id from the group. Folding is
                     # not free: the coverage tier deals one per ENTRY, so
                     # the group gets one guaranteed body instead of three.
                     "variantIds": [v for v in (e.get("variantIds") or [])],
                     "isVariantGroup": bool(e.get("variantIds")),
                     "cluster": _cluster_brief(e),
                     "pointer": f"{base}/enemyPool/{ei}"}
                    for ei, e in enumerate(floor.get("enemyPool") or [])
                ],
                "stageWeights": self._stage_weights(floor, base),
                # Applied to every generated room of this floor whose own
                # staticPlacements came out empty. A procedural floor has no
                # hand-written rooms, so this is where a chapter's props are
                # actually authored — and the only place `chance` / `countMin`
                # appear in shipped data.
                "defaultStaticPlacements": [
                    {"index": pi, "kind": p.get("kind"),
                     "prefabKey": p.get("prefabKey"),
                     "count": p.get("count") or 0,
                     "countMin": p.get("countMin") or 0,
                     "chance": p.get("chance") or 0,
                     "chancePercent": round((p.get("chance") or 1) * 100, 1),
                     "placement": p.get("placement"),
                     "pinned": bool(p.get("cells")),
                     # OZX-1057: the one room-level exception. The start room
                     # is the only one the player does not choose to enter —
                     # they materialise in it at launch and after every death
                     # — and an oil tank is a chain-reaction hazard that
                     # damages them. The rule lives in code because the
                     # authoring handle cannot express it: this list applies
                     # to a whole floor and never names a room.
                     "skipsStartRoom": p.get("kind") == OIL_TANK_KIND,
                     "pointer": f"{base}/defaultStaticPlacements/{pi}"}
                    for pi, p in enumerate(
                        floor.get("defaultStaticPlacements") or [])
                ],
                "appearance": {
                    # Absent and [] are the same to the generator (full pool),
                    # but only `declared` can tell the UI whether there is a
                    # node to patch — the span writer replaces, never inserts.
                    "declared": "tilePartConfigs" in appearance,
                    "tilePartConfigs": appearance.get("tilePartConfigs") or [],
                    "pointer": f"{base}/appearance/tilePartConfigs",
                },
                "themeParts": owned,
            })
        cap = doc.value.get("skillDropLevelCap") or 0
        return {
            "id": doc.id,
            "missing": False,
            "file": doc.path.name,
            # OZX-1069: once a skill reaches this level the chapter stops
            # DROPPING it — four channels at once, per skill. Not a level
            # clamp: `UpgradeSkill` is unchanged and the ceiling is still
            # `SkillData.maxLevel`. Top-level on purpose, because skill level
            # is carried across the whole run and a cap that changed on the
            # stairs would seal a skill on f1 and unseal it on f2.
            "skillDropLevelCap": cap,
            "skillDropLevelCapDeclared": bool(cap),
            "skillDropLevelCapEffective": cap or SKILL_DROP_LEVEL_CAP_DEFAULT,
            "floors": floors,
        }

    def level_view(self, level_id: str, *, document: dict | None = None,
                   file_name: str | None = None) -> dict:
        """Everything the editor needs to render one level.

        `document` renders a GENERATED level instead of one from GameData — a
        session dump or the headless CLI's output. It is the same `LevelData`
        shape, so it goes through this one view rather than a second, thinner
        one: a generated level deserves the floor map, the enemy rows and the
        loot columns exactly like an authored one.

        Two fields differ and are handled where they are read, not here: the
        generator leaves `encounterId` and `lootPlanId` empty and puts the
        real content in `inlineEncounter` / `floorLootTableId`, and the loot
        ids it uses are synthesised in memory and live in the document's own
        `generatedLootTables`.
        """
        if document is None:
            doc = self.get("LevelData", level_id)
            if doc is None:
                raise KeyError(level_id)
            value, file_name, level_id = doc.value, doc.path.name, doc.id
        else:
            value = document
            level_id = document.get("id") or level_id
        tables = (document or {}).get("generatedLootTables") or {}
        floors = []
        for f_index, floor in enumerate(value.get("floors") or []):
            rooms = []
            for r_index, room in enumerate(floor.get("rooms") or []):
                base = f"/floors/{f_index}/rooms/{r_index}"
                doors = room.get("doors") or []
                rooms.append({
                    "index": r_index,
                    "pointer": base,
                    "roomId": room.get("roomId"),
                    "roomCategory": room.get("roomCategory"),
                    "stageType": room.get("stageType"),
                    "roomShape": room.get("roomShape"),
                    "templateId": room.get("templateId"),
                    "encounterId": room.get("encounterId"),
                    "lootPlanId": room.get("lootPlanId"),
                    "bossId": room.get("bossId"),
                    "isFinalRoom": bool(room.get("isFinalRoom")),
                    "hasTeleportSpot": bool(room.get("hasTeleportSpot")),
                    "themeId": (room.get("appearance") or {}).get("backgroundImage"),
                    "doors": [
                        {"index": i,
                         "direction": d.get("direction", 0),
                         "dir": DIRECTIONS.get(d.get("direction", 0), "?"),
                         "toRoomId": d.get("toRoomId"),
                         "toDoorId": d.get("toDoorId", 0),
                         "locked": bool(d.get("locked")),
                         "keyId": d.get("keyId"),
                         "pointer": f"{base}/doors/{i}"}
                        for i, d in enumerate(doors)
                    ],
                    "openDoors": [DIRECTIONS.get(d.get("direction", 0), "?") for d in doors],
                    # staticPlacements is REQUIRED (non-null) for non-cave
                    # rooms; the adapters throw on null. null and [] mean
                    # different things and the UI must not conflate them.
                    "staticPlacements": [
                        {"index": i, "kind": p.get("kind"),
                         "itemId": p.get("itemId"),
                         "lootTableId": p.get("lootTableId"),
                         "mountedEnemyId": p.get("mountedEnemyId"),
                         # kind="skill" names its grant in `skillId`, not in
                         # itemId or a loot table, so without this a skill
                         # platform renders as an anonymous "skill".
                         "skillId": p.get("skillId"),
                         "skillIdDeclared": "skillId" in p,
                         "skill": (self.skill_brief(p.get("skillId"))
                                   if p.get("kind") == "skill" else None),
                         "count": p.get("count"),
                         # OZX-1057: `count` has always been the ceiling that
                         # random fill takes what it can of; these are the two
                         # ends the entry grew. `countMin` is the floor below
                         # which the entry declines entirely, and `chance` is
                         # rolled ONCE for the whole entry — a room either is
                         # a cultivation chamber with up to three pods or an
                         # ordinary room with none, never one lonely pod.
                         "countMin": p.get("countMin") or 0,
                         "chance": p.get("chance") or 0,
                         # 0 is "not authored", which appears every time. The
                         # UI must not draw that as a 0% placement.
                         "chancePercent": round((p.get("chance") or 1) * 100, 1),
                         "cells": p.get("cells") or [],
                         "placement": p.get("placement"),
                         "pointer": f"{base}/staticPlacements/{i}"}
                        for i, p in enumerate(room.get("staticPlacements") or [])
                    ],
                    "staticPlacementsDeclared": room.get("staticPlacements") is not None,
                    "decorations": [
                        {"index": i,
                         "decalCategory": d.get("decalCategory"),
                         "prefabKey": d.get("prefabKey"),
                         "pointer": f"{base}/decorations/{i}"}
                        for i, d in enumerate(room.get("decorations") or [])
                    ],
                    # `inlineEncounter` / `floorLootTableId` are the
                    # generator's channels; on an authored room both are
                    # absent and these fall back to the authored ids.
                    "enemies": self.encounter_enemies(
                        room.get("encounterId") or None,
                        inline=room.get("inlineEncounter"), tables=tables),
                    "roomClearLoot": self.loot_table_view(
                        room.get("lootPlanId") or room.get("floorLootTableId"),
                        tables),
                    "cargoLoot": [
                        {"placementIndex": i,
                         "kind": p.get("kind"),
                         "pointer": f"{base}/staticPlacements/{i}/lootTableId",
                         "table": self.loot_table_view(p.get("lootTableId"), tables)}
                        for i, p in enumerate(room.get("staticPlacements") or [])
                        if p.get("lootTableId")
                    ] + [
                        # Guaranteed cargo is a generated-only channel. It used
                        # to be a list of one-entry table ids the assigner
                        # synthesised; OZX-1041 made it a pin instead, so the
                        # crate now rolls the room's ORDINARY table and pays
                        # this on top. The room therefore drops strictly more
                        # than it did — the extra is the cargo loot the old
                        # wrapper table displaced.
                        {"placementIndex": None, "kind": "guaranteed",
                         "pointer": None, "table": None,
                         "pin": self.wave_pin({"guaranteedDrop": c}, tables)}
                        for c in room.get("guaranteedCargo") or []
                    ] + [
                        # Pre-OZX-1041 captures still on disk. Reading them is
                        # free and refusing to would make an older session look
                        # like one with no guaranteed cargo at all.
                        {"placementIndex": None, "kind": "guaranteed",
                         "pointer": None, "pin": None,
                         "table": self.loot_table_view(t, tables)}
                        for t in room.get("guaranteedCargoTableIds") or []
                    ],
                })
            layout = self.floor_layout(floor)
            for room in rooms:
                pos = layout["positions"].get(room["roomId"])
                room["gridX"] = pos["x"] if pos else None
                room["gridY"] = pos["y"] if pos else None
                room["hasLayout"] = pos is not None
                room["parentRoomId"] = layout["subRoomParents"].get(room["roomId"])
                room["cols"] = max(1, (floor.get("rooms") or [])[room["index"]].get("roomCols") or 1)
                room["rows"] = max(1, (floor.get("rooms") or [])[room["index"]].get("roomRows") or 1)
            floors.append({
                "layout": layout,
                "index": floor.get("index", f_index),
                "pointer": f"/floors/{f_index}",
                "themeId": floor.get("themeId"),
                "startRoomId": floor.get("startRoomId"),
                "bossRoomId": floor.get("bossRoomId"),
                "rooms": rooms,
            })
        return {
            "id": level_id,
            "displayName": value.get("displayName") or level_id,
            "file": file_name,
            "floors": floors,
            # Present only for a generated level; the client uses it to drop
            # every edit control, because there is no source file to patch.
            "generation": value.get("generation"),
            "masterSeed": value.get("masterSeed"),
            "readOnly": document is not None,
        }


    def floor_layout(self, floor: dict) -> dict:
        """Grid positions and door connectors for one floor.

        This mirrors ``MiniMapLayoutBuilder.BuildGridLayout`` in
        ``Game.Level`` so the editor's map and the in-game minimap agree:
        BFS the door graph from ``startRoomId``, offsetting by direction, then
        anchor whatever is left (caves, basements — reached by cave/stair
        links rather than doors) beside the room you enter them from.

        Grid Y increases upward, as it does in production. Flipping for
        screen coordinates is the renderer's job, not this function's.
        """
        rooms = floor.get("rooms") or []
        by_id = {r.get("roomId"): r for r in rooms if r.get("roomId")}
        start = floor.get("startRoomId") or (rooms[0].get("roomId") if rooms else None)

        positions: dict[str, tuple[int, int]] = {}
        sub_parents: dict[str, str] = {}
        if start and start in by_id:
            positions[start] = (0, 0)
            queue = [start]
            visited: set[str] = set()
            while queue:
                room_id = queue.pop(0)
                if room_id in visited:
                    continue
                visited.add(room_id)
                for door in by_id.get(room_id, {}).get("doors") or []:
                    target = door.get("toRoomId")
                    if not target or target in positions or target not in by_id:
                        continue
                    dx, dy = _DIRECTION_OFFSET[door.get("direction", 0)]
                    x, y = positions[room_id]
                    positions[target] = (x + dx, y + dy)
                    queue.append(target)

        self._anchor_sub_rooms(by_id, positions, sub_parents)

        # Connectors, deduplicated the way production does it: emit only from
        # the room with the smaller ordinal id, and treat a door as locked if
        # either side says so.
        connectors = []
        for room_id, room in by_id.items():
            for door in room.get("doors") or []:
                target = door.get("toRoomId")
                if not target:
                    continue
                same_floor = target in by_id
                if same_floor and room_id > target:
                    continue
                locked = bool(door.get("locked"))
                if not locked and same_floor:
                    locked = any(d.get("toRoomId") == room_id and d.get("locked")
                                 for d in by_id[target].get("doors") or [])
                connectors.append({
                    "from": room_id,
                    "to": target,
                    "locked": locked,
                    "keyId": door.get("keyId"),
                    "horizontal": _DIRECTION_OFFSET[door.get("direction", 0)][0] != 0,
                    "crossFloor": not same_floor,
                })

        return {
            "positions": {k: {"x": v[0], "y": v[1]} for k, v in positions.items()},
            "subRoomParents": sub_parents,
            "connectors": connectors,
            "startRoomId": start,
        }

    @staticmethod
    def _anchor_sub_rooms(by_id, positions, sub_parents) -> None:
        """Place rooms the door BFS never reached, beside their entry room.

        Caves and basements are entered through cave/stair links, not doors,
        so they carry no door-graph position. Production hangs them one cell
        down-and-right of their parent, bumping X for siblings.
        """
        unanchored = sorted(rid for rid in by_id if rid not in positions)
        bumps: dict[str, int] = {}
        progressed = True
        while progressed and unanchored:
            progressed = False
            remaining = []
            for sub_id in unanchored:
                parent = _find_anchor_parent(by_id, sub_id, positions)
                if parent is None:
                    remaining.append(sub_id)
                    continue
                bump = bumps.get(parent, 0)
                px, py = positions[parent]
                positions[sub_id] = (px + 1 + bump, py - 1)
                sub_parents[sub_id] = parent
                bumps[parent] = bump + 1
                progressed = True
            unanchored = remaining


_DIRECTION_OFFSET = {0: (0, 1), 1: (0, -1), 2: (-1, 0), 3: (1, 0)}


def _find_anchor_parent(by_id, room_id, positions):
    """The positioned room a cave/basement is entered from, if any."""
    room = by_id.get(room_id) or {}
    for link in room.get("caveLinks") or []:
        for side in ("roomIdA", "roomIdB"):
            other = link.get(side)
            if other and other != room_id and other in positions:
                return other
    for link in room.get("stairLinks") or []:
        other = link.get("roomIdB") or link.get("toRoomId")
        if other and other in positions:
            return other
    # Reverse lookup: someone else's link points at us.
    for other_id, other in by_id.items():
        if other_id not in positions:
            continue
        for link in (other.get("stairLinks") or []) + (other.get("caveLinks") or []):
            if room_id in (link.get("roomIdB"), link.get("toRoomId"), link.get("roomIdA")):
                return other_id
    return None


def describe_condition(condition: dict | None) -> str:
    """One phrase for a step's condition. An omitted condition is immediate.

    `time` and `killed` are relative to the playhead entering that step, not to
    the start of the encounter (`SequentialEncounterRuntime`), so they are
    phrased as offsets rather than absolute times.
    """
    if not condition:
        return "immediately"
    kind = condition.get("kind")
    if kind == "time":
        seconds = condition.get("seconds", 0)
        return f"+{seconds:g}s"
    if kind == "cleared":
        return "when cleared"
    if kind == "killed":
        count = condition.get("count") or 1
        enemy = condition.get("enemyId") or "?"
        return f"after {count}× {enemy} killed"
    if kind == "touch":
        return f"on touching {condition.get('triggerId') or '?'}"
    if kind == "custom":
        return f"on {condition.get('key') or '?'}"
    return f"on {kind or 'unknown'}"


def _stage_budget(enemies_per_room: int, cut: float) -> int:
    """`StageEnemyBudget.Resolve` — how many bodies one room of a stage gets.

    A partial cut never rounds down to zero: 0.9 off a budget of 4 is 0.4 of
    an enemy, and answering "none" there would make an empty room reachable
    by a number that does not say so. Below one the answer is one; a room
    with no fight in it has to be asked for with a full cut of 1.
    """
    if cut <= 0:
        return enemies_per_room
    if cut >= 1:
        return 0
    # Round half away from zero, as C# does — Python's round() is half-even.
    scaled = math.floor(enemies_per_room * (1 - cut) + 0.5)
    return max(1, scaled)


def _cluster_brief(source: dict) -> dict | None:
    """OZX-1063 clumping, off a pool entry or off an encounter action.

    The two carry the same four fields — the sampler copies an entry's values
    onto the step it synthesises — so one reader serves both.

    Clumping is declared by a size above one. `clusterRadius` alone declares
    nothing, which is a finding rather than a shape: inside a clump the
    executor's minimum-spacing guard is waived, so the radius is the only
    thing keeping the bodies off one point.
    """
    lo = source.get("clusterSizeMin") or 0
    hi = source.get("clusterSizeMax") or 0
    radius = source.get("clusterRadius") or 0
    if hi <= 1 and lo <= 1:
        # A radius with no size still has to reach the view: it is authored
        # text that does nothing, and the row is where that gets noticed.
        return {"declared": False, "sizeMin": lo, "sizeMax": hi,
                "radius": radius} if radius else None
    return {"declared": True, "sizeMin": lo, "sizeMax": hi, "radius": radius}


def _code(identifier: str) -> str:
    """Two-letter glyph for a catalog card, from the id's word initials."""
    parts = [p for p in identifier.replace("-", "_").split("_") if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
