"""Config persistence and mounting a project folder at runtime.

A terminal run can pass `--root`. A double-clicked app cannot, so the folder
has to persist, be changeable from the UI, and — critically — its absence must
not stop the server from starting, or there is no UI in which to fix it.
"""

import json
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.api import Api  # noqa: E402
from ozxlevel.config import Config, looks_like_project  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "ozx_base"
    (root / "Assets/StreamingAssets").mkdir(parents=True)
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData",
                    root / "Assets/StreamingAssets/GameData")
    return root


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "config" / "config.json"


# ── the file ─────────────────────────────────────────────────────────────


def test_missing_config_gives_defaults(config_path):
    cfg = Config.load(config_path)
    assert cfg.project_root == ""
    assert cfg.port == 8765
    assert cfg.auto_open_browser is True


def test_a_corrupt_config_is_treated_as_absent(config_path):
    """Must not be fatal — the app has to start so the folder can be fixed."""
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{ this is not json")
    cfg = Config.load(config_path)
    assert cfg.project_root == ""


def test_round_trip(config_path, project):
    cfg = Config.load(config_path)
    cfg.project_root = str(project)
    cfg.port = 9001
    cfg.auto_open_browser = False
    cfg.save()

    again = Config.load(config_path)
    assert again.project_root == str(project)
    assert again.port == 9001
    assert again.auto_open_browser is False
    # readable by hand, which is the point of the XDG location
    assert json.loads(config_path.read_text())["port"] == 9001


# ── validation, phrased for a human ──────────────────────────────────────


def test_a_good_folder_validates(project):
    assert looks_like_project(project)[0] is True


def test_a_folder_without_gamedata_is_rejected_with_a_reason(tmp_path):
    ok, reason = looks_like_project(tmp_path)
    assert ok is False
    assert "Assets/StreamingAssets/GameData" in reason


def test_resolve_explains_why_a_folder_is_unusable(config_path, tmp_path):
    cfg = Config.load(config_path)
    cfg.project_root = str(tmp_path / "nope")
    r = cfg.resolve()
    assert r.usable is False
    assert "does not exist" in r.reason

    cfg.project_root = str(tmp_path)          # exists, but not a checkout
    r = cfg.resolve()
    assert r.usable is False and r.exists is True
    assert "pick the ozx_base checkout itself" in r.reason


# ── starting with nothing mounted ────────────────────────────────────────


def test_an_unmounted_dataset_answers_every_read():
    """No project yet is a normal startup state, not an error path."""
    ds = Dataset.empty()
    assert ds.docs == {}
    assert ds.refresh() is False
    assert ds.of_type("LevelData") == []
    assert ds.level_summaries() == []
    assert ds.enemy_catalog() == []
    assert ds.problems[0]["code"] == "DATA_NO_PROJECT"


def test_the_api_starts_with_no_project(config_path):
    api = Api(None, Config.load(config_path))
    assert api.get_config()["mounted"] is False
    boot = api.bootstrap()
    assert boot["counts"]["documents"] == 0
    assert boot["levels"] == []


# ── mounting at runtime ──────────────────────────────────────────────────


def test_setting_the_folder_mounts_and_persists(config_path, project):
    api = Api(None, Config.load(config_path))
    result = api.set_project_root(str(project))

    assert result["ok"] is True
    assert result["mounted"] is True
    assert result["documents"] > 300
    assert api.level("chapter_1")["floors"][0]["rooms"]

    # and the choice survives a restart
    assert Config.load(config_path).project_root == str(project.resolve())


def test_a_bad_folder_is_refused_without_unmounting(config_path, project, tmp_path):
    api = Api(project, Config.load(config_path))
    before = len(api.dataset.docs)

    result = api.set_project_root(str(tmp_path / "not-a-checkout"))

    assert result["ok"] is False
    assert "does not exist" in result["error"]
    assert len(api.dataset.docs) == before, "a refused swap must not unmount"


def test_mounting_bumps_the_revision(config_path, project):
    """So the client re-fetches its cached catalogs for the new project."""
    api = Api(None, Config.load(config_path))
    before = api.bootstrap()["revision"]
    api.set_project_root(str(project))
    assert api.bootstrap()["revision"] != before


def test_swapping_between_projects(config_path, project, tmp_path):
    """Two checkouts, switched at runtime, no restart."""
    second = tmp_path / "other_base"
    (second / "Assets/StreamingAssets").mkdir(parents=True)
    shutil.copytree(project / "Assets/StreamingAssets/GameData",
                    second / "Assets/StreamingAssets/GameData")
    (second / "Assets/StreamingAssets/GameData/levels/chapter_1.json").unlink()

    api = Api(project, Config.load(config_path))
    assert api.dataset.get("LevelData", "chapter_1") is not None

    api.set_project_root(str(second))
    assert api.dataset.get("LevelData", "chapter_1") is None
    assert api.get_config()["project_root"] == str(second.resolve())


# ── finding the checkout without being told ──────────────────────────────


def test_discovery_finds_a_checkout_beside_the_caller(tmp_path, project):
    """The repo layout: ozx_base sitting next to this repo."""
    from ozxlevel.config import discover_project
    sibling = tmp_path / "some_other_repo"
    sibling.mkdir()
    # `project` is tmp_path/ozx_base, i.e. a sibling of `sibling`
    assert discover_project(near=sibling) == project.resolve()


def test_discovery_finds_nothing_when_there_is_nothing(tmp_path, monkeypatch):
    from ozxlevel.config import discover_project
    empty = tmp_path / "home"
    (empty / "Codes").mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: empty))
    assert discover_project(near=empty / "Codes") is None


def test_discovery_ignores_a_folder_that_is_not_a_checkout(tmp_path, monkeypatch):
    """A directory named ozx_base without GameData is not the thing."""
    from ozxlevel.config import discover_project
    home = tmp_path / "home"
    (home / "Codes" / "ozx_base").mkdir(parents=True)   # no GameData inside
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    assert discover_project() is None


def test_discovery_reaches_two_levels_down(tmp_path, monkeypatch):
    """Covers ~/Codes/github.com/<user>/ozx_base, which is the real layout."""
    from ozxlevel.config import discover_project
    home = tmp_path / "home"
    deep = home / "Codes" / "github.com" / "someone" / "ozx_base"
    (deep / "Assets/StreamingAssets/GameData").mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    assert discover_project() == deep.resolve()


def test_discovery_is_fast_enough_to_run_on_every_launch(tmp_path, monkeypatch):
    """It runs whenever nothing is configured, so it must not scan $HOME deeply."""
    import time
    from ozxlevel.config import discover_project
    home = tmp_path / "home"
    for i in range(40):
        (home / "Codes" / f"repo{i}" / "src").mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    start = time.time()
    discover_project()
    assert time.time() - start < 1.0


def test_the_api_suggests_a_folder_when_none_is_set(config_path):
    """So the picker is a confirmation, not a typing exercise."""
    api = Api(None, Config.load(config_path))
    cfg = api.get_config()
    assert cfg["mounted"] is False
    # On this machine there is a real checkout to find; elsewhere the key is
    # still present and simply empty.
    assert "suggestion" in cfg
    if cfg["suggestion"]:
        assert looks_like_project(cfg["suggestion"])[0] is True
