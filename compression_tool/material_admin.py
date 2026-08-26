"""
material_admin.py
==================
Rename and delete a material, everywhere it appears: every specimen
record, every run folder under Records/, the shared materials.json list,
the combined reports/<material> export, and the SQLite index.

Both operations edit the JSON records -- the documented source of truth
(persistence.py) -- and then call knowledge_base.rebuild(), the same
full drop-and-reindex-from-disk the app already uses to recover from any
other index/disk disagreement. That is what keeps this module from having
to hand-patch the database: whatever the JSON files now say on disk is
what the index ends up holding, exactly as if the workspace had just been
opened for the first time.

Gated in the webapp by permissions.is_admin() -- neither function checks
permissions itself, so a script or the CLI can always use them; the admin
gate is a UI concern, not a data-safety one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import knowledge_base
from .material_export import export_material
from .material_registry import add_material, remove_material
from .persistence import Workspace, read_json, slugify, specimen_id, write_json
from .reports_overview import build_overview


def _material_run_dirs(ws: Workspace, material: str) -> list[Path]:
    """Every run folder under Records/ whose manifest was ingested for
    exactly this material -- comparing the material FIELD, not the folder
    name, since a folder is only ever named from a slug of it."""
    if not ws.processed.exists():
        return []
    dirs = []
    for manifest_path in sorted(ws.processed.glob("*/run.json")):
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError):
            continue
        if manifest.get("material") == material:
            dirs.append(manifest_path.parent)
    return dirs


def _unique_target(base: Path) -> Path:
    """`base`, or the first `base-NNN` that does not already exist -- the
    same collision handling resolve_run_dir() uses when claiming a new run
    folder, needed here because renaming a material can make two run
    folders (different original dates or config) collide on the same
    `<new-slug>_<date>` target."""
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = base.parent / f"{base.name}-{index:03d}"
        if not candidate.exists():
            return candidate
        index += 1


def rename_material(ws: Workspace, old: str, new: str) -> dict:
    """Rename `old` to `new` across every run folder, specimen record and
    derived export. Returns a summary dict; never raises for a single run
    folder that fails to move (a file open elsewhere, a locked share) --
    that folder is reported in `failed`, everything else still completes,
    and the specimen JSON inside it was already rewritten either way so
    the index and every chart pick up the new name regardless of whether
    the folder itself could be renamed to match.

    specimen_id is derived from (source hash, material, label)
    (persistence.specimen_id), so it changes with the material -- every
    specimen JSON in scope is rewritten with a freshly computed id, not
    just a new "material" field. knowledge_base.rebuild() afterwards is
    what makes that safe: it re-derives the whole index from the JSONs on
    disk rather than trying to UPDATE rows whose primary key just changed
    out from under them.
    """
    old = old.strip()
    new = new.strip()
    if not new:
        raise ValueError("new material name cannot be empty")
    if not old:
        raise ValueError("old material name cannot be empty")

    run_dirs = _material_run_dirs(ws, old)
    if not run_dirs:
        raise ValueError(f"no indexed run found for material {old!r}")

    renamed_specimens = 0
    moved_dirs: list[tuple[Path, Path]] = []
    failed: list[str] = []

    for run_dir in run_dirs:
        # run.json (the manifest, handled below) and *.curve.json (a
        # sidecar with no "specimen" key of its own -- see curve_cache.py)
        # both match *.json but are not specimen records; the "specimen"
        # in payload check, the same guard knowledge_base.rebuild() uses,
        # is what actually distinguishes a record from either of those,
        # not the filename.
        for json_path in sorted(run_dir.glob("*.json")):
            if json_path.name == "run.json":
                continue
            try:
                payload = read_json(json_path)
            except (OSError, ValueError):
                continue
            if "specimen" not in payload:
                continue
            spec = payload["specimen"]
            spec["material"] = new
            spec["specimen_id"] = specimen_id(
                spec.get("source_sha256", ""), spec.get("label", ""), new
            )
            payload["specimen"] = spec
            write_json(payload, json_path)
            renamed_specimens += 1

        manifest_path = run_dir / "run.json"
        try:
            manifest = read_json(manifest_path)
            manifest["material"] = new
            write_json(manifest, manifest_path)
        except (OSError, ValueError):
            pass

        # Best-effort: the specimen JSONs above are already updated
        # regardless of whether this succeeds, so a locked folder still
        # ends up correctly re-indexed under the new material -- it just
        # keeps the old material's name in its path on disk.
        #
        # Strip the OLD slug as an exact known prefix, not by splitting on
        # the first underscore: the slug itself can contain underscores
        # (e.g. "T050LR1_batch2"), so a folder named
        # "T050LR1_batch2_2026-08-25" would otherwise have its split point
        # land inside the material name instead of at the date, leaving
        # part of the old name stuck onto the new one.
        old_prefix = slugify(old) + "_"
        suffix = (
            run_dir.name[len(old_prefix):]
            if run_dir.name.startswith(old_prefix)
            else run_dir.name
        )
        target = _unique_target(run_dir.parent / f"{slugify(new)}_{suffix}")
        try:
            new_dir = Path(shutil.move(str(run_dir), str(target)))
            moved_dirs.append((run_dir, new_dir))
        except OSError:
            failed.append(run_dir.name)

    # The registry, and the combined exports slugified from the name --
    # both keyed on the OLD name and now stale.
    remove_material(ws, old)
    canonical = add_material(ws, new)

    reports_dir = ws.root / "reports"
    old_slug = slugify(old)
    for suffix in (".xlsx", ".html"):
        stale = reports_dir / f"{old_slug}{suffix}"
        if stale.exists():
            stale.unlink()

    knowledge_base.rebuild(ws)
    export_material(ws, canonical)
    build_overview(ws)

    return {
        "material": canonical,
        "renamed_specimens": renamed_specimens,
        "moved_run_dirs": len(moved_dirs),
        "failed": failed,
    }


def delete_material(ws: Workspace, material: str, *, delete_raw: bool = False) -> dict:
    """Remove `material` and everything derived from it: every run folder
    under Records/ (specimen JSONs, curve caches, per-run exports), its
    combined reports/<material>.{xlsx,html}, and its entry in
    materials.json, then reindex so Materials/Compare/Results stop
    showing it.

    Raw exports/ is content-addressed (persistence.archive_raw) and may be
    shared: the same source file ingested a second time under a different
    material name reuses the identical archived copy. Left alone by
    default for that reason; delete_raw=True removes it too, but only for
    a source no OTHER remaining specimen still references, checked after
    this material's own run folders are already gone.
    """
    material = material.strip()
    if not material:
        raise ValueError("material name cannot be empty")

    run_dirs = _material_run_dirs(ws, material)
    if not run_dirs:
        raise ValueError(f"no indexed run found for material {material!r}")

    def _specimen_jsons(run_dir: Path) -> list[Path]:
        # Same "specimen" in payload guard as rename_material() and
        # knowledge_base.rebuild() -- *.curve.json sidecars also match
        # *.json and would otherwise be double-counted as specimens here.
        out = []
        for json_path in run_dir.glob("*.json"):
            if json_path.name == "run.json":
                continue
            try:
                payload = read_json(json_path)
            except (OSError, ValueError):
                continue
            if "specimen" in payload:
                out.append((json_path, payload))
        return out

    raw_paths: set[Path] = set()
    removed_specimens = 0
    for run_dir in run_dirs:
        specs = _specimen_jsons(run_dir)
        if delete_raw:
            for _json_path, payload in specs:
                rel = payload["specimen"].get("raw_input_path")
                if rel:
                    raw_paths.add((ws.root / rel).resolve())
        removed_specimens += len(specs)
        shutil.rmtree(run_dir, ignore_errors=True)

    remove_material(ws, material)

    reports_dir = ws.root / "reports"
    slug = slugify(material)
    for suffix in (".xlsx", ".html"):
        stale = reports_dir / f"{slug}{suffix}"
        if stale.exists():
            stale.unlink()

    knowledge_base.rebuild(ws)

    removed_raw = 0
    if delete_raw and raw_paths:
        still_referenced = set()
        conn = knowledge_base.connect(ws.db_path)
        try:
            df = knowledge_base.list_specimens(conn)
        finally:
            conn.close()
        if not df.empty and "raw_input_path" in df.columns:
            still_referenced = {
                (ws.root / rel).resolve()
                for rel in df["raw_input_path"].dropna()
            }
        for raw_path in raw_paths - still_referenced:
            try:
                raw_path.chmod(0o644)
                raw_path.unlink(missing_ok=True)
                removed_raw += 1
            except OSError:
                pass

    build_overview(ws)

    return {
        "removed_run_dirs": len(run_dirs),
        "removed_specimens": removed_specimens,
        "removed_raw_files": removed_raw,
    }
