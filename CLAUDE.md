# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

A working editor. `python3 serve.py` starts it; `python3 -m pytest tests/ -q` runs the
tests. Python 3.11+, **standard library only** — no dependencies, no build step. Keep it
that way unless there is a strong reason not to.

- `serve.py` — entry point.
- `ozxlevel/jsonspan.py` — span-recording JSON parser and byte-preserving patcher. The
  acceptance test is a zero-byte-diff round trip over all 387 data files; do not replace
  it with a pretty-printer.
- `ozxlevel/dataset.py` — GameData index and the projections the UI reads.
- `ozxlevel/validate.py` — the silent-failure rules (see below).
- `ozxlevel/api.py` — loopback HTTP API and static serving. Reads call
  `Dataset.refresh()` first; writes never do — they refuse when the file moved,
  because a pointer-addressed patch against changed text can hit the wrong node.
- `web/` — the browser client, built from `level_editor_template.html` (the design is
  the spec; keep `styles.css` in step with it).
- `swift-app/` — the macOS wrapper. `make -C swift-app build`. It spawns `serve.py`
  with **stock `/usr/bin/python3` (3.9)**, so the package must stay 3.9-compatible;
  `tests/test_python39.py` enforces that and `tests/test_app_bundle.py` pins the
  wrapper's contract. Never register Cmd+S as a menu item — AppKit would steal it
  from the page, which binds it itself.

`Documents/` holds three Chinese docs, all verified against `ozx_base@d867b644` — keep new
claims sourced the same way (cite `file:line`), and update these rather than starting
parallel docs:

- `level_data_reference.md` — the on-disk schema of `GameData/levels/`.
- `level_referenced_data.md` — the five data families level data points at.

The technical design lives in the sibling repo, at
`ozx_base/Documents/Shared/OZX_Level_Data_Editor_TD.md` (the `ozx_level_data_editor_td.md`
that `level_data_reference.md` cites in its header). It designs this as a Unity Editor
tool on UI Toolkit; the implementation here is a browser client on a local Python server
instead. The TD's data findings still hold — the silent-failure list, the formatting
constraints, the compiler extraction in §9.1 — but its architecture section describes a
tool that was not built. Reconcile it when editing.

## What this project is

A Unity Editor tool for authoring OZX level data — static `LevelData`, procedural `LevelBasePlanData`, `EncounterData`, `EnemyData`, `LootTableData`. JSON stays the shipped format; the tool never introduces a second runtime schema.

## Companion repository (read this before writing code)

The data and the game runtime live in a different repo: `/Users/andrew/Codes/github.com/warriorguo/ozx_base`.

- Data: `Assets/StreamingAssets/GameData/**` (363 files) and `Assets/StreamingAssets/TilemapData/**` (23 files).
- Production types and rules the tool must reuse rather than reimplement: `Game.Contracts/Data/*`, `Game.Level/LevelBasePlanAssigner.cs`, `Game.Unity/Level/DynamicLevelBuilder.cs`, `Game.Encounter/EncounterValidator.cs`, `Game.Room/Tilemap/RoomTilemapQuery.cs`, `Game.Loot/DropTableResolver.cs`. Appendix A of the TD is the full list.
- `ozx_base/CLAUDE.md` carries project-wide rules (subsystem README/TODO protocol, room-scoped spawn lifecycle) that apply to any code landing in that tree.

**Unresolved:** TD §5.1 places the new assemblies (`Game.Authoring.Core`, `Game.Level.Compilation`, `Game.Unity.Editor.LevelStudio`) *inside* `ozx_base/Assets/Scripts/`. How code in this repo reaches that Unity project — submodule, UPM package, or copy — has not been decided. Settle it before writing the first assembly; it changes the asmdef and test layout.

## Architecture the design commits to

- **DOM is the only writable truth.** UI edits a `JToken` working DOM; the typed C# snapshot is a read-only projection rebuilt after each command. `JsonUtility` cannot distinguish omitted from default, so typed objects must never be the edit target.
- **Every write is a command** (`Set/Remove/Insert/Move` addressed by JSON Pointer). The command journal is the only undo/redo mechanism — no second state in Unity serialization.
- **Reuse the production compiler, never approximate it.** Preview and CI compile through the same extracted `LevelCompiler` as `GameBootstrap` and `/api/reload`.
- **Assembly DAG is one-way.** Nothing in the runtime/domain assemblies may reference authoring or editor assemblies; `Game.Level.Compilation` may not touch `Game.Unity`, `ServiceLocator`, `AssetDatabase`, or Editor APIs.
- **Two CI lanes.** Pure-C# validation runs under `dotnet test` via `ozx_base/DotnetTests/`; only `JsonUtility` parity, template compatibility, and editor tests need Unity batchmode. `ozx_base` has no CI at all today — it is infrastructure to build, not something to plug into.

## Data invariants that fail silently

These are the ones that produce broken levels with no error, so they belong in validation and in any code that writes data:

- **Encounter "wait" is an absent `action`**, not `"verb": "wait"`. The only legal verb is `launch`; anything else throws in `EncounterValidator`. Likewise "immediate" is an **omitted `condition`**.
- **Tilemap JSON is `[x][y]`** — the outer array is the column (`RoomTilemapData.Visit`, `RoomGroundRenderer` read it that way). Reading it as rows transposes non-square rooms and makes cell/footprint checks lie. Every file's own `meta.width/height` is stated the other way round (all 23 have `meta.width == inner length`), so trust the code, not `meta`.
- **Unknown `generatorType` silently becomes `tree`** (`ParseGeneratorType`), so an unknown value there must be an Error, not a Warning.
- **`inlineEncounter` / `inlineLootPlan` / `floorLootTableId` are `[NonSerialized]`** runtime synthesis. Never write them back to source JSON.
- **Duplicate `(dataType, id)` is silently overwritten** by `JsonDataRepository` — the last file loaded wins, with no error.
- **A door to another floor's room without twin `stairLinks` is a silent dead end**, and a new `openDoors` mask with no matching same-`stageType` tilemap gets sealed by the generator.

## Writing data back

- Untouched files must come out **byte-identical**. GameData is hand-formatted (inline arrays like `"spriteKeys": ["anim/bubble"]`), two files are CRLF, and all TilemapData is single-line minified — a naive pretty-printer destroys all three. The writer's acceptance test is a zero-byte-diff round trip over all 386 files.
- `.meta` files are part of every create/delete/rename transaction. Never hand-write a GUID; let Unity generate it.
- **Cross-repo sync is mandatory**: a GameData shape/rule-key/convention change needs a companion OAE ticket; any RoomTilemapData change needs an ORT ticket. TilemapData is produced upstream by the room-template backend and pushed down by sync, so local edits to it get overwritten — MVP treats it as read-only (TD §12.1).

## Conventions

- The TD is written in Chinese; keep it in Chinese when editing.
- Issue codes are stable machine identifiers (`LEVEL_DOOR_TWIN_MISSING`, …) — see TD Appendix B before inventing a new one.
