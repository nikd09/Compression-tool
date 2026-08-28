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

from compression_tool import Workspace, ingest
from compression_tool.core import Config
from compression_tool.persistence import read_json
from compression_tool.webapp.config_view import _reanalyze_sources


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
    )

    assert second.run_dir != first.run_dir
    assert first.run_dir.exists()
    assert len(list(ws.raw.iterdir())) == 1

    second_manifest = read_json(second.run_dir / "run.json")
    assert (
        second_manifest["sources"][0]["raw_input_path"]
        == manifest["sources"][0]["raw_input_path"]
    )


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
    cross-run exports, the audit trail and workspace administration."""
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
        ":material/admin_panel_settings: Administration",
    }
    assert any(sb.label == "Run" for sb in at.selectbox)


def test_administration_tab_works_even_with_nothing_ingested(monkeypatch, workspace):
    Workspace.at(workspace).ensure()

    at = _run(monkeypatch, workspace)

    assert not at.exception
    assert any(b.label == "Claim admin access for myself" for b in at.button)


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
