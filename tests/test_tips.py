"""HUD tips: `DoorLinkData.lockedTip` / `RoomNodeData.exitBlockedTip` (OLE-34).

OZX-1168's banner tells a refused player why. `RoomTips.Validated` throws
on a tip over 25 characters instead of cutting it, and the shipped-data
guard refuses one that is present but blank — omitting the key is how a
level says "no tip". chapter_1 and chapter_2 ship two each.
"""

import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_level  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

LEVEL = "chapter_1"


@pytest.fixture(scope="module")
def ds():
    return Dataset(OZX_BASE)


@pytest.fixture
def level(ds):
    doc = ds.get("LevelData", LEVEL)
    before = copy.deepcopy(doc.value)
    yield doc.value
    doc.value.clear()
    doc.value.update(before)


def tipped_door(level):
    return next(d for f in level["floors"] for r in f["rooms"]
                for d in r.get("doors") or [] if d.get("lockedTip"))


def tipped_room(level):
    return next(r for f in level["floors"] for r in f["rooms"] if r.get("exitBlockedTip"))


def found(ds, code):
    return [i for i in validate_level(ds, LEVEL) if i["code"] == code]


@pytest.mark.parametrize("level_id", ["chapter_1", "chapter_2"])
def test_the_shipped_tips_are_clean(ds, level_id):
    assert not [i for i in validate_level(ds, level_id) if "TIP" in i["code"]]


def test_twenty_five_characters_fit_and_twenty_six_do_not(ds, level):
    tipped_door(level)["lockedTip"] = "X" * 25
    assert not found(ds, "LEVEL_TIP_TOO_LONG")
    tipped_door(level)["lockedTip"] = "X" * 26
    assert found(ds, "LEVEL_TIP_TOO_LONG")[0]["severity"] == "error"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_present_but_blank_tip_is_refused(ds, level, blank):
    tipped_room(level)["exitBlockedTip"] = blank
    bad = found(ds, "LEVEL_TIP_BLANK")
    assert bad and bad[0]["fix"]["op"] == "remove"


def test_cjk_draws_as_missing_glyphs(ds, level):
    tipped_door(level)["lockedTip"] = "去找金钥匙"
    assert found(ds, "LEVEL_TIP_GLYPH_MISSING")[0]["severity"] == "warning"


def test_latin_accents_are_fine(ds, level):
    tipped_door(level)["lockedTip"] = "TROUVEZ LA CLÉ"
    assert not found(ds, "LEVEL_TIP_GLYPH_MISSING")


def test_a_tip_on_a_door_that_never_refuses_is_dead_text(ds, level):
    door = tipped_door(level)
    door["locked"] = False
    assert found(ds, "LEVEL_TIP_NEVER_SHOWN")


def test_an_exit_tip_without_required_pickups_is_dead_text(ds, level):
    tipped_room(level).pop("requiredForExitItemIds", None)
    assert found(ds, "LEVEL_TIP_NEVER_SHOWN")


def test_the_view_carries_both_tips(ds):
    rooms = [r for f in ds.level_view(LEVEL)["floors"] for r in f["rooms"]]
    assert any(d["lockedTip"] for r in rooms for d in r["doors"])
    assert any(r["exitBlockedTip"] and r["requiredForExitItemIds"] for r in rooms)


def test_a_tip_can_be_written_where_there_was_none_and_taken_back(tmp_path):
    """`put` adds the key the first time and `remove` takes it out again, and
    the file comes back byte-identical."""
    import shutil
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/levels",
                    root / "Assets/StreamingAssets/GameData/levels")
    api = Api(root)
    doc = api.dataset.get("LevelData", LEVEL)
    before = doc.path.read_bytes()
    fi, ri, di = next((fi, ri, di) for fi, f in enumerate(doc.value["floors"])
                      for ri, r in enumerate(f["rooms"])
                      for di, d in enumerate(r.get("doors") or [])
                      if "lockedTip" not in d)
    ptr = f"/floors/{fi}/rooms/{ri}/doors/{di}/lockedTip"
    api.edit("LevelData", LEVEL, [{"op": "put", "path": ptr, "value": "HELLO"}])
    assert b'"lockedTip": "HELLO"' in doc.path.read_bytes()
    api.edit("LevelData", LEVEL, [{"op": "remove", "path": ptr}])
    assert doc.path.read_bytes() == before
