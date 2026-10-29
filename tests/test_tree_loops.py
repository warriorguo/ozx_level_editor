"""`treeShape`'s local loops (OLE-71 / OZX-1578).

`trunkLoops` (of which `balancedLoops` keep both arms on the spine),
`branchLoops` (a ring at a branch's end) and `loopLength` (rows a loop
spans). `TreeShapeFromData` refuses a range whose max is below its min; the
tree generator refuses a negative count and a loop under two rows while any
loop is on. 0–0 on both counts is off, down to the rng stream.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

# The README's example: an unbalanced trunk loop and a branch ring, 2–3 rows.
LOOPS = {"trunkLoopsMin": 0, "trunkLoopsMax": 1, "balancedLoopsMin": 0, "balancedLoopsMax": 0,
         "branchLoopsMin": 0, "branchLoopsMax": 1, "loopLengthMin": 2, "loopLengthMax": 3}


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def shape(ds):
    """chapter_3's tree shape — written once, in floorDefaults."""
    return ds.get("LevelBasePlanData", "chapter_3").value["floorDefaults"]["treeShape"]


def loop_issues(ds):
    return [i for i in validate_plan(ds, "chapter_3") if i["code"].startswith("LEVEL_TREE_LOOP")]


def test_the_shipped_shapes_build_no_loops(ds):
    assert loop_issues(ds) == []
    assert [f["treeLoops"] for f in ds.plan_view("chapter_3")["floors"]] == [None] * 3


def test_the_readme_example_is_clean_and_shown(ds):
    shape(ds).update(LOOPS)
    assert loop_issues(ds) == []
    floor = ds.plan_view("chapter_3")["floors"][0]
    assert floor["treeLoops"] == {"trunk": [0, 1], "balanced": [0, 0],
                                  "branch": [0, 1], "length": [2, 3]}


def test_loops_without_a_loop_length_throw(ds):
    # The generator's 2–3 default is not inherited by a written shape.
    shape(ds).update({k: v for k, v in LOOPS.items() if not k.startswith("loopLength")})
    issues = loop_issues(ds)
    assert [i["code"] for i in issues] == ["LEVEL_TREE_LOOP_LENGTH_INVALID"]
    # One finding, on the default every floor inherits it from.
    assert issues[0]["pointer"] == "/floorDefaults/treeShape/loopLengthMin"
    assert "floors 0, 1, 2" in issues[0]["message"]


def test_a_loop_length_alone_is_ignored(ds):
    shape(ds).update({"loopLengthMin": 1, "loopLengthMax": 1})
    assert loop_issues(ds) == []


@pytest.mark.parametrize("fields,pointer", [
    ({"trunkLoopsMin": 2, "trunkLoopsMax": 1}, "trunkLoopsMax"),
    ({"loopLengthMin": 3, "loopLengthMax": 2}, "loopLengthMax"),
    ({"branchLoopsMin": -1}, "branchLoopsMin"),
])
def test_a_malformed_range_throws(ds, fields, pointer):
    shape(ds).update(dict(LOOPS, **fields))
    assert [(i["code"], i["pointer"]) for i in loop_issues(ds)] == [
        ("LEVEL_TREE_LOOPS_INVALID", f"/floorDefaults/treeShape/{pointer}")]


def test_balanced_loops_without_trunk_loops_build_nothing(ds):
    shape(ds).update({"balancedLoopsMin": 1, "balancedLoopsMax": 1})
    assert [(i["code"], i["severity"]) for i in loop_issues(ds)] == [
        ("LEVEL_TREE_LOOPS_IGNORED", "warning")]


def test_a_cycle_floor_reads_no_tree_shape(ds):
    shape(ds).update({"trunkLoopsMin": 2, "trunkLoopsMax": 1})
    for floor in ds.get("LevelBasePlanData", "chapter_3").value["floors"]:
        floor["generatorType"] = "cycle"
    assert loop_issues(ds) == []
