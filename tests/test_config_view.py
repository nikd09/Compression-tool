"""
config_view.py's "Re-analyse this run" -- reusing a run's already-archived
source file(s) to re-run analysis with different thresholds, without asking
anyone to find and re-upload the original export.

_reanalyze_sources() is the pure, disk-only half (which of a run's sources
still have an archived copy to reuse) and is tested directly, without going
through Streamlit. The full round trip -- resolving those sources and
feeding them back through ingest() -- is tested against pipeline.ingest()
and persistence.read_json() directly, exercising exactly what the "Re-analyse
now" button's callback does.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, ingest, knowledge_base
from compression_tool.core import Config
from compression_tool.persistence import read_json
from compression_tool.webapp.config_view import _reanalyze_label_stems, _reanalyze_sources


def test_reanalyze_sources_finds_an_archived_source(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(result.run_dir / "run.json")

    found, missing = _reanalyze_sources(ws, manifest)

    assert not missing
    assert len(found) == 1
    assert found[0].exists()
    assert found[0].parent == ws.raw


def test_reanalyze_sources_reports_a_never_archived_source_as_missing(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50", archive_originals=False)
    manifest = read_json(result.run_dir / "run.json")

    found, missing = _reanalyze_sources(ws, manifest)

    assert not found
    assert missing == [manifest["sources"][0]["source_file"]]


def test_reanalyze_sources_reports_a_deleted_archive_as_missing(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(result.run_dir / "run.json")
    archived = ws.root / manifest["sources"][0]["raw_input_path"]
    archived.chmod(0o644)
    archived.unlink()

    found, missing = _reanalyze_sources(ws, manifest)

    assert not found
    assert missing == [manifest["sources"][0]["source_file"]]


def test_reanalyzing_with_a_different_config_creates_a_new_run_and_reuses_the_archive(
    workspace, single_file
):
    """The behaviour _render_reanalyze's callback depends on: feeding the
    resolved archive path(s) back through ingest() with a changed Config
    must not touch the original run (a different fingerprint gets its own
    folder, resolve_run_dir's existing rule), and must not create a second
    copy of the already-archived source (archive_raw's idempotency, see
    test_persistence.py's matching pin)."""
    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(first.run_dir / "run.json")
    found, missing = _reanalyze_sources(ws, manifest)
    assert not missing

    changed_cfg = Config(unload_frac=0.05)
    second = ingest(
        found, ws, material=manifest["material"], cfg=changed_cfg,
        archive_originals=True, write_reports=True,
        label_stems=_reanalyze_label_stems(ws, manifest),
        allow_same_content=True,
    )

    assert second.run_dir != first.run_dir
    assert first.run_dir.exists()
    assert len(list(ws.raw.iterdir())) == 1

    second_manifest = read_json(second.run_dir / "run.json")
    assert (
        second_manifest["sources"][0]["raw_input_path"]
        == manifest["sources"][0]["raw_input_path"]
    )


def test_reanalyzing_keeps_the_same_specimen_label_and_id(workspace, single_file):
    """The regression this exists to pin: re-analysing used to re-derive
    each specimen's label from the ARCHIVE's own hash-prefixed, slugified
    filename instead of the name the file had at first ingest -- a
    different label means a different specimen_id (persistence.specimen_id
    hashes source content + label + material), so the "same" specimen
    showed up TWICE in Results after a re-analysis: once under its
    original name, once under "<hash>_Slugified-Name_S1". checked in the running app
    before this fix."""
    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50")
    first_manifest = read_json(first.run_dir / "run.json")
    first_label = first_manifest["specimens"][0]["label"]

    found, _ = _reanalyze_sources(ws, first_manifest)
    second = ingest(
        found, ws, material=first_manifest["material"], cfg=Config(unload_frac=0.05),
        archive_originals=True, write_reports=True,
        label_stems=_reanalyze_label_stems(ws, first_manifest),
    )
    second_manifest = read_json(second.run_dir / "run.json")
    second_label = second_manifest["specimens"][0]["label"]

    assert second_label == first_label
    assert second_manifest["specimens"][0]["specimen_id"] == first_manifest["specimens"][0]["specimen_id"]

    # And the index reflects one specimen, not two, once both runs are indexed.
    conn = knowledge_base.connect(ws.db_path)
    specimens = knowledge_base.list_specimens(conn)
    assert len(specimens) == 1
    assert specimens.iloc[0]["label"] == first_label


def test_reanalyzing_with_the_same_config_overwrites_the_run_in_place(workspace, single_file):
    """Same sources, same config, same day: resolve_run_dir's existing
    "re-run, not a new folder" rule -- re-analysing without touching a
    threshold is idempotent, not a growing pile of near-identical runs."""
    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(first.run_dir / "run.json")
    found, _ = _reanalyze_sources(ws, manifest)

    second = ingest(
        found, ws, material=manifest["material"], cfg=Config(),
        archive_originals=True, write_reports=True,
        label_stems=_reanalyze_label_stems(ws, manifest),
    )

    assert second.run_dir == first.run_dir


def _app() -> None:
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import config_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"])
    config_view.render(ws)


def _run(monkeypatch, workspace) -> AppTest:
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    return AppTest.from_function(_app).run()


def test_config_page_renders_as_tabs_with_the_run_picker_shared_above_them(
    monkeypatch, workspace, single_file
):
    """The reorganised Config page: one Run picker feeding every tab, not a
    long scroll mixing run inspection, the one write action (Re-analyse),
    cross-run exports, the audit trail and index maintenance."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)

    assert not at.exception
    tab_labels = {t.label for t in at.tabs}
    assert tab_labels == {
        ":material/description: Run",
        ":material/refresh: Re-analyse",
        ":material/download: Exports",
        ":material/history: Activity",
        ":material/admin_panel_settings: Maintenance",
    }
    assert any(sb.label == "Run" for sb in at.selectbox)


def test_maintenance_tab_works_even_with_nothing_ingested(monkeypatch, workspace):
    Workspace.at(workspace).ensure()

    at = _run(monkeypatch, workspace)

    assert not at.exception
    assert any(b.label == "Rebuild sample list" for b in at.button)
    assert not any("admin" in (b.label or "").lower() for b in at.button)


def test_reanalyze_button_is_disabled_until_the_confirm_checkbox_is_ticked(
    monkeypatch, workspace, single_file
):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    reanalyze_btn = next(b for b in at.button if b.key == "cfg_reanalyze_btn")
    assert reanalyze_btn.disabled

    confirm = next(cb for cb in at.checkbox if cb.key == "cfg_reanalyze_confirm")
    at = confirm.check().run()
    reanalyze_btn = next(b for b in at.button if b.key == "cfg_reanalyze_btn")
    assert not reanalyze_btn.disabled


def test_reanalyze_all_rewrites_every_run_in_its_own_folder(workspace, single_file, series_file):
    """After a tool update every run is recalculated in place: same run
    folders, same specimens, no duplicates. A run whose original was never
    archived is skipped, not failed."""
    from datetime import datetime

    from compression_tool.webapp.config_view import _stem_of, reanalyze_all

    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="MatA", when=datetime(2026, 1, 5))
    ingest([series_file], ws, material="MatB", when=datetime(2026, 1, 6),
           cfg=Config(stiff_lo_frac=0.3))
    folders_before = sorted(p.parent.name for p in ws.processed.glob("*/run.json"))
    conn = knowledge_base.connect(ws.db_path)
    n_before = len(knowledge_base.list_specimens(conn))
    conn.close()

    done, skipped, failed = reanalyze_all(ws)

    assert (done, skipped, failed) == (2, [], [])
    assert sorted(p.parent.name for p in ws.processed.glob("*/run.json")) == folders_before
    conn = knowledge_base.connect(ws.db_path)
    assert len(knowledge_base.list_specimens(conn)) == n_before
    conn.close()
    # Each run keeps the settings it was saved with.
    matb = read_json(next(ws.processed.glob("MatB_*/run.json")))
    assert matb["config"]["stiff_lo_frac"] == 0.3

    assert _stem_of(r"C:\Users\x\Tests\T050E1 run.xlsx") == "T050E1 run"
    assert _stem_of("/data/T050E1 run.xlsx") == "T050E1 run"


def test_delete_run_removes_one_run_and_keeps_the_material(workspace, single_file, series_file):
    from datetime import datetime

    from compression_tool.material_admin import delete_run

    ws = Workspace.at(workspace).ensure()
    keep = ingest([single_file], ws, material="MatA", when=datetime(2026, 1, 5))
    drop = ingest([series_file], ws, material="MatA", when=datetime(2026, 1, 6))

    out = delete_run(ws, drop.run_dir)

    assert out["removed_specimens"] == 2 and not out["material_removed"]
    assert not drop.run_dir.exists() and keep.run_dir.exists()
    conn = knowledge_base.connect(ws.db_path)
    assert len(knowledge_base.list_specimens(conn, "MatA")) == 1
    conn.close()

    out = delete_run(ws, keep.run_dir)
    assert out["material_removed"]


def test_delete_run_still_reindexes_when_windows_keeps_the_folder(
    workspace, single_file, series_file, monkeypatch
):
    """OneDrive or Explorer can hold an emptied folder open ("Access is
    denied" on the folder itself). The files go, the index is rebuilt, and
    the leftover empty folder is reported instead of stopping half-way."""
    import os
    from datetime import datetime

    from compression_tool import material_admin
    from compression_tool.material_admin import delete_run

    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="MatA", when=datetime(2026, 1, 5))
    drop = ingest([series_file], ws, material="MatA", when=datetime(2026, 1, 6))

    real_rmdir = os.rmdir
    locked = drop.run_dir.name

    def rmdir(path, *a, **kw):
        if str(path).endswith(locked):
            raise PermissionError(5, "Access is denied", str(path))
        return real_rmdir(path, *a, **kw)

    monkeypatch.setattr(os, "rmdir", rmdir)
    monkeypatch.setattr(material_admin.time, "sleep", lambda s: None)

    out = delete_run(ws, drop.run_dir)

    assert out["folder_removed"] is False and out["files_left"] == 0
    conn = knowledge_base.connect(ws.db_path)
    assert len(knowledge_base.list_specimens(conn, "MatA")) == 1
    conn.close()


def test_reanalyzing_twice_keeps_the_original_specimen_names(workspace, tmp_path):
    """After one re-analysis the run's manifest points at the archive copy
    ("<sha12>_<name>"). Taking the label from that again used to save every
    specimen a second time under "<sha12>_..." in the same run folder. The
    label now comes from the run's own records, and any such leftover copy
    is removed by the next re-analysis."""
    import json
    import shutil

    from compression_tool.webapp.config_view import reanalyze_all

    src = tmp_path / "Druckversuch Mehrstufen_X 1149571.xlsx"
    shutil.copy(_series_copy(tmp_path), src)
    ws = Workspace.at(workspace).ensure()
    ingest([src], ws, material="X")

    def labels():
        return sorted(
            read_json(p)["specimen"]["label"] for p in ws.processed.glob("*/*.json")
            if p.name != "run.json" and not p.name.endswith(".curve.json")
        )

    before = labels()
    for _ in range(3):
        reanalyze_all(ws)
        assert labels() == before

    # A run polluted by the old behaviour (a "<sha12>_" copy next to the
    # originals) is cleaned by the next re-analysis.
    run_dir = next(ws.processed.glob("X_*"))
    for p in list(run_dir.glob("*.json")):
        if p.name != "run.json":
            shutil.copy(p, p.with_name("0123456789ab_" + p.name))
            if not p.name.endswith(".curve.json"):
                data = read_json(p.with_name("0123456789ab_" + p.name))
                data["specimen"]["label"] = "0123456789ab_" + data["specimen"]["label"]
                data["specimen"]["specimen_id"] = "x" + data["specimen"]["specimen_id"][1:]
                p.with_name("0123456789ab_" + p.name).write_text(json.dumps(data))
    knowledge_base.rebuild(ws)
    assert len(labels()) == 2 * len(before)
    reanalyze_all(ws)
    assert labels() == before
    conn = knowledge_base.connect(ws.db_path)
    assert len(knowledge_base.list_specimens(conn, "X")) == len(before)
    conn.close()


def _series_copy(tmp_path):
    from conftest import multistage_signal, write_series_workbook

    s, x = multistage_signal()
    path = tmp_path / "series_src.xlsx"
    write_series_workbook(path, {"1": (s, x), "2": (s, x * 1.01)})
    return path


def test_reanalysing_with_changed_settings_replaces_the_run(workspace, single_file):
    """Changed thresholds used to save a second run next to the first, so the
    material showed every specimen twice. The new result now takes the old
    run's place."""
    from datetime import datetime

    from compression_tool.webapp.config_view import _reanalyze_one

    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50", when=datetime(2026, 1, 5))
    manifest = read_json(first.run_dir / "run.json")
    found, _ = _reanalyze_sources(ws, manifest)

    result = _reanalyze_one(ws, manifest, first.run_dir, found, "TALCO50", Config(unload_frac=0.3))

    runs = [p.parent.name for p in ws.processed.glob("*/run.json")]
    assert runs == [first.run_dir.name]
    assert read_json(first.run_dir / "run.json")["config"]["unload_frac"] == 0.3
    conn = knowledge_base.connect(ws.db_path)
    assert len(knowledge_base.list_specimens(conn, "TALCO50")) == len(first.specimens)
    conn.close()
    assert result.run_dir == first.run_dir
