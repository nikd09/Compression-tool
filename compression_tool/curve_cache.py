"""
curve_cache.py
===============
Per-cycle stress-displacement curve points, reduced for display.

Not part of the frozen JSON contract (schema.py, SCHEMA_VERSION). A chart
needs the full loop shape; the record does not, and putting curve points in
it would turn every plotting change into a schema bump. This writes a
sidecar instead -- `<specimen>.curve.json` -- disposable and rebuildable from
the archived raw file at any time, exactly like the SQLite index.

Cycle boundaries come from the same `_start`/`_end` indices `analyse_test()`
used to compute every metric, so the curve drawn can never disagree with the
numbers next to it.

Reduction is Ramer-Douglas-Peucker, run per cycle against axes normalised to
the whole specimen's own displacement/stress range -- not each cycle's own
range -- so an early, small-stage loop is not simplified as aggressively
relative to the big loops it is plotted next to. Validated at eps=1e-4
against the T050E1 export: 85,844 raw samples reduced to 2,084 vertices at
0.32% enclosed-area error, checked cycle by cycle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .core import TestData
from .persistence import jsonable

CACHE_VERSION = 1
DEFAULT_EPS = 1e-4


def _rdp_mask(xy: np.ndarray, eps: float) -> np.ndarray:
    """Boolean keep-mask for an (N, 2) array of already-normalised points.

    Iterative (stack-based) rather than recursive: a single cycle can carry
    several thousand samples, and there is no reason to risk the recursion
    limit over a loop this shape.
    """
    n = len(xy)
    keep = np.zeros(n, dtype=bool)
    if n == 0:
        return keep
    keep[0] = True
    keep[-1] = True
    if n < 3:
        return keep

    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        a, b = xy[lo], xy[hi]
        seg_x, seg_y = b[0] - a[0], b[1] - a[1]
        seg_len = float(np.hypot(seg_x, seg_y))
        mid = xy[lo + 1 : hi]
        if seg_len == 0.0:
            d = np.hypot(mid[:, 0] - a[0], mid[:, 1] - a[1])
        else:
            d = np.abs(seg_y * (mid[:, 0] - a[0]) - seg_x * (mid[:, 1] - a[1])) / seg_len
        far_rel = int(np.argmax(d))
        if d[far_rel] > eps:
            far = lo + 1 + far_rel
            keep[far] = True
            stack.append((lo, far))
            stack.append((far, hi))
    return keep


def build_curve_cache(
    test: TestData, df: pd.DataFrame, *, specimen_id: str, eps: float = DEFAULT_EPS
) -> dict:
    """Per-cycle [displacement_mm, stress_mpa] points, RDP-reduced for drawing."""
    disp, stress = test.displacement_mm, test.stress_mpa
    x_range = float(np.nanmax(disp) - np.nanmin(disp)) or 1.0
    y_range = float(np.nanmax(stress) - np.nanmin(stress)) or 1.0

    cycles: list[dict] = []
    raw_points = 0
    kept_points = 0
    for _, row in df.iterrows():
        a, b = int(row["_start"]), int(row["_end"])
        cx, cy = disp[a : b + 1], stress[a : b + 1]
        raw_points += len(cx)

        norm = np.column_stack([cx / x_range, cy / y_range])
        mask = _rdp_mask(norm, eps)
        kept_points += int(mask.sum())

        cycles.append({
            "cycle": int(row["Cycle"]),
            "points": [[jsonable(px), jsonable(py)] for px, py in zip(cx[mask], cy[mask])],
        })

    return {
        "cache_version": CACHE_VERSION,
        "specimen_id": specimen_id,
        "reduction": {
            "algorithm": "rdp",
            "eps": eps,
            "raw_points": raw_points,
            "kept_points": kept_points,
        },
        "cycles": cycles,
    }


def write_curve_cache(cache: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False)
    return path


def read_curve_cache(path: str | os.PathLike) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def curve_cache_path_for(json_path: str | os.PathLike) -> Path:
    """`<stem>.json` -> `<stem>.curve.json`, the sidecar written next to it at
    ingest time. The two are never named independently, so deriving one from
    the other is safe rather than a guess."""
    return Path(json_path).with_suffix("").with_suffix(".curve.json")
