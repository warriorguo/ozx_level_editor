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


pytestmark = pytest.mark.skipif(
    not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir() or not have_node(),
    reason="needs a sibling ozx_base checkout and node/npm")


def _stamp_a_pin(api, level):
    """Give the first wave of the first floor a `guaranteedDrop` (OZX-1041).

    Writes the view the way the assigner would, so the client is exercised on
    the shape it will actually meet: a pin drawn beside the wave's table, not
    in place of it.
    """
    wave = next(e for r in level["floors"][0]["rooms"] for e in r["enemies"])
    wave["drop"]["pin"] = api.dataset.wave_pin(
        {"guaranteedDrop": {"itemId": "key_silver", "count": 1, "chance": 0.07}})


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
    levels = boot["levels"]
    chosen = next((l for l in levels if l["floors"] > 1), levels[0])
    boot["levels"] = [chosen] + [l for l in levels if l is not chosen]
    (out / "boot.json").write_text(json.dumps(boot))
    (out / "val.json").write_text(json.dumps(api.validate(None, None)))
    (out / "cfg.json").write_text(json.dumps(api.get_config()))
    level = api.level(chosen["id"])
    # No hand-authored encounter carries a pin — `guaranteedDrop` is written
    # by the assigner, so it only ever reaches this tool through a generated
    # level. The page still has to draw one, so one is stamped in here rather
    # than left to a session capture that may or may not be on this machine.
    _stamp_a_pin(api, level)
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
        # The stage-weight column is only exercised by a plan that writes the
        # weights on its floors. chapter_3 keeps them in floorDefaults, which
        # this side does not merge yet (OLE-9), so they are borrowed from
        # whichever plan does rather than leaving the column untested.
        if not any(f["stageWeights"] for f in chosen_plan["floors"]):
            donor = next((v for v in views
                          if any(f["stageWeights"] for f in v["floors"])), None)
            if donor:
                rows = next(f["stageWeights"] for f in donor["floors"]
                            if f["stageWeights"])
                chosen_plan["floors"][0]["stageWeights"] = rows
        (out / "plan.json").write_text(json.dumps(chosen_plan))
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
