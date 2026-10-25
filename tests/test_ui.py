"""Runs the browser-client tests under a real DOM.

The Python suite covers the server; this covers the page. It shells out to
`tests/ui/ui_test.mjs` under jsdom, because the client bugs that have actually
happened here were all runtime ones that `node --check` cannot see.

jsdom is the one dependency in the project and it is deliberately not a hard
one: it installs into a temp dir on first run, and the test skips cleanly when
node or the network is unavailable, so `pytest` still works offline.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ozxlevel.api import Api  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"
JSDOM_HOME = pathlib.Path(tempfile.gettempdir()) / "ozx-level-studio-jsdom"
JSDOM_ENTRY = JSDOM_HOME / "node_modules/jsdom/lib/api.js"


def have_node():
    return shutil.which("node") is not None and shutil.which("npm") is not None


def ensure_jsdom():
    """Install jsdom once into a temp dir. Returns None if it can't be had."""
    if JSDOM_ENTRY.exists():
        return JSDOM_ENTRY
    JSDOM_HOME.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["npm", "install", "--no-save", "--silent",
             "--prefix", str(JSDOM_HOME), "jsdom"],
            check=True, capture_output=True, timeout=300)
    except Exception:
        return None
    return JSDOM_ENTRY if JSDOM_ENTRY.exists() else None


SAMPLE_SEED = 7   # the seed tests/ui/ui_test.mjs types before generating

pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir() or not have_node(),
    reason="needs a sibling ozx_base checkout and node/npm")


def _with_a_skill_platform(api, level_id):
    """The level view, with a skill platform in floor 0's first room.

    Skill platforms moved to the test levels (OZX-1400), so the level chosen
    for its loot slots may have none — and the page's skill row would go
    untested in silence. The platform is put in the DOCUMENT and the view
    built by the real projection, then the document is put back.
    """
    doc = api.dataset.get("LevelData", level_id)
    room = doc.value["floors"][0]["rooms"][0]
    if any(p.get("kind") == "skill" for p in room.get("staticPlacements") or []):
        return api.level(level_id)
    before = room.get("staticPlacements")
    room["staticPlacements"] = list(before or []) + [
        {"kind": "skill", "skillId": "laser", "count": 1, "charges": 3}]
    try:
        return api.level(level_id)
    finally:
        room["staticPlacements"] = before


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """Snapshot the API payloads the page loads at boot."""
    out = tmp_path_factory.mktemp("ui-fixtures")
    api = Api(OZX_BASE)
    boot = api.bootstrap()
    # Prefer a level with more than one floor: several client behaviours only
    # exist across a floor switch (a stale selection re-aiming at another
    # room, most of all), and a single-floor fixture skips them silently.
    # It has to lead the list, because the page boots levels[0] and the
    # harness serves exactly one level view.
    # And the selection checks click into the sixth loot slot of floor 0, so
    # take the multi-floor level with the most of them rather than whichever
    # sorts first — the corpus changes underneath this test.
    levels = boot["levels"]

    def loot_slots(entry):
        rooms = api.level(entry["id"])["floors"][0]["rooms"]
        return sum(len(r["enemies"]) + len(r["cargoLoot"] or [])
                   + len(r["guaranteedDrops"]) for r in rooms)

    chosen = max((l for l in levels if l["floors"] > 1),
                 key=loot_slots, default=levels[0])
    boot["levels"] = [chosen] + [l for l in levels if l is not chosen]
    (out / "boot.json").write_text(json.dumps(boot))
    (out / "val.json").write_text(json.dumps(api.validate(None, None)))
    (out / "cfg.json").write_text(json.dumps(api.get_config()))
    level = _with_a_skill_platform(api, chosen["id"])
    (out / "lvl.json").write_text(json.dumps(level))
    # A plan too: it is a different document kind with no rooms, and the
    # client has a whole second render path for it.
    plans = boot["plans"]
    if plans:
        # Prefer one that uses the spawner channel (OZX-1032): the pool row
        # has a second line only when a floor declares one, so a plan without
        # it would skip that check in silence.
        views = [api.plan(p["id"]) for p in plans]
        chosen_plan = next(
            (v for v in views
             if any(e["isSpawner"] for f in v["floors"] for e in f["enemyPool"])),
            views[0])
        (out / "plan.json").write_text(json.dumps(chosen_plan))
        # OLE-1: the generator's status, and one real sample of that plan at
        # the seed the page will ask for. Without a built generator only the
        # status is written, and the page's sample checks stand down.
        gen = api.generator_status()
        (out / "gen.json").write_text(json.dumps(gen))
        if gen.get("available"):
            sample = api.plan_sample(chosen_plan["id"], SAMPLE_SEED)
            if not sample.get("missing"):
                (out / "sample.json").write_text(json.dumps(sample))
    return out


def test_browser_client(fixtures):
    jsdom = ensure_jsdom()
    if jsdom is None:
        pytest.skip("jsdom unavailable (offline?)")

    env = {**os.environ,
           "JSDOM_PATH": str(jsdom),
           "FIXTURES": str(fixtures)}
    result = subprocess.run(
        ["node", str(ROOT / "tests/ui/ui_test.mjs")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    assert result.returncode == 0, "browser-client checks failed (see output above)"
