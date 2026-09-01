"""Overview: the landing view -- what is in this workspace, and what needs
a look, before picking a task. Exists because the app used to open straight
onto Ingest step 1 every time with no orientation at all: no count of what
is here, and no visibility into a run that quietly carries a diagnostic
warning until someone happens to open that exact material's dashboard.

Read-only, like Materials and Config: this tab never writes anything, it
only points at where to go next."""

from __future__ import annotations

import streamlit as st

from .. import audit
from .. import knowledge_base
from ..persistence import Workspace, read_json
from .common import connect_readonly, dashboard_lang

# A diagnostic scan, not a full workspace audit: bounded to the most
# recently analysed specimens so this stays cheap to render on every visit,
# the same trade-off Config's own "Recent activity" already makes (limit=15
# there). A workspace with a genuinely large backlog of unread warnings
# older than this window still surfaces them the normal way, on that
# material's own Results tab -- this is a heads-up, not the only place a
# warning is ever visible.
_RECENT_SPECIMENS_SCANNED = 25

_SEVERITY_ORDER = {"critical": 0, "caution": 1, "info": 2}
_SEVERITY_ICON = {
    "critical": ":material/error:",
    "caution": ":material/warning:",
    "info": ":material/info:",
}

# EN/DE strings for this page. `message`/`message_de` on a warning itself
# comes from diagnostics.py (computed once, at analysis time, alongside the
# numbers it describes) -- this dict is only this page's own chrome.
_T = {
    "header": {"en": "Overview", "de": "Übersicht"},
    "subtitle": {"en": "What's in this workspace, and what needs a look, before picking a task.",
        "de": "Was in diesem Workspace steckt, und was einen Blick braucht, bevor eine Aufgabe gewählt wird."},
    "materials": {"en": "Materials", "de": "Materialien"},
    "specimens": {"en": "Specimens", "de": "Proben"},
    "runs": {"en": "Runs", "de": "Läufe"},
    "empty_title": {"en": "Nothing ingested into this workspace yet",
        "de": "In diesen Workspace wurde noch nichts eingelesen"},
    "empty_caption": {
        "en": "Bring in a test export to get started -- Preview shows what the "
              "engine sees before anything is committed.",
        "de": "Eine Test-Exportdatei hinzufügen, um zu starten -- die Vorschau "
              "zeigt, was die Engine sieht, bevor etwas gespeichert wird."},
    "go_to_ingest": {"en": "Go to Ingest", "de": "Zu Ingest"},
    "needs_a_look": {"en": "Needs a look", "de": "Braucht einen Blick"},
    "no_open_warnings": {"en": "No open diagnostic warnings across the {n} most recently analysed specimen(s).",
        "de": "Keine offenen Diagnosehinweise unter den {n} zuletzt analysierten Proben."},
    "warning_summary": {
        "en": "{flagged} of the {total} most recently analysed specimen(s), across "
              "{materials} material(s), carry a diagnostic warning -- open that "
              "material on Results to read it in full.",
        "de": "{flagged} von {total} zuletzt analysierten Proben, über {materials} "
              "Material(ien) hinweg, tragen einen Diagnosehinweis -- das Material "
              "unter Results öffnen, um ihn vollständig zu lesen."},
    "more": {"en": " (+{n} more)", "de": " (+{n} weitere)"},
    "recent_activity": {"en": "Recent activity", "de": "Letzte Aktivität"},
    "full_log": {"en": "Full log", "de": "Vollständiges Protokoll"},
    "ingested_n_into": {"en": "{time} · {user} ingested {n} specimen(s) into **{material}**",
        "de": "{time} · {user} hat {n} Probe(n) in **{material}** eingelesen"},
}


def _go_to(view_name: str) -> None:
    st.session_state["nav_view"] = view_name
    st.rerun()


def render(ws: Workspace) -> None:
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    st.header(L("header"))
    st.caption(L("subtitle"))

    conn = connect_readonly(ws)
    specimens = knowledge_base.list_specimens(conn) if conn is not None else None
    if specimens is None or specimens.empty:
        _render_empty_state(L)
        return

    c1, c2, c3 = st.columns(3)
    c1.metric(L("materials"), int(specimens["material"].dropna().nunique()))
    c2.metric(L("specimens"), len(specimens))
    c3.metric(L("runs"), int(specimens["run_dir"].dropna().nunique()))

    _render_warnings(ws, specimens, lang, L)
    _render_recent_activity(ws, L)


def _render_empty_state(L) -> None:
    with st.container(border=True, key="card_overview_empty"):
        st.markdown(f"##### {L('empty_title')}")
        st.caption(L("empty_caption"))
        if st.button(L("go_to_ingest"), icon=":material/upload_file:", type="primary"):
            _go_to("Ingest")


_MESSAGE_PREVIEW_CHARS = 140


def _render_warnings(ws: Workspace, specimens, lang: str, L) -> None:
    recent = specimens.sort_values("created_utc", ascending=False).head(_RECENT_SPECIMENS_SCANNED)
    # By material, not by specimen: two specimens from the same run almost
    # always carry the same warning (a gauge length nobody confirmed applies
    # to the whole run, not one sample of it), and a landing-page glance
    # should read as "this material needs a look", once, not the identical
    # sentence repeated once per specimen in it.
    by_material: dict[str, list[dict]] = {}
    n_specimens_flagged = 0
    for _, row in recent.iterrows():
        try:
            record = read_json(ws.root / row["json_path"])
        except (OSError, ValueError):
            # Same degraded case results_view.py already handles: the index
            # still lists a record whose file is gone from disk. A heads-up
            # panel silently skipping it is the right amount of ceremony --
            # Results is where a missing record gets its own real error and
            # points at "Reindex from disk".
            continue
        warnings = record.get("analysis", {}).get("warnings", [])
        if warnings:
            n_specimens_flagged += 1
            by_material.setdefault(row["material"], []).extend(warnings)

    with st.container(border=True, key="card_overview_warnings"):
        st.markdown(f"##### {L('needs_a_look')}")
        if not by_material:
            st.caption(L("no_open_warnings", n=len(recent)))
            return
        st.caption(L(
            "warning_summary", flagged=n_specimens_flagged, total=len(recent),
            materials=len(by_material),
        ))
        for material, warnings in by_material.items():
            worst = min(warnings, key=lambda w: _SEVERITY_ORDER.get(w["severity"], 99))
            icon = _SEVERITY_ICON.get(worst["severity"], ":material/info:")
            message = (worst.get("message_de") or worst["message"]) if lang == "de" else worst["message"]
            if len(message) > _MESSAGE_PREVIEW_CHARS:
                message = message[:_MESSAGE_PREVIEW_CHARS].rsplit(" ", 1)[0] + "…"
            extra = L("more", n=len(warnings) - 1) if len(warnings) > 1 else ""
            st.markdown(f"{icon} **{material}**: {message}{extra}")


def _render_recent_activity(ws: Workspace, L) -> None:
    entries = audit.list_entries(ws, limit=8)
    if not entries:
        return
    with st.container(border=True, key="card_overview_activity"):
        head_l, head_r = st.columns([4, 1.4])
        with head_l:
            st.markdown(f"##### {L('recent_activity')}")
        with head_r:
            if st.button(L("full_log"), icon=":material/history:", key="overview_full_activity_log"):
                # Config's own tabs default to "Run", not "Activity" -- this
                # can only land on Config as a whole, not deep-link into one
                # of its tabs (Streamlit's tabs are not addressable from
                # session_state). "Full log" undersells that half-step, but
                # promising it opens straight to the audit trail and landing
                # somewhere else instead would be worse.
                _go_to("Config")
        for e in entries:
            st.caption(L(
                "ingested_n_into",
                time=e.get("timestamp_utc", "-"), user=e.get("user", "-"),
                n=len(e.get("specimens", [])), material=e.get("material", "-"),
            ))
