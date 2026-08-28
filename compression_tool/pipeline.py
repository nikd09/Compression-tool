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

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import audit, curve_cache, diagnostics, excel_export, html_report, knowledge_base
from .core import Config, analyse_test, load_tests
from .material_export import export_material
from .material_registry import add_material
from .reports_overview import build_overview
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

_log = logging.getLogger(__name__)


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
    overview_html: Optional[Path] = None
    audit_path: Optional[Path] = None
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
        if self.overview_html:
            lines.append(f"Materials overview (all materials) : {self.overview_html}")
        if self.audit_path:
            lines.append(f"Audit record : {self.audit_path}")
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
            try:
                df = analyse_test(test, cfg)
            except Exception as exc:  # noqa: BLE001 - one degenerate specimen must
                # not take the rest of a multi-file batch's preview down with it.
                out.append({
                    "source_file": path, "label": test.label,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
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


def preview_dashboard_data(
    paths: Sequence[str | os.PathLike],
    cfg: Optional[Config] = None,
    *,
    material: Optional[str] = None,
    material_by_path: Optional[dict[str, str]] = None,
    gauge_length_confirmed: bool = False,
) -> dict:
    """Everything `dashboard_data.build_dashboard_data()` needs, for every
    specimen across `paths`, built entirely in memory.

    Nothing is archived, written to disk, or registered as a material --
    `build_payload()` and `curve_cache.build_curve_cache()` are both pure
    assembly, and even the material name is used only to label the charts,
    never passed to `add_material()`. For the Ingest screen: the same
    charted dashboard Results renders, available before any decision to
    Commit, so a re-check of an old export or a throwaway trial never has
    to become a permanent entry just to be looked at.

    `material_by_path` -- {str(path): material} -- overrides `material` for
    specific paths, for a batch that mixes more than one material: without
    it, every path in the batch is labelled with the SAME `material`
    (webapp/ingest_view.py's per-file material picker is what actually
    builds this mapping when someone uses it; every other caller passes
    nothing and keeps today's one-material-for-the-whole-batch behaviour).

    Reading the file's bytes to hash them is the only disk I/O -- the same
    cost `ingest()` already pays before archiving, just without the archive
    step after it.
    """
    from .dashboard_data import MAX_SPECIMENS  # local: avoids a module-level
    # import cycle (dashboard_data has no reason to import pipeline, but
    # pipeline importing it at module scope would still make the two
    # modules' import order matter for no benefit -- this is the only use).

    cfg = cfg or Config()
    paths = [Path(p).expanduser() for p in paths]
    resolved_material = material or _infer_material(paths)
    material_by_path = material_by_path or {}

    payloads: list[dict] = []
    curves: list[dict] = []
    skipped: list[tuple[str, str]] = []
    for path in paths:
        path_material = material_by_path.get(str(path), resolved_material)
        try:
            tests = load_tests(str(path), cfg)
        except Exception as exc:  # noqa: BLE001 - one bad file must not blank
            # the whole preview; every other file's specimens still render.
            skipped.append((path.name, f"{type(exc).__name__}: {exc}"))
            continue
        digest = sha256_file(path)
        for test in tests:
            try:
                df = analyse_test(test, cfg)
            except Exception as exc:  # noqa: BLE001 - same reasoning as ingest():
                # one degenerate specimen must not take the rest down with it.
                skipped.append((test.label, f"{type(exc).__name__}: {exc}"))
                continue
            if df.empty:
                skipped.append((test.label, "no cycles detected"))
                continue
            payload = build_payload(
                test, df, cfg,
                material=path_material, raw_path=None, source_sha256=digest,
                gauge_length_confirmed=gauge_length_confirmed,
            )
            payloads.append(payload)
            curves.append(curve_cache.build_curve_cache(
                test, df, specimen_id=payload["specimen"]["specimen_id"]
            ))

    total = len(payloads)
    truncated = total > MAX_SPECIMENS
    if truncated:
        # Same limit and same "keep the newest" reasoning as
        # material_export.py's combined dashboard -- the chart's colour
        # palette has a hard ceiling, not something a preview can special-case.
        payloads, curves = payloads[-MAX_SPECIMENS:], curves[-MAX_SPECIMENS:]

    return {
        "material": resolved_material,
        "payloads": payloads,
        "curves": curves,
        "skipped": skipped,
        "truncated": truncated,
        "total_specimens": total,
    }


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

    `archive_originals=False` skips copying the export into Raw exports/ --
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
    if not material or not material.strip():
        # No fallback to the file stem here (preview()/preview_dashboard_data()
        # still infer one, but that is a cosmetic chart title for something
        # never written to disk). Committing is different: the file stem is
        # whatever the export happened to be named -- "Mehrstufiger
        # Druckversuch Vergleichstest 2 T050LR1.xlsx" -- not a material code,
        # and once it is the material every specimen JSON, run folder and
        # report gets built under that name. Failing here, before anything is
        # archived, is what makes a material name mandatory in practice, not
        # just in the webapp form that happens to check for one -- a CLI or
        # script call bypassed that check entirely before this existed.
        raise ValueError(
            "a material name is required to ingest -- pass material=... "
            "(or -m/--material on the CLI); it is not inferred from the file name"
        )
    material = material.strip()

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

    # Registered (and, if it normalizes to match an existing entry, resolved
    # to that entry's exact spelling) from every entry point uniformly --
    # CLI, webapp, or direct API use -- so materials.json can never drift
    # from what has actually been ingested, and "steel-mesh" typed once
    # after "SteelMesh" already exists files under "SteelMesh" rather than
    # quietly starting a second, never-comparable material.
    material = add_material(ws, material)
    fingerprint = run_fingerprint((s["sha256"] for s in sources), cfg)
    # resolve_run_dir claims the folder itself (exclusive-create) -- nothing
    # left to mkdir here.
    run_dir = resolve_run_dir(ws, material, fingerprint, when)

    # 2. Analyse and write one record per specimen.
    result = IngestResult(material=material, run_dir=run_dir, workspace=ws)
    used_names: set[str] = set()

    for source in sources:
        path_str = source["source_file"]
        try:
            tests = load_tests(path_str, cfg)
        except Exception as exc:  # noqa: BLE001
            result.skipped.append((Path(path_str).name, f"{type(exc).__name__}: {exc}"))
            _log.warning("ingest: %s failed to load: %s", Path(path_str).name, exc)
            continue
        if not tests:
            result.skipped.append((Path(path_str).name, "no usable specimen found"))
            continue

        for test in tests:
            try:
                df = analyse_test(test, cfg)
            except Exception as exc:  # noqa: BLE001 - one degenerate specimen must
                # not abort a batch mid-write: everything archived and written
                # for earlier specimens in this call stays valid and indexed.
                result.skipped.append((test.label, f"{type(exc).__name__}: {exc}"))
                _log.warning("ingest: %s failed to analyse: %s", test.label, exc)
                continue
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

    manifest_specimens = [
        {
            "specimen_id": s.specimen_id,
            "label": s.label,
            "n_cycles": s.n_cycles,
            "json": s.json_path.name,
        }
        for s in result.specimens
    ]
    write_manifest(
        run_dir,
        material=material,
        cfg=cfg,
        fingerprint=fingerprint,
        sources=[{k: v for k, v in s.items() if k != "archived_abs"} for s in sources],
        specimens=manifest_specimens,
    )

    # 4. Audit trail: who ingested what, regardless of whether indexing was
    # requested -- an ingest call is worth recording even with
    # update_index=False, and even when every file in it was skipped.
    result.audit_path = audit.record_ingest(
        ws, material=material, run_dir=run_dir,
        sources=sources, specimens=manifest_specimens, skipped=result.skipped,
    )

    # 5. Index last, from what was actually written.
    if update_index and result.specimens:
        conn = knowledge_base.connect(ws.db_path)
        try:
            result.indexed = knowledge_base.index_payloads(conn, result.payloads)
        finally:
            conn.close()

        # 6. Refresh the material's combined workbook + dashboard so it
        # covers this run too. Reads back through the index rather than the
        # in-memory payloads above, since it must include every specimen
        # ever ingested for this material, not just this run's -- the whole
        # point of a rollup that outlives any one ingest session.
        exported = export_material(ws, material)
        result.material_xlsx = exported["xlsx"]
        result.material_html = exported["html"]

        # 7. Refresh the all-materials overview too -- this run may have
        # changed this material's numbers, or (rarer) be the first time it
        # has any specimens at all, either of which the overview page
        # needs to reflect.
        result.overview_html = build_overview(ws)

    _log.info(
        "ingest: %s -> %d specimen(s) indexed, %d skipped, run_dir=%s",
        material, len(result.specimens), len(result.skipped), run_dir,
    )
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
