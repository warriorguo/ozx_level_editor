"""A sample of a procedural plan, from ozx_base's own generator (OLE-1).

A `LevelBasePlanData` has no rooms until something generates them, and the
route is settled: do not rewrite the generator in Python — call the headless
entry OZX ships (OZX-991) and render what it returns through the ordinary
level view. One algorithm, so the preview cannot drift from the game:
teleport spots (OLE-30), spacing (OLE-31), stage dealing (OLE-48), sampling
(OLE-47, OLE-49) all come out of the same `LevelBuilder` the game runs.

    dotnet <ozx_base>/DotnetTests/Game.Balance/bin/<cfg>/<tfm>/Game.Balance.dll \\
        genlevel --plan <id> --seed <int> [--gamedata <dir>]

stdout is the generated `LevelData` and nothing else (logs go to stderr);
same plan + seed is byte-identical. Exit 2 is a usage error, any other
non-zero a generation failure whose stderr is worth showing as is — a plan
that cannot generate is the designer's to see, not this tool's bug.

The DLL is built from the C# in `Assets/Scripts`, so it can be older than
the code it claims to run. GameData is read live (`--gamedata`), so data
edits show at once; a code change needs a rebuild, and a preview from a
stale build would show the old rules with a straight face. So every sample
says whether its build predates the newest source it was compiled from.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

BUILD_HINT = ("cd <ozx_base>/DotnetTests && "
              "dotnet build Game.Balance/Game.Balance.csproj -m:1")

# The app wrapper spawns serve.py with a minimal PATH, which has no Homebrew.
_DOTNET_FALLBACKS = ("/opt/homebrew/bin/dotnet", "/usr/local/share/dotnet/dotnet",
                     "/usr/local/bin/dotnet", "~/.dotnet/dotnet")

# Source trees genlevel is not compiled from: the Unity layer and the tests.
_NOT_COMPILED = ("Game.Unity", "Game.Tests", "Editor")


class GenerationError(RuntimeError):
    """genlevel ran and failed; `detail` is its stderr, kept whole."""

    def __init__(self, message: str, detail: str = "", code: int | None = None):
        super().__init__(message)
        self.detail = detail
        self.code = code


def find_dotnet() -> str | None:
    found = shutil.which("dotnet")
    if found:
        return found
    for candidate in _DOTNET_FALLBACKS:
        path = pathlib.Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def find_generator(root: pathlib.Path) -> pathlib.Path | None:
    """The newest built Game.Balance.dll, Debug or Release, any target."""
    built = list((root / "DotnetTests/Game.Balance/bin").glob("*/*/Game.Balance.dll"))
    return max(built, key=lambda p: p.stat().st_mtime) if built else None


def compiled_source_dirs(root: pathlib.Path) -> list[pathlib.Path] | None:
    """The `Assets/Scripts` trees genlevel is actually built from.

    Read off the build graph rather than guessed: `Game.Balance.csproj` and
    every project it references, transitively, each naming its sources as
    `<Compile Include="../../Assets/Scripts/Game.X/**/*.cs" />`. Guessing
    ("everything but Unity and the tests") called a build stale over
    `Game.Skill`, which genlevel never compiles. None when the project file
    cannot be read, so the caller can fall back.
    """
    import re
    start = root / "DotnetTests/Game.Balance/Game.Balance.csproj"
    if not start.is_file():
        return None
    seen: set = set()
    dirs: list[pathlib.Path] = []
    stack = [start.resolve()]
    while stack:
        proj = stack.pop()
        if proj in seen or not proj.is_file():
            continue
        seen.add(proj)
        text = proj.read_text(encoding="utf-8", errors="ignore")
        for ref in re.findall(r'<ProjectReference\s+Include="([^"]+)"', text):
            stack.append((proj.parent / ref.replace("\\", "/")).resolve())
        for glob in re.findall(r'<Compile\s+Include="([^"]+)"', text):
            glob = glob.replace("\\", "/")
            base = glob.split("*", 1)[0].rstrip("/")
            folder = (proj.parent / base).resolve()
            if "Assets/Scripts" in str(folder) and folder.is_dir():
                dirs.append(folder)
    return sorted(set(dirs))


def newest_source(root: pathlib.Path) -> tuple[float, str | None]:
    """mtime and path of the newest C# file genlevel is built from."""
    dirs = compiled_source_dirs(root)
    if dirs is None:
        scripts = root / "Assets/Scripts"
        dirs = [d for d in scripts.iterdir()
                if d.is_dir() and not d.name.startswith(_NOT_COMPILED)] if scripts.is_dir() else []
    newest, which = 0.0, None
    for folder in dirs:
        for path in folder.rglob("*.cs"):
            mtime = path.stat().st_mtime
            if mtime > newest:
                newest, which = mtime, str(path.resolve().relative_to(root.resolve()))
    return newest, which


def status(root: pathlib.Path | None) -> dict:
    """Can a sample be generated here, and is the build current?"""
    if root is None:
        return {"available": False, "reason": "no project folder"}
    dotnet = find_dotnet()
    dll = find_generator(root)
    if dotnet is None:
        return {"available": False, "reason": "dotnet is not installed or not on PATH"}
    if dll is None:
        return {"available": False,
                "reason": f"the headless generator is not built — {BUILD_HINT}"}
    source_time, source = newest_source(root)
    built = dll.stat().st_mtime
    return {"available": True, "dotnet": dotnet, "generator": str(dll),
            "builtAt": built, "stale": source_time > built,
            "newerSource": source if source_time > built else None,
            "buildHint": BUILD_HINT.replace("<ozx_base>", str(root))}


def generate(root: pathlib.Path, plan_id: str, seed: int, timeout: float = 120) -> dict:
    """Run genlevel and return the generated LevelData document."""
    info = status(root)
    if not info["available"]:
        raise GenerationError(info["reason"])
    cmd = [info["dotnet"], info["generator"], "genlevel", "--plan", plan_id,
           "--seed", str(int(seed)),
           "--gamedata", str(root / "Assets/StreamingAssets/GameData")]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             cwd=str(root))
    except subprocess.TimeoutExpired:
        raise GenerationError(f"genlevel took longer than {timeout:g}s and was stopped")
    if run.returncode != 0:
        first = next((line for line in run.stderr.splitlines() if line.strip()), "")
        raise GenerationError(
            f"genlevel failed for plan '{plan_id}' seed {seed}: {first}",
            detail=run.stderr, code=run.returncode)
    try:
        return json.loads(run.stdout)
    except ValueError as exc:
        raise GenerationError(f"genlevel printed something that is not JSON: {exc}",
                              detail=run.stdout[:2000])


def summarize(doc: dict) -> dict:
    """What one sample came out as, in numbers — for comparing several seeds.

    One sample is one draw; a designer judging a plan needs the spread. So
    each floor reports its room count, its stage mix, its teleport spots and
    its locked doors, and the level its total — enough to see whether a
    property holds on every seed or only on the one that happened to be
    opened.
    """
    floors = []
    for floor in doc.get("floors") or []:
        rooms = floor.get("rooms") or []
        stages: dict = {}
        for room in rooms:
            stage = room.get("stageType") or "unset"
            stages[stage] = stages.get(stage, 0) + 1
        floors.append({
            "rooms": len(rooms),
            "stages": stages,
            "teleports": sum(1 for r in rooms if r.get("hasTeleportSpot")),
            "lockedDoors": sum(1 for r in rooms for d in r.get("doors") or [] if d.get("locked")),
            "finalRoom": next((r.get("roomId") for r in rooms if r.get("isFinalRoom")), None),
            # OZX-1568: story rooms whose one placement is an upgrade platform.
            "upgradeRooms": sum(1 for r in rooms if r.get("stageType") == "story" and any(
                (p or {}).get("kind") == "upgrade" for p in r.get("staticPlacements") or [])),
        })
    return {"masterSeed": doc.get("masterSeed"), "floors": floors,
            "rooms": sum(f["rooms"] for f in floors),
            "teleports": sum(f["teleports"] for f in floors),
            # OZX-1483: the dead ends StoryRoomPlanner hangs off the layout.
            "storyRooms": sum(f["stages"].get("story", 0) for f in floors),
            # OZX-1568: of those, the upgrade-platform ones, and the areas they
            # landed in — which should all be areas every run passes through.
            "upgradeRooms": sum(f["upgradeRooms"] for f in floors),
            "upgradeAreas": [i for i, f in enumerate(floors) for _ in range(f["upgradeRooms"])]}
