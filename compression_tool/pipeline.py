"""
pipeline.py
===========
The one call that turns a pile of exports into archived inputs, records,
reports and an updated index.

    result = ingest(["Mehrstufiger.xlsx"], workspace="./data", material="PEEK-GF30")

Order matters and is not arbitrary: the raw file is archived BEFORE anything is
analysed, so an export that later turns out to crash the engine is still
preserved; and the database is updated LAST, from the records that were
actually written, so the index can never claim a result that is not on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import curve_cache, diagnostics, excel_export, html_report, knowledge_base
from .core import Config, analyse_test, load_tests
from .material_export import export_material
from .persistence import (
    Workspace,
    archive_raw,
    build_payload,
    resolve_run_dir,
    run_fingerprint,
    sha256_file,
    slugify,
    write_json,
    write_manifest,
)


@dataclass
class SpecimenResult:
    label: str
    specimen_id: str
    n_cycles: int
    payload: dict
    json_path: Path
    csv_path: Optional[Path]
    xlsx_path: Optional[Path]
    html_path: Optional[Path]
    curve_path: Path


@dataclass
class IngestResult:
    material: str
    run_dir: Path
    workspace: Workspace
    specimens: list[SpecimenResult] = field(default_factory=list)
    run_xlsx: Optional[Path] = None
    run_html: Optional[Path] = None
    material_xlsx: Optional[Path] = None
    material_html: Optional[Path] = None
    indexed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def payloads(self) -> list[dict]:
        return [s.payload for s in self.specimens]

    def summary(self) -> str:
        lines = [f"Material : {self.material}", f"Run      : {self.run_dir}"]
        for s in self.specimens:
            lines.append(f"  {s.label}: {s.n_cycles} cycles -> {s.json_path.name}")
        for name, why in self.skipped:
            lines.append(f"  SKIPPED {name}: {why}")
        if self.run_xlsx:
            lines.append(f"Workbook : {self.run_xlsx}")
        if self.run_html:
            lines.append(f"Report   : {self.run_html}")
        lines.append(f"Indexed  : {self.indexed} specimen(s)")
        if self.material_xlsx:
            lines.append(f"Material workbook (all runs) : {self.material_xlsx}")
        if self.material_html:
            lines.append(f"Material dashboard (all runs) : {self.material_html}")
        return "\n".join(lines)


def preview(
    paths: Sequence[str | os.PathLike],
    cfg: Optional[Config] = None,
    *,
    gauge_length_confirmed: bool = False,
) -> list[dict]:
    """Load and analyse without writing anything.

    Exists for the Ingest screen: the user should see the detected format,
    cycle count and hold count and confirm they look right BEFORE any of it is
    committed to the archive.
    """
    cfg = cfg or Config()
    out: list[dict] = []
    for raw_path in paths:
        path = str(Path(raw_path).expanduser())
        try:
            tests = load_tests(path, cfg)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            out.append({"source_file": path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for test in tests:
            df = analyse_test(test, cfg)
            out.append({
                "source_file": path,
                "label": test.label,
                "source_format": test.source_format,
                "displacement_channel": test.displacement_channel,
                "h0_mm": test.h0_mm,
                "n_points": int(len(test.stress_mpa)),
                "n_cycles": int(len(df)),
                "n_holds": int(df["HoldDetected"].sum()) if not df.empty else 0,
                "global_peak_mpa": df.attrs.get("global_peak_mpa") if not df.empty else None,
                "multi_stage": bool(df.attrs.get("multi_stage", False)) if not df.empty else False,
                "notes": list(test.notes),
                # Shown on the Ingest screen so threshold and gauge-length
                # problems surface BEFORE anything is committed to the archive.
                "warnings": diagnostics.collect(
                    test, df, cfg, gauge_length_confirmed=gauge_length_confirmed
                ),
            })
    return out


def ingest(
    paths: Iterable[str | os.PathLike],
    workspace: str | os.PathLike | Workspace,
    *,
    material: Optional[str] = None,
    cfg: Optional[Config] = None,
    when: Optional[datetime] = None,
    update_index: bool = True,
    gauge_length_confirmed: bool = False,
    archive_originals: bool = True,
    write_reports: bool = True,
) -> IngestResult:
    """Archive, analyse, persist and index one or more exports.

    `gauge_length_confirmed` asserts that the displacement channel spans only
    the specimen height h0. It defaults to False, which marks every strain and
    modulus figure provisional: nothing in an export proves what the
    extensometer was clamped across, so the tool will not assume it.

    `archive_originals=False` skips copying the export into raw_input/ --
    only its SHA-256 is recorded (still needed for the specimen ID and to
    detect a re-ingest of the same file). For someone who already keeps their
    own originals elsewhere and does not want a second copy on disk.

    `write_reports=False` skips the per-specimen and per-run Excel/CSV/HTML
    (the "optional" rows in the layout above) -- only the JSON record and
    curve cache are written, plus the always-on combined reports/<material>
    workbook and dashboard covering every run. For someone who only ever
    opens the combined export and finds the per-run copies redundant.
    Neither flag touches the JSON record or curve cache: everything else,
    including the combined export, is rebuilt FROM those, so they are never
    optional.
    """
    cfg = cfg or Config()
    ws = workspace if isinstance(workspace, Workspace) else Workspace.at(workspace)
    ws.ensure()

    paths = [Path(p).expanduser() for p in paths]
    if not paths:
        raise ValueError("ingest needs at least one file")

    # 1. Archive first -- the original is preserved even if the analysis fails.
    sources: list[dict] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        if archive_originals:
            archived, digest = archive_raw(path, ws)
            raw_input_path = ws.relative(archived)
            archived_abs = str(archived)
        else:
            # No copy, but the hash is still needed -- it is what the
            # specimen ID and same-file re-ingest detection are keyed on.
            digest = sha256_file(path)
            raw_input_path = None
            archived_abs = None
        sources.append({
            "source_file": str(path.resolve()),
            "sha256": digest,
            "raw_input_path": raw_input_path,
            "archived_abs": archived_abs,
        })

    material = material or _infer_material(paths)
    fingerprint = run_fingerprint((s["sha256"] for s in sources), cfg)
    run_dir = resolve_run_dir(ws, material, fingerprint, when)
    run_dir.mkdir(parents=True, exist_ok=True)

    # 2. Analyse and write one record per specimen.
    result = IngestResult(material=material, run_dir=run_dir, workspace=ws)
    used_names: set[str] = set()

    for source in sources:
        path_str = source["source_file"]
        try:
            tests = load_tests(path_str, cfg)
        except Exception as exc:  # noqa: BLE001
            result.skipped.append((Path(path_str).name, f"{type(exc).__name__}: {exc}"))
            continue
        if not tests:
            result.skipped.append((Path(path_str).name, "no usable specimen found"))
            continue

        for test in tests:
            df = analyse_test(test, cfg)
            if df.empty:
                result.skipped.append((test.label, "no cycles detected"))
                continue

            payload = build_payload(
                test, df, cfg,
                material=material,
                raw_path=Path(source["archived_abs"]) if source["archived_abs"] else None,
                source_sha256=source["sha256"],
                workspace=ws,
                gauge_length_confirmed=gauge_length_confirmed,
            )
            stem = _unique_stem(slugify(test.label), used_names)
            paths_written = _write_specimen_artifacts(
                test, df, payload, run_dir, stem, write_reports=write_reports,
            )

            payload["_json_path"] = ws.relative(paths_written["json"])
            payload["_run_dir"] = ws.relative(run_dir)

            result.specimens.append(SpecimenResult(
                label=test.label,
                specimen_id=payload["specimen"]["specimen_id"],
                n_cycles=int(len(df)),
                payload=payload,
                json_path=paths_written["json"],
                csv_path=paths_written["csv"],
                xlsx_path=paths_written["xlsx"],
                html_path=paths_written["html"],
                curve_path=paths_written["curve"],
            ))

    # 3. Run-level rollup, only when it says something a single file does not.
    if write_reports and len(result.specimens) > 1:
        combined = run_dir / f"{slugify(material)}_{run_dir.name.split('_')[-1]}.xlsx"
        result.run_xlsx = excel_export.write_workbook(result.payloads, combined)
        excel_export.write_csv(result.payloads, combined.with_suffix(".csv"),
                               with_specimen=True)
        result.run_html = html_report.write_html(
            result.payloads, combined.with_suffix(".html"),
            title=f"{material} - {_run_dir_suffix(run_dir.name, material)}",
        )
    elif len(result.specimens) == 1:
        result.run_xlsx = result.specimens[0].xlsx_path
        result.run_html = result.specimens[0].html_path

    write_manifest(
        run_dir,
        material=material,
        cfg=cfg,
        fingerprint=fingerprint,
        sources=[{k: v for k, v in s.items() if k != "archived_abs"} for s in sources],
        specimens=[
            {
                "specimen_id": s.specimen_id,
                "label": s.label,
                "n_cycles": s.n_cycles,
                "json": s.json_path.name,
            }
            for s in result.specimens
        ],
    )

    # 4. Index last, from what was actually written.
    if update_index and result.specimens:
        conn = knowledge_base.connect(ws.db_path)
        try:
            result.indexed = knowledge_base.index_payloads(conn, result.payloads)
        finally:
            conn.close()

        # 5. Refresh the material's combined workbook + dashboard so it
        # covers this run too. Reads back through the index rather than the
        # in-memory payloads above, since it must include every specimen
        # ever ingested for this material, not just this run's -- the whole
        # point of a rollup that outlives any one ingest session.
        exported = export_material(ws, material)
        result.material_xlsx = exported["xlsx"]
        result.material_html = exported["html"]

    return result


def _run_dir_suffix(run_dir_name: str, material: str) -> str:
    """The part of a run folder's name after the material slug -- the date,
    and any '-002' collision suffix.

    run_dir.name is f"{slugify(material)}_{date}[-NNN]", so a title built as
    "{material} - {run_dir.name}" repeats the material: "T050E1 -
    T050E1_2026-08-17". This strips the slug prefix so the title reads
    "T050E1 - 2026-08-17" instead.
    """
    prefix = slugify(material) + "_"
    if run_dir_name.startswith(prefix):
        return run_dir_name[len(prefix):]
    return run_dir_name


def _write_specimen_artifacts(
    test, df, payload: dict, run_dir: Path, stem: str, *, write_reports: bool = True
) -> dict[str, Optional[Path]]:
    json_path = write_json(payload, run_dir / f"{stem}.json")
    csv_path = xlsx_path = html_path = None
    if write_reports:
        csv_path = excel_export.write_csv([payload], run_dir / f"{stem}.csv")
        xlsx_path = excel_export.write_workbook([payload], run_dir / f"{stem}.xlsx")
        html_path = html_report.write_html([payload], run_dir / f"{stem}.html")
    # Display-only sidecar, not part of the frozen record: the full per-cycle
    # loop shape a chart needs, which the JSON contract deliberately does not
    # carry. Rebuildable from the archived raw file at any time. Never
    # optional -- the combined per-material dashboard (material_export.py)
    # and the interactive Results view both depend on it for every specimen.
    cache = curve_cache.build_curve_cache(
        test, df, specimen_id=payload["specimen"]["specimen_id"]
    )
    curve_path = curve_cache.write_curve_cache(cache, run_dir / f"{stem}.curve.json")
    return {
        "json": json_path,
        "csv": csv_path,
        "xlsx": xlsx_path,
        "html": html_path,
        "curve": curve_path,
    }


def _unique_stem(stem: str, used: set[str]) -> str:
    candidate, n = stem, 1
    while candidate in used:
        n += 1
        candidate = f"{stem}-{n}"
    used.add(candidate)
    return candidate


def _infer_material(paths: Sequence[Path]) -> str:
    """Fall back to the file stem when no material was given. Named explicitly
    in the result so a wrong guess is visible rather than buried."""
    if len(paths) == 1:
        return slugify(paths[0].stem)
    return slugify(os.path.commonprefix([p.stem for p in paths]).strip("_- ") or "batch")


def rebuild_index(workspace: str | os.PathLike | Workspace) -> int:
    ws = workspace if isinstance(workspace, Workspace) else Workspace.at(workspace)
    return knowledge_base.rebuild(ws)
