"""Cave passages (OLE-79 / OLE-80 / OLE-81).

OZX-1806 gave a cave link a `cargoPlacement` block (crates on the cave parts'
CargoPlaces markers, `CaveCargoPlacementLogic.Validate` /
`CargoVariantLogic.CaveLooks`); OZX-1805 lets only zoner launches find a spot
in a cave. OZX-1807 has a plan's `caves[]` carry the grid, tentacles and
crates onto the link it generates (and throw without a grid); OZX-1808 makes
every cave — passage or `roomCategory: "cave"` room — carry `noRoots: true`
(`LevelValidator.ValidateCaveRoots`, at level load).
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_all, validate_level, validate_plan  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

CAVE_CODES = {"LEVEL_CAVE_ROOTS_MISSING", "LEVEL_CAVE_GRID_INVALID",
              "LEVEL_PLAN_CAVE_GRID_INVALID", "LEVEL_CAVE_CARGO_COUNT_INVALID",
              "LEVEL_CAVE_CARGO_LOOT_MISSING", "LEVEL_CAVE_CARGO_NOT_CAVE_CRATE",
              "LEVEL_CAVE_TENTACLE_TYPES_EMPTY", "LEVEL_CAVE_LAUNCH_SKIPPED"}
LINK = "/floors/0/caveLinks/0"
PLAN_CAVE = "/floors/0/caves/0"


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues, extra=()):
    """(code, pointer) for the cave codes, plus DATA_REFERENCE_MISSING under a cave."""
    return [(i["code"], i["pointer"]) for i in issues
            if i["code"] in CAVE_CODES
            or (i["code"] == "DATA_REFERENCE_MISSING" and "cave" in (i["pointer"] or ""))]


def link(ds, level_id="level_test"):
    return ds.get("LevelData", level_id).value["floors"][0]["caveLinks"][0]


def plan_cave(ds):
    return ds.get("LevelBasePlanData", "level_02").value["floors"][0]["caves"][0]


def test_the_shipped_data_is_clean(ds):
    assert found(validate_all(ds)) == []


# ── OZX-1808: caves grow no roots ──────────────────────────────────────────


def test_a_cave_passage_must_say_no_roots(ds):
    del link(ds)["noRoots"]
    issues = [i for i in validate_level(ds, "level_test") if i["code"] == "LEVEL_CAVE_ROOTS_MISSING"]
    assert [i["pointer"] for i in issues] == [LINK]
    assert issues[0]["fix"] == {"op": "put", "path": f"{LINK}/noRoots", "value": True,
                                "label": "Set noRoots"}
    link(ds)["noRoots"] = False
    assert found(validate_level(ds, "level_test")) == [
        ("LEVEL_CAVE_ROOTS_MISSING", f"{LINK}/noRoots")]


def test_a_direct_link_has_no_passage_to_root_in(ds):
    lk = link(ds)
    del lk["noRoots"]
    lk["linkType"] = "direct"
    lk["gridWidth"] = 0
    assert found(validate_level(ds, "level_test")) == []


def test_a_cave_room_must_say_no_roots(ds):
    floor = ds.get("LevelData", "level_cave_demo").value["floors"][0]
    ri = next(i for i, r in enumerate(floor["rooms"]) if r.get("roomCategory") == "cave")
    del floor["rooms"][ri]["noRoots"]
    assert ("LEVEL_CAVE_ROOTS_MISSING", f"/floors/0/rooms/{ri}") \
        in found(validate_level(ds, "level_cave_demo"))


# ── a passage needs a grid (CaveSystemAdapter / OZX-1807) ──────────────────


def test_a_cave_passage_needs_a_grid(ds):
    link(ds)["gridHeight"] = 0
    assert found(validate_level(ds, "level_test")) == [
        ("LEVEL_CAVE_GRID_INVALID", f"{LINK}/gridHeight")]


def test_a_plan_cave_without_a_grid_does_not_generate(ds):
    del plan_cave(ds)["gridWidth"]
    issues = [i for i in validate_plan(ds, "level_02") if i["code"] == "LEVEL_PLAN_CAVE_GRID_INVALID"]
    assert [i["pointer"] for i in issues] == [f"{PLAN_CAVE}/gridWidth"]
    assert "gridWidth=0, gridHeight=4" in issues[0]["message"]


def test_an_inherited_plan_cave_is_reported_once_in_the_defaults(ds):
    plan = ds.get("LevelBasePlanData", "level_02").value
    caves = plan["floors"][0].pop("caves")
    caves[0]["cargoPlacement"]["lootTableId"] = ""
    plan["floorDefaults"] = {"caves": caves}
    issues = [i for i in validate_plan(ds, "level_02")
              if i["code"] == "LEVEL_CAVE_CARGO_LOOT_MISSING"]
    assert [i["pointer"] for i in issues] == ["/floorDefaults/caves/0/cargoPlacement/lootTableId"]
    assert "inherited from floorDefaults by floors 0, 1" in issues[0]["message"]


# ── OZX-1806 / OZX-1815: crates in a cave ──────────────────────────────────


@pytest.mark.parametrize("change, expected", [
    ({"count": -1}, [("LEVEL_CAVE_CARGO_COUNT_INVALID", "/cargoPlacement/count")]),
    ({"lootTableId": ""}, [("LEVEL_CAVE_CARGO_LOOT_MISSING", "/cargoPlacement/lootTableId")]),
    ({"lootTableId": "no_such_table"}, [("DATA_REFERENCE_MISSING", "/cargoPlacement/lootTableId")]),
    ({"cargoId": "no_such_crate"}, [("DATA_REFERENCE_MISSING", "/cargoPlacement/cargoId")]),
    ({"cargoId": "cargo_box_2"}, [("LEVEL_CAVE_CARGO_NOT_CAVE_CRATE", "/cargoPlacement/cargoId")]),
    ({"cargoId": "cargo_nest_large"}, []),
    # Nothing is stocked, so the crate is never looked up — but the table is.
    ({"count": 0, "cargoId": "cargo_box_2"}, []),
])
def test_cave_cargo_is_held_to_what_stocking_the_cave_throws_on(ds, change, expected):
    link(ds)["cargoPlacement"].update(change)
    assert found(validate_level(ds, "level_test")) == [(c, LINK + p) for c, p in expected]
    plan_cave(ds)["cargoPlacement"].update(change)
    assert found(validate_plan(ds, "level_02")) == [(c, PLAN_CAVE + p) for c, p in expected]


def test_a_block_with_no_table_is_refused_even_when_it_stocks_nothing(ds):
    link(ds)["cargoPlacement"] = {"count": 0}
    assert found(validate_level(ds, "level_test")) == [
        ("LEVEL_CAVE_CARGO_LOOT_MISSING", f"{LINK}/cargoPlacement")]


def test_no_block_stocks_nothing(ds):
    del link(ds)["cargoPlacement"]
    assert found(validate_level(ds, "level_test")) == []


def test_no_cave_crate_at_all(ds):
    for doc in ds.of_type("CargoData"):
        doc.value.pop("caveCrate", None)
    assert found(validate_level(ds, "level_test")) == [
        ("LEVEL_CAVE_CARGO_NOT_CAVE_CRATE", f"{LINK}/cargoPlacement")]


# ── tentacles and the encounter ────────────────────────────────────────────


def test_tentacles_need_types_that_exist(ds):
    tent = link(ds)["tentaclePlacement"]
    tent["allowedTypes"] = ["tentacle_basic", "no_such_tentacle"]
    assert found(validate_level(ds, "level_test")) == [
        ("DATA_REFERENCE_MISSING", f"{LINK}/tentaclePlacement/allowedTypes/1")]
    tent["allowedTypes"] = []
    assert found(validate_level(ds, "level_test")) == [
        ("LEVEL_CAVE_TENTACLE_TYPES_EMPTY", f"{LINK}/tentaclePlacement/allowedTypes")]
    tent["count"] = 0
    assert found(validate_level(ds, "level_test")) == []


def test_only_a_zoner_finds_a_spot_in_a_cave(ds):
    chaser = next(d.id for d in ds.of_type("EnemyData")
                  if d.value.get("role") == "chaser"
                  and not (d.value.get("railConfig") or {}).get("enabled"))
    step = ds.get("EncounterData", "enc_cave_test_01").value["steps"][0]
    step["action"]["enemyId"] = chaser
    issues = [i for i in validate_level(ds, "level_test") if i["code"] == "LEVEL_CAVE_LAUNCH_SKIPPED"]
    assert [i["pointer"] for i in issues] == [f"{LINK}/encounterId"]
    assert issues[0]["severity"] == "warning" and f"'{chaser}' on 'chaser'" in issues[0]["message"]
    # The plan line that names the same encounter is told the same.
    assert ("LEVEL_CAVE_LAUNCH_SKIPPED", f"{PLAN_CAVE}/encounterId") \
        in found(validate_plan(ds, "level_02"))
    # Launched at zoner explicitly, it stands on ZonerPlaces.
    step["action"]["at"] = "zoner"
    assert found(validate_level(ds, "level_test")) == []


def test_a_cave_encounter_must_exist(ds):
    link(ds)["encounterId"] = "no_such_encounter"
    assert found(validate_level(ds, "level_test")) == [
        ("DATA_REFERENCE_MISSING", f"{LINK}/encounterId")]


# ── what the editor shows ──────────────────────────────────────────────────


def test_the_level_view_shows_each_passage(ds):
    floor = ds.level_view("level_test")["floors"][0]
    (cave,) = floor["caveLinks"]
    assert cave == {
        "pointer": LINK, "caveId": "cave_test_01",
        "roomIdA": "f0_room_0_0", "roomIdB": "f0_room_2_0", "linkType": "cave",
        "encounterId": "enc_cave_test_01", "encounterMissing": False,
        "gridWidth": 3, "gridHeight": 4, "noRoots": True,
        "tentaclePlacement": {"count": 10, "allowedTypes": [
            "tentacle_basic", "tentacle_short", "tentacle_long", "tentacle_fast"]},
        "cargoPlacement": {"count": 6, "lootTableId": "loot_common", "cargoId": None},
    }
    demo = ds.level_view("level_cave_demo")["floors"][0]["rooms"]
    assert [r["noRoots"] for r in demo if r["roomCategory"] == "cave"] == [True]


def test_the_plan_view_shows_its_caves_where_they_live(ds):
    (cave,) = ds.plan_view("level_02")["floors"][0]["caves"]
    assert cave["pointer"] == PLAN_CAVE and cave["caveId"] is None
    assert (cave["gridWidth"], cave["gridHeight"]) == (3, 4)
    assert cave["cargoPlacement"] == {"count": 6, "lootTableId": "loot_common", "cargoId": None}
    plan = ds.get("LevelBasePlanData", "level_02").value
    plan["floorDefaults"] = {"caves": plan["floors"][0].pop("caves")}
    floors = ds.plan_view("level_02")["floors"]
    assert [f["caves"][0]["pointer"] for f in floors] == ["/floorDefaults/caves/0"] * 2
