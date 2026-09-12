#!/usr/bin/env python3
"""Run OZX Level Studio.

    python3 serve.py                 # use the saved folder, or a sibling ozx_base
    python3 serve.py --root PATH     # point at a checkout explicitly
    python3 serve.py --port 9000
    python3 serve.py --no-open       # don't launch a browser

Standard library only. Binds to loopback.

Runs on macOS's stock /usr/bin/python3 (3.9) as well as newer ones, because the
bundled .app spawns the system interpreter rather than shipping its own — see
tests/test_python39.py, which holds that guarantee.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ozxlevel.api import serve  # noqa: E402
from ozxlevel.config import (Config, discover_project,  # noqa: E402
                             looks_like_project)


def find_project_root(explicit: str | None,
                      config: Config) -> tuple[pathlib.Path | None, str]:
    """Resolve the folder to mount, and say where the answer came from.

    Order: --root, then the saved config, then a sibling checkout. Returning
    None is a legitimate outcome — a double-clicked app has no command line, so
    it starts with nothing mounted and lets the user pick. Only an explicitly
    bad --root is fatal, because that one is a typo worth reporting.
    """
    if explicit:
        ok, reason = looks_like_project(explicit)
        if not ok:
            sys.exit(f"error: {reason}")
        return pathlib.Path(explicit).expanduser().resolve(), "--root"

    if config.project_root:
        ok, _ = looks_like_project(config.project_root)
        if ok:
            return pathlib.Path(config.project_root).expanduser().resolve(), "config"

    # Nothing configured: look beside this file first (the checkout layout),
    # then the conventional code roots. An installed .app has no sibling repo,
    # so without this every fresh install opens into the folder picker with
    # the checkout sitting somewhere obvious.
    found = discover_project(near=pathlib.Path(__file__).resolve().parent)
    if found:
        return found, "discovered"

    return None, "nothing found"


def main() -> None:
    parser = argparse.ArgumentParser(description="OZX Level Studio")
    parser.add_argument("--root", help="path to the ozx_base checkout")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--config", help="path to the config file")
    args = parser.parse_args()

    config = Config.load(pathlib.Path(args.config) if args.config else None)
    root, source = find_project_root(args.root, config)
    port = args.port or config.port

    # Remember an explicitly given folder so the app finds it next launch.
    if root and str(root) != config.project_root:
        config.project_root = str(root)
        try:
            config.save()
        except OSError:
            pass  # not being able to persist must not stop the editor

    server, api = serve(root, port, config)
    url = f"http://127.0.0.1:{port}/"

    print("OZX Level Studio")
    if root:
        counts = api.bootstrap()["counts"]
        print(f"  project   {root}  ({source})")
        print(f"  indexed   {counts['documents']} documents — "
              f"{counts['levels']} levels, {counts['plans']} plans, "
              f"{counts['encounters']} encounters, {counts['enemies']} enemies, "
              f"{counts['lootTables']} loot tables")
        if api.dataset.problems:
            print(f"  PROBLEMS  {len(api.dataset.problems)} at load — "
                  f"see the Problems panel")
    else:
        print("  project   none set — pick your ozx_base checkout in the window")
    print(f"  serving   {url}")
    print("  Ctrl+C to stop")

    if not args.no_open and config.auto_open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        server.shutdown()


if __name__ == "__main__":
    main()
