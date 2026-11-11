"""A rail oil tank's launch carries no floor drop table (OLE-86 / OZX-1791).

`LevelBasePlanAssigner` stamps the floor's synthesised loot table on every
launch of a generated room's inlineEncounter; since OZX-1791 it skips a launch
with a `railOilTankId` (LevelBasePlanAssigner.cs:1250) — the tank is neutral
and drops nothing (OZX-1798), and `EncounterValidator` refuses a dropTableId
on it. chapter_5's B1 (floor 2, the basement) is the first shipped case: its
`rail_evil_factory` line rolls `rail_oil_tank`.

Generation is genlevel's; the editor's part is not to attribute loot — not
even the item_exp_small fallback — to a tank in the room view.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel import generate  # noqa: E402
from ozxlevel.api import Api  # noqa: E402
from ozxlevel.dataset import Dataset  # noqa: E402

OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
needs_data = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
    reason="sibling ozx_base checkout not found")
built = pytest.mark.skipif(
    not generate.status(OZX_BASE if OZX_BASE.is_dir() else None).get("available"),
    reason="no dotnet or no built Game.Balance.dll")
B1 = 2


@needs_data
def test_a_tank_row_drops_nothing():
    ds = Dataset(OZX_BASE)
    inline = {"id": "x", "steps": [
        {"action": {"verb": "launch", "railOilTankId": "rail_oil_tank", "min": 1, "max": 1}},
        {"action": {"verb": "launch", "enemyId": "jammer", "min": 2, "max": 2}}]}
    tank, body = ds.encounter_enemies(None, inline=inline)
    assert tank["rail"] == "tank" and tank["drop"] is None
    assert body["drop"]["source"] in ("enemy", "fallback")


@built
def test_chapter_5_b1_tanks_carry_no_floor_table():
    doc = generate.generate(OZX_BASE, "chapter_5", 1)
    tanks = stamped = 0
    for room in doc["floors"][B1]["rooms"]:
        for step in (room.get("inlineEncounter") or {}).get("steps") or []:
            action = step.get("action") or {}
            if action.get("railOilTankId"):
                tanks += 1
                assert not action.get("dropTableId"), (room["roomId"], action)
            elif action.get("verb") == "launch" and room.get("floorLootTableId"):
                assert action.get("dropTableId") == room["floorLootTableId"]
                stamped += 1
    assert tanks and stamped


@built
def test_the_sample_view_shows_no_tank_loot():
    view = Api(OZX_BASE).plan_sample("chapter_5", 1)
    rows = [e for r in view["floors"][B1]["rooms"] for e in r["enemies"]]
    assert any(e["rail"] == "tank" for e in rows)
    assert all(e["drop"] is None for e in rows if e["rail"] == "tank")
    assert all(e["drop"] is not None for e in rows if e["rail"] != "tank")
