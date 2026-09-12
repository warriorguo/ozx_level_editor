"""User config: which project folder is mounted, and how to serve it.

Running from a terminal you can pass ``--root``; a double-clicked app has no
command line, so the folder has to persist somewhere and be changeable from the
UI. This is that somewhere.

XDG-style placement (``~/.config/ozx-level-studio/config.json``) rather than
``~/Library`` so it stays easy to inspect and edit by hand.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

APP_ID = "ozx-level-studio"
DEFAULT_PORT = 8765

# Every mounted project must contain this, or there is nothing to edit.
GAME_DATA_REL = "Assets/StreamingAssets/GameData"


def default_path() -> pathlib.Path:
    """``~/.config/ozx-level-studio/config.json``, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
    return root / APP_ID / "config.json"


class Config:
    """The persisted settings, and nothing derived from them."""

    __slots__ = ("project_root", "port", "auto_open_browser", "path")

    def __init__(self, path: pathlib.Path | None = None, **values: Any):
        self.path = path or default_path()
        self.project_root: str = values.get("project_root") or ""
        self.port: int = int(values.get("port") or DEFAULT_PORT)
        self.auto_open_browser: bool = bool(
            values.get("auto_open_browser", True))

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> "Config":
        """Read the config, returning defaults when it does not exist yet.

        A corrupt file is treated as absent rather than fatal — the app has to
        start so the user can fix the folder through the UI.
        """
        path = path or default_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, json.JSONDecodeError):
            data = {}
        return cls(path, **data)

    def save(self) -> None:
        """Write atomically, the same way document saves do."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "project_root": self.project_root,
            "port": self.port,
            "auto_open_browser": self.auto_open_browser,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def resolve(self) -> "ResolvedConfig":
        return ResolvedConfig.of(self)


class ResolvedConfig:
    """What the UI needs to show: the folder, and why it is or isn't usable.

    Kept separate from :class:`Config` so the stored settings never carry
    computed state, and so the reason a folder was rejected can be phrased for
    a human rather than inferred from an empty string.
    """

    __slots__ = ("project_root", "game_data_dir", "exists", "usable",
                 "reason", "config_path", "port", "auto_open_browser")

    def __init__(self, **kw: Any):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))

    @classmethod
    def of(cls, config: Config) -> "ResolvedConfig":
        common = {
            "config_path": str(config.path),
            "port": config.port,
            "auto_open_browser": config.auto_open_browser,
        }
        if not config.project_root.strip():
            return cls(project_root="", game_data_dir="", exists=False,
                       usable=False, reason="no project folder set yet",
                       **common)

        root = pathlib.Path(config.project_root).expanduser()
        if not root.is_dir():
            return cls(project_root=str(root), game_data_dir="", exists=False,
                       usable=False, reason=f"{root} does not exist", **common)

        game_data = root / GAME_DATA_REL
        if not game_data.is_dir():
            return cls(project_root=str(root), game_data_dir=str(game_data),
                       exists=True, usable=False,
                       reason=f"no {GAME_DATA_REL} under this folder — pick the "
                              f"ozx_base checkout itself, not a folder above or "
                              f"inside it",
                       **common)

        return cls(project_root=str(root), game_data_dir=str(game_data),
                   exists=True, usable=True, reason=None, **common)

    def as_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}


def looks_like_project(candidate: str | os.PathLike) -> tuple[bool, str]:
    """Validate a folder before mounting it. Returns (ok, reason)."""
    root = pathlib.Path(candidate).expanduser()
    if not str(root).strip():
        return False, "no folder given"
    if not root.is_dir():
        return False, f"{root} does not exist"
    if not (root / GAME_DATA_REL).is_dir():
        return False, f"no {GAME_DATA_REL} under {root}"
    return True, ""
