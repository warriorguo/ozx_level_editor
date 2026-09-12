"""The .app spawns macOS's stock /usr/bin/python3, so the code must run on it.

That interpreter is 3.9 on current macOS. Bundling our own would cost ~20MB and
a code-signing problem, and we chose not to — which makes 3.9 compatibility a
shipping constraint rather than a nicety. This test is what keeps it honest:
a 3.10+ syntax feature that slips into the source breaks the app, not the
developer's terminal, and would otherwise be found by a user.

Skips cleanly where /usr/bin/python3 is absent or is not older than the
interpreter running the suite (a Linux CI box, say) — there it proves nothing.
"""

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYSTEM_PYTHON = pathlib.Path("/usr/bin/python3")
OZX_BASE = pathlib.Path.home() / "Codes/github.com/warriorguo/ozx_base"


def system_python_version():
    if not SYSTEM_PYTHON.exists():
        return None
    try:
        out = subprocess.run([str(SYSTEM_PYTHON), "-c",
                              "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    return out.stdout.strip() or None


VERSION = system_python_version()
pytestmark = pytest.mark.skipif(
    VERSION is None,
    reason="no /usr/bin/python3 to check against")


def run_on_system_python(code: str, timeout: int = 120):
    return subprocess.run([str(SYSTEM_PYTHON), "-c", code],
                          cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)


def test_every_module_imports():
    """Catches 3.10+ syntax anywhere in the package, not just on a hot path."""
    result = run_on_system_python(
        "import sys; sys.path.insert(0, '.');"
        "import ozxlevel.jsonspan, ozxlevel.dataset, ozxlevel.validate,"
        " ozxlevel.config, ozxlevel.api;"
        "print('ok')")
    assert result.returncode == 0, (
        f"the package does not import on Python {VERSION}, which is what the "
        f".app runs:\n{result.stderr}")
    assert "ok" in result.stdout


def test_serve_py_parses_and_shows_help():
    """The entry point the app actually launches."""
    result = subprocess.run([str(SYSTEM_PYTHON), "serve.py", "--help"],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout and "--no-open" in result.stdout


@pytest.mark.skipif(not (OZX_BASE / "Assets/StreamingAssets/GameData").is_dir(),
                    reason="sibling ozx_base checkout not found")
def test_it_indexes_and_validates_real_data():
    """Importing is not enough — the work has to run there too."""
    result = run_on_system_python(
        "import sys, pathlib; sys.path.insert(0, '.');"
        "from ozxlevel.dataset import Dataset;"
        "from ozxlevel.validate import validate_all;"
        f"ds = Dataset(pathlib.Path({str(OZX_BASE)!r}));"
        "v = ds.level_view('chapter_1');"
        "print(len(ds.docs), len(v['floors'][0]['rooms']),"
        " v['floors'][0]['layout']['positions']['f0_room_0_0']['x'],"
        " len(validate_all(ds)))")
    assert result.returncode == 0, result.stderr
    docs, rooms, origin_x, _issues = result.stdout.split()
    assert int(docs) > 300
    assert int(rooms) == 12
    assert int(origin_x) == 0        # the layout BFS agrees with the dev run


def test_the_runtime_assumption_is_recorded():
    """If this ever fails, the .app's spawn target needs revisiting."""
    major, minor = (int(p) for p in VERSION.split("."))
    assert (major, minor) >= (3, 9), (
        f"/usr/bin/python3 is {VERSION}; the app assumes 3.9+")
    if (major, minor) > tuple(sys.version_info[:2]):
        pytest.skip("system python is newer than the dev one — nothing to prove")
