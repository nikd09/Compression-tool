"""
user_settings.py
================
Per-user, per-PC settings for the web app: where the two libraries live and
which one is active.

Two libraries, one switcher (no merged view):
  * "shared" -- the team's common library (a OneDrive/SharePoint/network
    folder with every material). Its location differs on every PC, so it is
    a per-user setting too; it defaults to COMPRESSION_TOOL_WORKSPACE.
  * "local"  -- the user's own folder for personal experiments.

Stored in %LOCALAPPDATA%\\CompressionTool\\settings.json (the same
per-user, never-synced folder the search index already uses), so nobody has
to edit code or set Windows variables, and it is not shared between users.
Everything here is best-effort: an unreadable or unwritable settings file
falls back to the defaults rather than stopping the app.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .persistence import default_index_root, write_json

SHARED = "shared"
LOCAL = "local"
_FILENAME = "settings.json"


def settings_path() -> Path:
    return default_index_root() / _FILENAME


# The old default: a "data" folder INSIDE the tool folder. Unsafe once the
# tool is updated by replacing its folder with a new zip -- the data would go
# with it -- so it is only kept for an install that already has data there.
_LEGACY_SHARED = Path("data")


def default_shared_path() -> str:
    env = os.environ.get("COMPRESSION_TOOL_WORKSPACE")
    if env:
        return env
    if (_LEGACY_SHARED / "Records").is_dir():
        return str(_LEGACY_SHARED.resolve())
    return str(Path.home() / "CompressLab shared library")


def default_local_path() -> str:
    return str(Path.home() / "CompressLab data")


def load() -> dict[str, Any]:
    """Saved settings merged over the defaults; never raises."""
    out: dict[str, Any] = {
        "shared_path": default_shared_path(),
        "local_path": default_local_path(),
        "active": SHARED,
    }
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return out
    if isinstance(saved, dict):
        for key in ("shared_path", "local_path"):
            value = saved.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
        if saved.get("active") in (SHARED, LOCAL):
            out["active"] = saved["active"]
    return out


def save(settings: dict[str, Any]) -> bool:
    """Persist; returns False (instead of raising) when the folder cannot be
    written, so a locked-down PC just forgets the choice next launch."""
    try:
        write_json(
            {k: settings[k] for k in ("shared_path", "local_path", "active")},
            settings_path(),
        )
    except (OSError, KeyError):
        return False
    return True
