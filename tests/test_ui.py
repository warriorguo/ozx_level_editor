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


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """Snapshot the API payloads the page loads at boot."""
    out = tmp_path_factory.mktemp("ui-fixtures")
    api = Api(OZX_BASE)
    (out / "boot.json").write_text(json.dumps(api.bootstrap()))
    (out / "val.json").write_text(json.dumps(api.validate(None, None)))
    level_id = api.bootstrap()["levels"][0]["id"]
    (out / "lvl.json").write_text(json.dumps(api.level(level_id)))
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
