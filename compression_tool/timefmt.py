"""Readable local times for people. Records keep their exact UTC timestamps;
only what is shown on screen and in the workbooks is converted."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def local_time(value: Any, *, date_only: bool = False) -> str:
    """'2026-11-26T10:04:21+00:00' -> '26.11.2026 11:04' in this computer's
    time zone. Anything that is not a timestamp is returned unchanged."""
    if value is None:
        return "-"
    text = str(value)
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone()
    return stamp.strftime("%d.%m.%Y" if date_only else "%d.%m.%Y %H:%M")
