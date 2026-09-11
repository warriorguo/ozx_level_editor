#!/usr/bin/env python3
"""Run OZX Level Studio.

    python3 serve.py                 # autodetect the sibling ozx_base
    python3 serve.py --root PATH     # point at a checkout explicitly
    python3 serve.py --port 9000
    python3 serve.py --no-open       # don't launch a browser

Python 3.11+, standard library only. Binds to loopback.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ozxlevel.api import serve  # noqa: E402

DEFAULT_PORT = 8765


def find_project_root(explicit: str | None) -> pathlib.Path:
    if explicit:
        root = pathlib.Path(explicit).expanduser().resolve()
        if not (root / "Assets/StreamingAssets/GameData").is_dir():
            sys.exit(f"error: no Assets/StreamingAssets/GameData under {root}")
        return root

    here = pathlib.Path(__file__).resolve().parent
    for base in (here, *here.parents):
        candidate = base.parent / "ozx_base"
        if (candidate / "Assets/StreamingAssets/GameData").is_dir():
            return candidate.resolve()
    sys.exit("error: could not find a sibling ozx_base checkout; pass --root PATH")


def main() -> None:
    parser = argparse.ArgumentParser(description="OZX Level Studio")
    parser.add_argument("--root", help="path to the ozx_base checkout")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser window")
    args = parser.parse_args()

    root = find_project_root(args.root)
    server, api = serve(root, args.port)
    counts = api.bootstrap()["counts"]
    url = f"http://127.0.0.1:{args.port}/"

    print(f"OZX Level Studio")
    print(f"  project   {root}")
    print(f"  indexed   {counts['documents']} documents — "
          f"{counts['levels']} levels, {counts['plans']} plans, "
          f"{counts['encounters']} encounters, {counts['enemies']} enemies, "
          f"{counts['lootTables']} loot tables")
    if api.dataset.problems:
        print(f"  PROBLEMS  {len(api.dataset.problems)} at load — see the Problems panel")
    print(f"  serving   {url}")
    print("  Ctrl+C to stop")

    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        server.shutdown()


if __name__ == "__main__":
    main()
