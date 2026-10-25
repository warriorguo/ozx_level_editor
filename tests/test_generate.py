"""Generated samples of a plan, from ozx_base's own genlevel (OLE-1).

These pin this tool's side of the contract — how the generator is found and
run, what a failure looks like, when a build is stale — and not what the
generator decides. Asserting its layouts here would be the OLE-16 mistake:
a test that goes red when upstream improves.
"""

import os
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import generate  # noqa: E402
from ozxlevel.api import Api  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
built = pytest.mark.skipif(
    not generate.status(OZX_BASE if OZX_BASE.is_dir() else None).get("available"),
    reason="no dotnet or no built Game.Balance.dll")


def fake_project(tmp_path, dll_age, source_age):
    """A project with one generator build and one C# source, aged as asked."""
    dll = tmp_path / "DotnetTests/Game.Balance/bin/Debug/net10.0/Game.Balance.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"")
    src = tmp_path / "Assets/Scripts/Game.Level/LevelBuilder.cs"
    src.parent.mkdir(parents=True)
    src.write_text("class X {}")
    unity = tmp_path / "Assets/Scripts/Game.Unity/View.cs"
    unity.parent.mkdir(parents=True)
    unity.write_text("class Y {}")
    now = time.time()
    os.utime(dll, (now - dll_age, now - dll_age))
    os.utime(src, (now - source_age, now - source_age))
    os.utime(unity, (now, now))   # never compiled into genlevel
    return tmp_path


def test_a_build_older_than_its_source_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "find_dotnet", lambda: "/usr/bin/true")
    info = generate.status(fake_project(tmp_path, dll_age=100, source_age=10))
    assert info["available"] and info["stale"]
    assert info["newerSource"].endswith("LevelBuilder.cs")


def test_unity_and_test_sources_do_not_make_a_build_stale(tmp_path, monkeypatch):
    """genlevel is not compiled from them; only the engine-free code counts."""
    monkeypatch.setattr(generate, "find_dotnet", lambda: "/usr/bin/true")
    info = generate.status(fake_project(tmp_path, dll_age=10, source_age=100))
    assert info["available"] and not info["stale"]


def test_no_build_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "find_dotnet", lambda: "/usr/bin/true")
    info = generate.status(tmp_path)
    assert not info["available"] and "dotnet build" in info["reason"]


def test_no_dotnet_is_said_plainly(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "find_dotnet", lambda: None)
    assert "dotnet" in generate.status(tmp_path)["reason"]


def test_a_summary_counts_what_a_designer_compares():
    doc = {"masterSeed": 9, "floors": [
        {"rooms": [{"roomId": "a", "stageType": "start", "hasTeleportSpot": True},
                   {"roomId": "b", "stageType": "peak", "isFinalRoom": True,
                    "doors": [{"locked": True}]}]}]}
    s = generate.summarize(doc)
    assert s["rooms"] == 2 and s["teleports"] == 1
    assert s["floors"][0]["stages"] == {"start": 1, "peak": 1}
    assert s["floors"][0]["lockedDoors"] == 1 and s["floors"][0]["finalRoom"] == "b"


@built
def test_a_sample_is_the_generators_and_is_read_only():
    api = Api(OZX_BASE)
    view = api.plan_sample("chapter_3", 7)
    assert not view.get("missing")
    assert view["readOnly"] is True
    assert view["sample"]["planId"] == "chapter_3" and view["sample"]["seed"] == 7
    assert all(f["rooms"] for f in view["floors"])
    # A generated room carries its waves inline; the run view resolves them.
    assert any(r["enemies"] for f in view["floors"] for r in f["rooms"])


@built
def test_the_same_seed_is_the_same_level():
    a = generate.generate(OZX_BASE, "chapter_3", 11)
    b = generate.generate(OZX_BASE, "chapter_3", 11)
    assert a == b


@built
def test_a_plan_that_cannot_generate_comes_back_as_a_finding():
    view = Api(OZX_BASE).plan_sample("no_such_plan", 1)
    assert view["missing"] is True
    assert "no_such_plan" in view["sample"]["error"]
    assert view["sample"]["detail"], "genlevel's stderr is kept whole"


@built
def test_several_seeds_compare_side_by_side():
    out = Api(OZX_BASE).plan_samples("chapter_3", [1, 2])
    assert [s["seed"] for s in out["samples"]] == [1, 2]
    assert all(s["rooms"] > 0 for s in out["samples"])


def test_only_sources_the_build_graph_compiles_can_make_it_stale(tmp_path, monkeypatch):
    """Read off Game.Balance.csproj and its ProjectReferences: a newer file in
    a tree genlevel never compiles (here Game.Skill) is not a stale build."""
    monkeypatch.setattr(generate, "find_dotnet", lambda: "/usr/bin/true")
    root = fake_project(tmp_path, dll_age=50, source_age=100)
    tests = root / "DotnetTests"
    (tests / "Game.Balance/Game.Balance.csproj").write_text(
        '<Project><ItemGroup><Compile Include="**/*.cs" />'
        '<ProjectReference Include="../Game.Level/Game.Level.csproj" /></ItemGroup></Project>')
    (tests / "Game.Level").mkdir()
    (tests / "Game.Level/Game.Level.csproj").write_text(
        '<Project><ItemGroup><Compile Include="../../Assets/Scripts/Game.Level/**/*.cs" />'
        '</ItemGroup></Project>')
    skill = root / "Assets/Scripts/Game.Skill/PassiveSkillListener.cs"
    skill.parent.mkdir(parents=True)
    skill.write_text("class Z {}")   # newest file of all, and not compiled
    assert [d.name for d in generate.compiled_source_dirs(root)] == ["Game.Level"]
    assert generate.status(root)["stale"] is False
    level = root / "Assets/Scripts/Game.Level/LevelBuilder.cs"
    os.utime(level, None)            # now a compiled file is newer than the build
    info = generate.status(root)
    assert info["stale"] and info["newerSource"].endswith("LevelBuilder.cs")
    assert str(root) in info["buildHint"]
