"""A rail-bound enemy's railConfig (OLE-73 / OZX-1693, OZX-1727).

`RailTrainComposition.Validate` (Game.Room/Tilemap/RailTrain.cs) checks every
car of a train as the train spawns: the chassis numbers (`ValidateChassis`),
the junction policy ("circuit" / "random" / OZX-1693's "chase"), and, for a
chaser, `chaseTurnMargin > 0` and `chaseTurnCooldown >= 0`. OZX-1727 added a
signed `axleOffset` with |axleOffset| <= bodyLength/2. The segmented rail worm
(rail_worm_head / body / tail) is the first shipped user of all of them.
"""

import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402
from ozxlevel.validate import validate_all, validate_enemy  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")

CODE = "ENEMY_RAIL_CONFIG_INVALID"
WORM = ("rail_worm_head", "rail_worm_body", "rail_worm_tail")


@pytest.fixture()
def ds():
    return Dataset(OZX_BASE)


def found(issues):
    return [(i["entity"], i["pointer"]) for i in issues if i["code"] == CODE]


def rail(ds, enemy_id):
    return ds.get("EnemyData", enemy_id).value["railConfig"]


def test_shipped_rail_cars_validate_clean(ds):
    assert found(validate_all(ds)) == []
    for enemy_id in WORM:
        assert rail(ds, enemy_id)["junctionPolicy"] == "chase"


def test_a_chaser_needs_a_turn_margin(ds):
    del rail(ds, "rail_worm_head")["chaseTurnMargin"]
    assert found(validate_enemy(ds, "rail_worm_head")) == [("rail_worm_head", "/railConfig")]
    rail(ds, "rail_worm_head")["chaseTurnMargin"] = 0
    assert found(validate_enemy(ds, "rail_worm_head")) == [
        ("rail_worm_head", "/railConfig/chaseTurnMargin")]


def test_a_negative_cooldown_is_refused_and_zero_is_fine(ds):
    rail(ds, "rail_worm_body")["chaseTurnCooldown"] = -1
    assert found(validate_enemy(ds, "rail_worm_body")) == [
        ("rail_worm_body", "/railConfig/chaseTurnCooldown")]
    rail(ds, "rail_worm_body")["chaseTurnCooldown"] = 0
    assert found(validate_enemy(ds, "rail_worm_body")) == []


def test_chase_numbers_are_read_only_by_a_chaser(ds):
    config = rail(ds, "rail_worm_tail")
    config["junctionPolicy"] = "circuit"
    config["chaseTurnMargin"] = 0
    config["chaseTurnCooldown"] = -5
    assert found(validate_enemy(ds, "rail_worm_tail")) == []


def test_an_unknown_policy_is_refused(ds):
    rail(ds, "rail_cyclops")["junctionPolicy"] = "hunt"
    assert found(validate_enemy(ds, "rail_cyclops")) == [
        ("rail_cyclops", "/railConfig/junctionPolicy")]
    rail(ds, "rail_cyclops")["junctionPolicy"] = ""
    assert found(validate_enemy(ds, "rail_cyclops")) == []


def test_the_axles_stay_on_the_body(ds):
    config = rail(ds, "rail_worm_head")
    half = config["bodyLength"] / 2
    for offset in (half, -half):
        config["axleOffset"] = offset
        assert found(validate_enemy(ds, "rail_worm_head")) == []
    config["axleOffset"] = -(half + 0.01)
    assert found(validate_enemy(ds, "rail_worm_head")) == [
        ("rail_worm_head", "/railConfig/axleOffset")]


def test_the_chassis_numbers(ds):
    config = rail(ds, "rail_heavy")
    config.update(speed=0, turnSpeedMultiplier=1.5, keepOut=-1, wheelBase=0)
    assert sorted(found(validate_enemy(ds, "rail_heavy"))) == sorted(
        ("rail_heavy", f"/railConfig/{k}")
        for k in ("speed", "turnSpeedMultiplier", "keepOut", "wheelBase"))


def test_a_disabled_rail_config_is_not_read(ds):
    config = rail(ds, "rail_heavy")
    config.update(enabled=False, speed=0, junctionPolicy="hunt")
    assert found(validate_enemy(ds, "rail_heavy")) == []


def test_the_picker_names_the_policy(ds):
    meta = {e["id"]: e["meta"] for e in ds.enemy_catalog()}
    assert "rail car · chase" in meta["rail_worm_head"]
    assert "axles -0.3u off centre" in meta["rail_worm_head"]
    assert "rail car · circuit" in meta["rail_cyclops"]


def test_the_worm_round_trips_with_its_new_fields(tmp_path):
    root = tmp_path / "ozx"
    shutil.copytree(OZX_BASE / "Assets/StreamingAssets/GameData/enemies",
                    root / "Assets/StreamingAssets/GameData/enemies")
    api = Api(root)
    for enemy_id in WORM:
        doc = api.dataset.get("EnemyData", enemy_id)
        before = doc.path.read_bytes()
        config = dict(doc.value["railConfig"])
        api.edit("EnemyData", enemy_id,
                 [{"op": "set", "path": "/railConfig/chaseTurnMargin", "value": 3.5},
                  {"op": "set", "path": "/railConfig/junctionPolicy", "value": "random"}])
        after = api.dataset.get("EnemyData", enemy_id).value["railConfig"]
        assert after["chaseTurnMargin"] == 3.5 and after["junctionPolicy"] == "random"
        assert {k: v for k, v in after.items() if k not in ("chaseTurnMargin", "junctionPolicy")} \
            == {k: v for k, v in config.items() if k not in ("chaseTurnMargin", "junctionPolicy")}
        api.edit("EnemyData", enemy_id,
                 [{"op": "set", "path": "/railConfig/chaseTurnMargin",
                   "value": config["chaseTurnMargin"]},
                  {"op": "set", "path": "/railConfig/junctionPolicy", "value": "chase"}])
        assert doc.path.read_bytes() == before
