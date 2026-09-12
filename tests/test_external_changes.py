"""Files edited outside the editor.

The dataset used to be parsed once at startup, so the page served whatever the
files said when the server booted. That is wrong on screen, and worse than
wrong on write: a span patch is computed against the remembered text and the
whole result is written back, so editing through a stale view restored the old
file and took the outside change with it.

Reads now refresh, and writes refuse when the file moved underneath them.
"""

import json
import pathlib
import shutil
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset, ExternallyModified  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")


@pytest.fixture
def project(tmp_path):
    """A throwaway copy of GameData we can edit behind the editor's back."""
    root = tmp_path / "ozx_base"
    (root / "Assets/StreamingAssets").mkdir(parents=True)
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData",
                    root / "Assets/StreamingAssets/GameData")
    return root


def level_file(project, name="chapter_1"):
    return project / f"Assets/StreamingAssets/GameData/levels/{name}.json"


def edit_outside(path, old, new):
    """Change a file the way another tool or a git checkout would."""
    time.sleep(0.01)  # keep mtime distinguishable on coarse clocks
    text = path.read_text()
    assert old in text, f"fixture no longer contains {old!r}"
    path.write_text(text.replace(old, new, 1))


# ── reads ────────────────────────────────────────────────────────────────


def test_a_read_picks_up_an_outside_edit(project):
    api = Api(project)
    assert api.level("chapter_1")["floors"][0]["rooms"][0]["stageType"] == "start"

    edit_outside(level_file(project), '"stageType": "start"', '"stageType": "teaching"')

    # No restart, no Reload click — the next read simply tells the truth.
    assert api.level("chapter_1")["floors"][0]["rooms"][0]["stageType"] == "teaching"


def test_a_read_picks_up_a_new_file(project):
    api = Api(project)
    assert "loot_brand_new" not in [t["id"] for t in api.bootstrap()["lootCatalog"]]

    (project / "Assets/StreamingAssets/GameData/loot_tables/loot_brand_new.json").write_text(
        '{\n  "dataType": "LootTableData",\n  "id": "loot_brand_new",\n  "entries": []\n}\n')

    assert "loot_brand_new" in [t["id"] for t in api.bootstrap()["lootCatalog"]]


def test_a_read_notices_a_deleted_file(project):
    api = Api(project)
    doc = api.dataset.get("LootTableData", "loot_boss")
    assert doc is not None
    # Filenames do not carry ids here — loot_boss lives in boss_loot.json —
    # so take the path from the index rather than composing it.
    assert doc.path.name == "boss_loot.json"

    doc.path.unlink()
    api.dataset.refresh()

    assert api.dataset.get("LootTableData", "loot_boss") is None


def test_refresh_is_quiet_when_nothing_changed(project):
    ds = Dataset(project)
    assert ds.refresh() is False


# ── writes ───────────────────────────────────────────────────────────────


def test_a_write_against_a_stale_view_is_refused(project):
    """The whole point: do not restore old text over someone's edit."""
    api = Api(project)
    api.level("chapter_1")                      # load it
    doc = api.dataset.get("LevelData", "chapter_1")
    doc.stamp = (0, 0)                          # pretend the file moved

    with pytest.raises(ExternallyModified):
        doc.apply([{"op": "set", "path": "/floors/0/rooms/0/stageType",
                    "value": "peak"}])


def test_the_refused_write_leaves_the_outside_edit_intact(project):
    api = Api(project)
    api.level("chapter_1")
    doc = api.dataset.get("LevelData", "chapter_1")

    edit_outside(level_file(project), '"displayName"', '"displayNameX"')
    marker = level_file(project).read_text()

    result = api.edit("LevelData", "chapter_1",
                      [{"op": "set", "path": "/floors/0/rooms/0/stageType",
                        "value": "peak"}])

    assert result["ok"] is False
    assert result["externallyModified"] is True
    assert level_file(project).read_text() == marker, "the outside edit was clobbered"


def test_after_a_refusal_the_next_read_is_current_and_the_write_succeeds(project):
    api = Api(project)
    api.level("chapter_1")
    doc = api.dataset.get("LevelData", "chapter_1")

    edit_outside(level_file(project), '"stageType": "start"', '"stageType": "release"')
    refused = api.edit("LevelData", "chapter_1",
                       [{"op": "set", "path": "/floors/0/rooms/0/roomCategory",
                         "value": "test"}])
    assert refused["ok"] is False

    # The refusal refreshed the dataset, so the retry is against current text.
    view = api.level("chapter_1")
    assert view["floors"][0]["rooms"][0]["stageType"] == "release"

    again = api.edit("LevelData", "chapter_1",
                     [{"op": "set", "path": "/floors/0/rooms/0/roomCategory",
                       "value": "test"}])
    assert again["ok"] is True

    on_disk = json.loads(level_file(project).read_text())
    room = on_disk["floors"][0]["rooms"][0]
    assert room["roomCategory"] == "test"      # our edit landed
    assert room["stageType"] == "release"      # and theirs survived


def test_door_writes_are_guarded_too(project):
    api = Api(project)
    api.level("chapter_1")
    api.dataset.get("LevelData", "chapter_1").stamp = (0, 0)

    result = api.set_door("chapter_1", 0, 4, "E", False)
    assert result["ok"] is False
    assert result["externallyModified"] is True


# ── derived data the client caches ───────────────────────────────────────


def test_the_revision_moves_when_a_file_changes(project):
    """Clients cache catalogs; they need a cheap way to know those are stale."""
    api = Api(project)
    start = api.bootstrap()["revision"]

    edit_outside(level_file(project), '"stageType": "start"', '"stageType": "peak"')
    api.dataset.refresh()

    assert api.bootstrap()["revision"] != start


def test_the_revision_holds_still_when_nothing_changes(project):
    api = Api(project)
    start = api.bootstrap()["revision"]
    api.dataset.refresh()
    assert api.bootstrap()["revision"] == start


def test_a_level_read_reports_the_revision(project):
    """So the client can compare without a second round trip."""
    api = Api(project)
    assert api.level("chapter_1")["revision"] == api.dataset.revision


def test_a_loot_table_added_outside_reaches_the_catalog(project):
    """The case that was broken: the library was frozen at page load."""
    api = Api(project)
    assert "loot_brand_new" not in [t["id"] for t in api.bootstrap()["lootCatalog"]]

    before = api.level("chapter_1")["revision"]
    (project / "Assets/StreamingAssets/GameData/loot_tables/loot_brand_new.json").write_text(
        '{\n  "dataType": "LootTableData",\n  "id": "loot_brand_new",\n  "entries": []\n}\n')

    after = api.level("chapter_1")["revision"]
    assert after != before, "the client would never know to re-fetch"
    assert "loot_brand_new" in [t["id"] for t in api.bootstrap()["lootCatalog"]]
