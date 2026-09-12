"""The contract between the .app wrapper and the Python server.

The Swift side spawns `/usr/bin/python3 <Resources>/serve.py --port N
--no-open` and waits for `/health` before showing the window. Those are the
only assumptions it makes, and each is checked here — the alternative is
finding out by launching the app.

`swift-app/` itself is built with `make -C swift-app build`; nothing here
requires Swift or a built bundle.
"""

import pathlib
import plistlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SWIFT = ROOT / "swift-app"
SYSTEM_PYTHON = pathlib.Path("/usr/bin/python3")

pytestmark = pytest.mark.skipif(not SWIFT.is_dir(), reason="swift-app/ not present")


# ── the flags the wrapper passes ─────────────────────────────────────────


def test_serve_accepts_the_flags_the_wrapper_uses():
    """--port and --no-open are the wrapper's entire command line."""
    result = subprocess.run([sys.executable, "serve.py", "--help"],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert "--port" in result.stdout
    assert "--no-open" in result.stdout


def test_the_wrapper_passes_exactly_those_flags():
    """If the Swift argument list drifts, this is where it shows up."""
    src = (SWIFT / "Sources/LevelStudio/PythonServer.swift").read_text()
    args = re.search(r'p\.arguments = \[(.*?)\]', src, re.S)
    assert args, "PythonServer no longer sets process arguments"
    body = args.group(1)
    assert '"--port"' in body and '"--no-open"' in body
    assert "script.path" in body


def test_the_wrapper_probes_health_not_the_index():
    """Readiness must not wait on parsing 365 files."""
    src = (SWIFT / "Sources/LevelStudio/PythonServer.swift").read_text()
    assert 'appendingPathComponent("health")' in src
    assert "bootstrap" not in src, "readiness must not depend on the data index"


def test_health_answers_without_a_project_mounted():
    """The app starts before a folder is chosen; health still has to answer."""
    sys.path.insert(0, str(ROOT))
    from ozxlevel.api import Api
    from ozxlevel.config import Config
    api = Api(None, Config(pathlib.Path("/nonexistent/config.json")))
    # /health is handled in the request handler, not Api, so assert the
    # precondition it relies on: an unmounted Api is constructible and answers.
    assert api.get_config()["mounted"] is False


# ── the bundle layout the wrapper expects ────────────────────────────────


def test_makefile_copies_what_serve_py_needs():
    """serve.py resolves ozxlevel/ and web/ relative to itself."""
    makefile = (SWIFT / "Makefile").read_text()
    for needed in ("serve.py", "ozxlevel", "web"):
        assert needed in makefile, f"the bundle would be missing {needed}"
    assert "__pycache__" in makefile, "build caches would ship in the bundle"


def test_makefile_removes_the_destination_before_installing():
    """ditto preserves source mtime, so a stale install can look fresh."""
    makefile = (SWIFT / "Makefile").read_text()
    install = makefile[makefile.index("install:"):]
    assert "rm -rf" in install.split("ditto")[0], \
        "install must clear the destination before ditto"


def test_info_plist_is_internally_consistent():
    plist = plistlib.loads((SWIFT / "Resources/Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "LevelStudio"
    assert plist["CFBundleIdentifier"] == "com.warriorguo.ozx-level-studio"
    # The binary the Makefile builds must match what the plist launches.
    makefile = (SWIFT / "Makefile").read_text()
    assert f"EXEC_NAME   := {plist['CFBundleExecutable']}" in makefile


def test_localhost_http_is_allowed_through_app_transport_security():
    """Without this, WKWebView silently refuses to load http://127.0.0.1."""
    plist = plistlib.loads((SWIFT / "Resources/Info.plist").read_bytes())
    ats = plist["NSAppTransportSecurity"]
    assert ats["NSAllowsLocalNetworking"] is True


# ── the recalled lessons, as assertions ──────────────────────────────────


def test_no_menu_item_steals_cmd_s():
    """AppKit menus intercept their keyEquivalent before the WebView sees it.

    web/app.js binds Cmd+S itself, so registering a File ▸ Save menu item would
    silently break saving from the keyboard. (ORT recipe, lessons learned.)
    """
    for swift in (SWIFT / "Sources/LevelStudio").glob("*.swift"):
        assert 'keyEquivalent: "s"' not in swift.read_text(), \
            f"{swift.name} registers Cmd+S, which would steal it from the page"


def test_the_window_is_built_only_after_the_server_is_ready():
    """Otherwise the WebView flashes 'cannot connect' on launch."""
    src = (SWIFT / "Sources/LevelStudio/AppDelegate.swift").read_text()
    start = src.index("server.start()")
    window = src.index("MainWindowController(initialURL:")
    assert start < window, "the window is constructed before the server is up"


def test_the_interpreter_is_located_in_one_place():
    """So swapping to a bundled runtime is a single edit, per the plan."""
    src = (SWIFT / "Sources/LevelStudio/PythonServer.swift").read_text()
    assert src.count("func locateInterpreter") == 1
    assert "/usr/bin/python3" in src
    # a bundled binary, if ever shipped, takes precedence
    assert src.index('forResource: "level-studio"') < src.index('"/usr/bin/python3"')


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no /usr/bin/python3")
def test_a_free_port_is_allocated_rather_than_a_fixed_one():
    """The app must not collide with a terminal `python3 serve.py`."""
    src = (SWIFT / "Sources/LevelStudio/PythonServer.swift").read_text()
    assert "allocateFreePort" in src
    assert "sin_port = 0" in src, "port 0 is what asks the kernel for a free one"
