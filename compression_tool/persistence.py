"""
persistence.py
==============
On-disk layout and the JSON record that is the system's source of truth.

    <root>/
      raw_input/                     immutable copies of the original exports
        <sha12>_<original name>.xlsx
      processed_output/
        <material>_<YYYY-MM-DD>/
          run.json                   what was ingested, with which config
          <specimen>.json            the record -- source of truth
          <specimen>.csv             per-cycle table, flat
          <specimen>.xlsx            per-cycle table + summary
          <specimen>.html            standalone report
          <material>_<date>.xlsx     all specimens of the run in one workbook
      knowledge_base.db              SQLite index, rebuildable from the JSONs

The JSON records are authoritative. The database is a queryable index over
them and may be deleted and rebuilt at any time; nothing is stored there that
cannot be recovered from disk.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from . import diagnostics
from .core import Config, TestData
from .schema import SCHEMA_VERSION

RAW_DIRNAME = "raw_input"
PROCESSED_DIRNAME = "processed_output"
DB_FILENAME = "knowledge_base.db"


# ----------------------------------------------------------------------------
# Workspace
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Workspace:
    """The three directories the tool owns. Nothing is written outside them."""

    root: Path

    @classmethod
    def at(cls, root: str | os.PathLike) -> "Workspace":
        return cls(Path(root).expanduser().resolve())

    @property
    def raw(self) -> Path:
        return self.root / RAW_DIRNAME

    @property
    def processed(self) -> Path:
        return self.root / PROCESSED_DIRNAME

    @property
    def db_path(self) -> Path:
        return self.root / DB_FILENAME

    def ensure(self) -> "Workspace":
        self.raw.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        return self

    def relative(self, path: Path) -> str:
        """Store paths relative to the workspace so a run folder stays valid
        when the whole tree is moved or shared."""
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(Path(path).resolve())


# ----------------------------------------------------------------------------
# Hashing and naming
# ----------------------------------------------------------------------------


def sha256_file(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def config_hash(cfg: Config) -> str:
    payload = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def specimen_id(source_sha256: str, label: str) -> str:
    """Deterministic, so a rebuilt database reuses the same identifiers and
    references held elsewhere do not rot."""
    return hashlib.sha256(f"{source_sha256}:{label}".encode()).hexdigest()[:16]


_SLUG_STRIP = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str, fallback: str = "unnamed") -> str:
    """Filesystem-safe name that still reads like the original."""
    cleaned = _SLUG_STRIP.sub("-", str(text).strip()).strip("-._")
    return cleaned or fallback


# ----------------------------------------------------------------------------
# raw_input -- immutable archive
# ----------------------------------------------------------------------------


def archive_raw(source: str | os.PathLike, ws: Workspace) -> tuple[Path, str]:
    """Copy an export into raw_input/ and return (archived path, sha256).

    The copy is content-addressed and marked read-only. Re-ingesting the same
    file is a no-op, so an export can be fed in repeatedly without ever
    duplicating or -- more importantly -- silently replacing the archived
    original that every downstream record points at.
    """
    src = Path(source).expanduser().resolve()
    digest = sha256_file(src)
    target = ws.raw / f"{digest[:12]}_{slugify(src.name)}"

    if target.exists():
        # Content-addressed: an existing file with this name has this content.
        return target, digest

    ws.raw.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")
    shutil.copy2(src, tmp)
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o444)
    except OSError:
        # Read-only is a guard rail, not a requirement; some filesystems refuse.
        pass
    return target, digest


# ----------------------------------------------------------------------------
# JSON-safe conversion
# ----------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """Convert numpy / pandas scalars to plain Python, and every flavour of
    missing value to None.

    JSON has no NaN, and a NaN that survives into a record would come back from
    a rebuild as the string 'NaN' and quietly poison later arithmetic. Missing
    is missing: it is written as null and read back as None.
    """
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, (np.ndarray,)):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (np.str_, str)):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def cycle_records(df: pd.DataFrame) -> list[dict]:
    """Per-cycle rows as plain dicts, missing values as None."""
    return [{str(k): jsonable(v) for k, v in row.items()} for row in df.to_dict("records")]


# ----------------------------------------------------------------------------
# The specimen record
# ----------------------------------------------------------------------------


def build_payload(
    test: TestData,
    df: pd.DataFrame,
    cfg: Config,
    *,
    material: str,
    raw_path: Optional[Path],
    source_sha256: str,
    workspace: Optional[Workspace] = None,
    gauge_length_confirmed: bool = False,
) -> dict:
    """Assemble everything needed to reproduce and index one specimen's result.

    Deliberately self-contained: metadata, the exact config used, the source
    file's identity and every per-cycle metric. A record plus the archived raw
    file is enough to re-run the analysis and get the same numbers.
    """
    attrs = df.attrs if not df.empty else {}
    rel_raw = workspace.relative(raw_path) if (workspace and raw_path) else (
        str(raw_path) if raw_path else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "specimen": {
            "specimen_id": specimen_id(source_sha256, test.label),
            "label": test.label,
            "material": material,
            "source_file": str(test.source_file),
            "source_format": test.source_format,
            "source_sha256": source_sha256,
            "raw_input_path": rel_raw,
            "displacement_channel": test.displacement_channel,
            "h0_mm": jsonable(test.h0_mm),
            "d0_mm": jsonable(test.d0_mm),
            "temperature_c": jsonable(test.temperature_c),
            "n_points": int(len(test.stress_mpa)),
            "notes": list(test.notes),
        },
        "analysis": {
            "n_cycles": int(len(df)),
            "global_peak_mpa": jsonable(attrs.get("global_peak_mpa")),
            "multi_stage": bool(attrs.get("multi_stage", False)),
            "ref_stress_mpa": jsonable(attrs.get("ref_stress_mpa")),
            "residual_stress_mpa": jsonable(attrs.get("residual_stress_mpa")),
            "h0_mm": jsonable(attrs.get("h0_mm")),
            "has_strain": bool(attrs.get("h0_mm")),
            "notes": list(attrs.get("notes", [])),
            # What the strain columns were divided by, and whether anyone has
            # checked it. Travels with the record so a stored result can never
            # be read as validated strain when nobody confirmed the gauge length.
            "strain_basis": diagnostics.strain_basis(
                test, gauge_length_confirmed=gauge_length_confirmed
            ),
            # Conditions that change how these numbers should be read.
            "warnings": diagnostics.collect(
                test, df, cfg, gauge_length_confirmed=gauge_length_confirmed
            ),
        },
        "config": {k: jsonable(v) for k, v in asdict(cfg).items()},
        "cycles": cycle_records(df),
    }


def write_json(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def read_json(path: str | os.PathLike) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def payload_frame(payload: dict) -> pd.DataFrame:
    """Per-cycle table from a record, with column order restored from the spec."""
    from .schema import CYCLE_COLUMNS

    df = pd.DataFrame(payload.get("cycles", []))
    if df.empty:
        return df
    ordered = [c.key for c in CYCLE_COLUMNS if c.key in df.columns]
    extra = [c for c in df.columns if c not in ordered]
    return df[ordered + extra]


# ----------------------------------------------------------------------------
# Run directories
# ----------------------------------------------------------------------------


def run_dir_name(material: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    return f"{slugify(material)}_{when.strftime('%Y-%m-%d')}"


def resolve_run_dir(
    ws: Workspace, material: str, fingerprint: str, when: Optional[datetime] = None
) -> Path:
    """Pick the output folder for a run.

    Re-analysing the same sources with the same config on the same day is a
    re-run and overwrites in place, which keeps the tree from filling with
    identical folders. Anything else -- different files, different config --
    gets its own suffixed folder, so a changed result never silently displaces
    the earlier one it should be compared against.
    """
    base = run_dir_name(material, when)
    candidate = ws.processed / base
    index = 1
    while candidate.exists():
        manifest = candidate / "run.json"
        if manifest.exists():
            try:
                if read_json(manifest).get("fingerprint") == fingerprint:
                    return candidate
            except (json.JSONDecodeError, OSError):
                pass
        index += 1
        candidate = ws.processed / f"{base}-{index:03d}"
    return candidate


def run_fingerprint(source_hashes: Iterable[str], cfg: Config) -> str:
    joined = ",".join(sorted(source_hashes))
    return hashlib.sha256(f"{joined}|{config_hash(cfg)}".encode()).hexdigest()[:16]


def write_manifest(
    run_dir: Path,
    *,
    material: str,
    cfg: Config,
    fingerprint: str,
    sources: list[dict],
    specimens: list[dict],
) -> Path:
    """Index of one ingest run: what went in, under which settings, what came out."""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "material": material,
        "fingerprint": fingerprint,
        "config": {k: jsonable(v) for k, v in asdict(cfg).items()},
        "sources": sources,
        "specimens": specimens,
    }
    return write_json(manifest, run_dir / "run.json")


def iter_specimen_jsons(ws: Workspace) -> list[Path]:
    """Every specimen record under processed_output, run manifests excluded."""
    if not ws.processed.exists():
        return []
    return sorted(p for p in ws.processed.glob("*/*.json") if p.name != "run.json")
