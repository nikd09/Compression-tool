"""Shared bits every view needs: which workspace to look at, a read-only
connection to its index, the shared style layer, and the specimen-label
helpers every specimen picker uses so they read the same way everywhere."""

from __future__ import annotations

import json
import os
import re

import streamlit as st

from .. import knowledge_base
from ..core import Config
from ..persistence import Workspace, WorkspacePathNotAllowed, check_workspace_allowed, workspace_index_root
from ..pipeline import rebuild_index

# Resolved once, at import time, from an environment variable set on the
# HOST machine -- not hardcoded, so the same code works for whoever is
# hosting the app today and for whoever it moves to later; only the launch
# environment changes, never this file. Falls back to a plain relative
# folder for local development when the variable is unset.
DEFAULT_WORKSPACE = os.environ.get("COMPRESSION_TOOL_WORKSPACE", "./data")

# The validated categorical slots (light mode; Streamlit widgets do not
# follow the dashboard's dark-mode CSS variables, so these are the literal
# hex steps, not var() references) -- used for the small colour dots next to
# specimen/group names so a picker visually previews the chart colour a
# choice will get, before any chart is drawn.
CATEGORICAL_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]

# Streamlit's own theme covers colour and radius (see .streamlit/config.toml);
# this covers what the theme cannot reach -- card surfaces, hover/focus
# treatment, heading rhythm, and the empty-state look -- so every view (not
# only Results) reads as one designed system rather than default widgets
# next to a custom dashboard.
_POLISH = """
<style>
  .block-container{padding-top:2.2rem;padding-bottom:3rem;max-width:1500px;}
  h1,h2,h3{letter-spacing:-.018em;}
  h1{font-size:1.75rem!important;font-weight:670!important;}
  h2{font-size:1.2rem!important;font-weight:650!important;}
  h3{font-size:1.02rem!important;font-weight:640!important;}
  [data-testid="stSidebarHeader"]{padding-bottom:.4rem;}
  section[data-testid="stSidebar"] h1{font-size:1.05rem!important;}
  /* The dashboard iframe brings its own card surfaces; a second border around
     it would read as a frame inside a frame. */
  iframe[title="st.iframe"]{border:none!important;}
  div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;}
  div[data-testid="stMetricLabel"]{font-size:.72rem;text-transform:uppercase;
    letter-spacing:.06em;font-weight:650;opacity:.72;}

  /* Bordered containers (st.container(border=True, key="card_...")) -- the
     one card style used everywhere a view groups related controls, matching
     the dashboard's own .cell surfaces so the whole app reads as one system.

     This used to target [data-testid="stVerticalBlockBorderWrapper"], which
     turned out to not exist ANYWHERE in this Streamlit version's rendered
     DOM (confirmed live) -- border=True now styles the stVerticalBlock
     itself, via an auto-generated class shared identically by every bordered
     container, not a stable thing to select. `key=` is: Streamlit still
     guarantees a stable `st-key-<key>` class on that element regardless of
     its own internal markup churn. Every st.container(border=True) call
     site that wants this treatment opts in with a key starting "card_" --
     compare_view.py, config_view.py, ingest_view.py and materials_view.py
     (which layers its own richer, clickable-card styling in _CARD_CSS on
     top of this) all follow that convention now; a bordered container added
     later without one simply will not pick this up, the same way it never
     did before this fix -- see README, "Materials cards hover". */
  [class*="st-key-card_"]{
    border-radius:.7rem!important;
    transition:box-shadow .18s ease, border-color .18s ease;
  }
  [class*="st-key-card_"]:hover{
    box-shadow:0 3px 14px rgba(0,0,0,.07);
    border-color:rgba(42,120,214,.45)!important;
  }
  @media (prefers-color-scheme: dark){
    [class*="st-key-card_"]:hover{
      box-shadow:0 3px 14px rgba(0,0,0,.28);
      border-color:rgba(57,135,229,.55)!important;
    }
  }

  /* File uploader: a calmer dashed drop-zone instead of the default block.
     var(--secondary-background-color) was confirmed (DOM inspection) to
     resolve to nothing in this Streamlit build -- it is not defined as a
     custom property anywhere in the page, despite being documented. The
     fallback is this app's own [theme] secondaryBackgroundColor literal, so
     the rule still does something instead of silently no-op'ing. The dark
     variant below matches [theme.dark], since the app does follow the
     browser's prefers-color-scheme regardless of [theme] base. */
  [data-testid="stFileUploaderDropzone"]{
    border-radius:.7rem!important;
    background:color-mix(in srgb, var(--secondary-background-color, #f5eef1) 55%, transparent)!important;
  }
  @media (prefers-color-scheme: dark){
    [data-testid="stFileUploaderDropzone"]{
      background:color-mix(in srgb, var(--secondary-background-color, #241521) 55%, transparent)!important;
    }
  }

  /* Buttons: consistent weight/radius everywhere, primary gets a touch of lift. */
  .stButton>button, .stDownloadButton>button{
    border-radius:.55rem!important;font-weight:600!important;
  }
  .stButton>button[kind="primary"]{box-shadow:0 1px 3px rgba(0,0,0,.14);}

  /* Expanders: a slightly firmer header so they read as controls, not text. */
  [data-testid="stExpander"] summary{font-weight:610!important;}

  /* Numbered step badge used by the Ingest flow. var(--primary-color) has
     the same no-such-custom-property gap as --secondary-background-color
     above (confirmed empty via DOM inspection) -- same fix, same reasoning. */
  .ct-step{display:inline-flex;align-items:center;justify-content:center;
    width:1.55rem;height:1.55rem;border-radius:50%;
    background:var(--primary-color, #2a78d6);color:#fff;font-weight:700;font-size:.82rem;
    margin-right:.55rem;flex:none;}
  @media (prefers-color-scheme: dark){
    .ct-step{background:var(--primary-color, #3987e5);}
  }
  .ct-step-head{display:flex;align-items:center;gap:.1rem;margin-bottom:.15rem;}
  .ct-step-head h3{margin:0!important;}
  .ct-step-sub{margin:0 0 .9rem 2.15rem;font-size:.85rem;opacity:.72;}

  /* Small colour swatch, inline with text -- specimen/group colour preview. */
  .ct-dot{display:inline-block;width:9px;height:9px;border-radius:2.5px;
    margin-right:.4rem;vertical-align:1px;}
</style>
"""


def polish() -> None:
    """Apply the shared style layer. Called once, from app.py."""
    st.markdown(_POLISH, unsafe_allow_html=True)


_CONFIG_FORM_T = {
    "expander": {"en": "Advanced: segmentation and reference thresholds",
        "de": "Erweitert: Segmentierungs- und Referenzschwellenwerte"},
    "intro": {
        "en": "Every threshold is relative to the test's own peak stress, never "
              "absolute: the same knobs `--unload-frac` etc. expose on the "
              "command line. Cycle boundaries are found by locally-adaptive peak "
              "detection, not by these numbers directly -- unload_frac and "
              "major_cycle_frac are SAFETY FLOORS on that, and the stiffness "
              "window is auto-located from the data, with stiff_lo_frac / "
              "stiff_hi_frac used only as a fallback. Defaults work unmodified for "
              "most exports; change one only if Preview below shows a stage being "
              "lost or a cycle miscounted.",
        "de": "Jeder Schwellenwert ist relativ zur eigenen Spitzenspannung der "
              "Prüfung, nie absolut: dieselben Regler, die `--unload-frac` usw. "
              "auf der Kommandozeile bereitstellen. Zyklusgrenzen werden durch "
              "lokal-adaptive Peak-Erkennung gefunden, nicht direkt durch diese "
              "Zahlen -- unload_frac und major_cycle_frac sind "
              "SICHERHEITSUNTERGRENZEN dafür, und das Steifigkeitsfenster wird "
              "automatisch aus den Daten bestimmt, wobei stiff_lo_frac / "
              "stiff_hi_frac nur als Ersatzwert dienen. Die Standardwerte "
              "funktionieren unverändert für die meisten Exporte; einen Wert nur "
              "ändern, wenn die Vorschau unten eine verlorene Stufe oder einen "
              "falsch gezählten Zyklus zeigt."},
    "unload_sensitivity": {"en": "Unload sensitivity", "de": "Entlastungsempfindlichkeit"},
    "unload_sensitivity_help": {
        "en": "A candidate cycle's bounding valley must give back at least this "
              "fraction of the candidate's OWN peak stress to count as a real "
              "load-unload separation, rather than a shoulder on the ramp toward "
              "a taller neighbouring peak.",
        "de": "Das umgebende Tal eines Kandidatenzyklus muss mindestens diesen "
              "Anteil der EIGENEN Spitzenspannung des Kandidaten nachgeben, um "
              "als echte Trennung zwischen Be- und Entlastung zu zählen, statt "
              "als Schulter auf dem Weg zu einem höheren Nachbarn."},
    "min_cycle_size": {"en": "Minimum cycle size", "de": "Mindestzyklusgröße"},
    "min_cycle_size_help": {
        "en": "A candidate peaking below this fraction of the GLOBAL peak is "
              "rejected outright, regardless of its neighbours -- catches "
              "near-zero contact-finding blips. Kept low: real stages are judged "
              "by unload_frac and local noise, not by this.",
        "de": "Ein Kandidat mit einer Spitze unter diesem Anteil der GLOBALEN "
              "Spitze wird unabhängig von seinen Nachbarn sofort verworfen -- "
              "fängt Nahe-Null-Kontaktartefakte ab. Niedrig gehalten: echte "
              "Stufen werden anhand von unload_frac und lokalem Rauschen "
              "beurteilt, nicht hierdurch."},
    "stiff_lo": {"en": "Stiffness window start (fallback)", "de": "Steifigkeitsfenster Anfang (Ersatzwert)"},
    "stiff_lo_help": {
        "en": "Fallback stiffness window (fraction of that cycle's own peak), "
              "used only when no auto-located window clears the minimum span.",
        "de": "Ersatz-Steifigkeitsfenster (Anteil der eigenen Spitze dieses "
              "Zyklus), nur verwendet, wenn kein automatisch bestimmtes Fenster "
              "die Mindestspanne erreicht."},
    "stiff_hi": {"en": "Stiffness window end (fallback)", "de": "Steifigkeitsfenster Ende (Ersatzwert)"},
    "stiff_hi_help": {"en": "Fallback stiffness window upper bound -- see Stiffness window start.",
        "de": "Obere Grenze des Ersatz-Steifigkeitsfensters -- siehe Steifigkeitsfenster Anfang."},
    "reference_stress": {"en": "Reference stress", "de": "Referenzspannung"},
    "reference_stress_help": {
        "en": "The one low, test-wide reference stress (fraction of the global "
              "peak), read on the loading and unloading branches of every cycle. "
              "Used both for permanent deformation (within one cycle) and for "
              "cross-cycle comparison (the same reading, cycle over cycle) -- "
              "see the warning below if a cycle's own peak puts this in the "
              "contact-loss-noise range.",
        "de": "Die eine niedrige, prüfungsweite Referenzspannung (Anteil der "
              "globalen Spitze), gelesen auf dem Belastungs- und Entlastungsast "
              "jedes Zyklus. Verwendet sowohl für die bleibende Verformung "
              "(innerhalb eines Zyklus) als auch für den Zyklenvergleich "
              "(dieselbe Ablesung, Zyklus für Zyklus) -- siehe den Hinweis "
              "unten, falls die eigene Spitze eines Zyklus dies in den "
              "Kontaktverlust-Rauschbereich bringt."},
    "hold_tol": {"en": "Hold detection tolerance", "de": "Toleranz für Halteerkennung"},
    "hold_tol_help": {
        "en": "How much a signal can drift during a dwell and still count as "
              "\"held\", as a fraction of that cycle's peak stress.",
        "de": "Wie weit ein Signal während einer Verweilzeit driften darf und "
              "noch als „gehalten“ zählt, als Anteil der Spitzenspannung dieses "
              "Zyklus."},
    "h0_override": {"en": "Specimen thickness override (h0)", "de": "Überschreibung der Probendicke (h0)"},
    "h0_placeholder": {"en": "blank = read from the export's metadata sheet",
        "de": "leer = aus dem Metadatenblatt des Exports gelesen"},
    "h0_error": {"en": "h0_mm override must be a number, got {value!r}",
        "de": "Die Überschreibung von h0_mm muss eine Zahl sein, erhalten: {value!r}"},
}


def config_form(detect_holds: bool) -> Config:
    """The "Advanced: segmentation and reference thresholds" expander --
    every threshold knob the CLI also exposes, defaulted from a fresh
    Config() and rendered as one form. Shared between Ingest (where a
    changed value is tried against a Preview before anything is committed)
    and Config's "Re-analyse this run" (where it is applied straight to a
    run's already-archived sources) so the two never drift into offering a
    different set of knobs for the same underlying Config fields.
    """
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _CONFIG_FORM_T[key][lang]
        return s.format(**kw) if kw else s

    d = Config()
    with st.expander(L("expander")):
        st.caption(L("intro"))
        # Every field pairs a plain-language label (what a materials engineer
        # who has never opened core.py would call this) with the exact
        # Config field name/CLI flag as a small caption underneath -- not
        # replaced, since that name is still what --unload-frac etc. expect
        # on the command line and what "Settings this run used" on Config
        # prints back. Neither alone was right: the raw field name as the
        # ONLY label assumes everyone reads this file's source; the plain
        # label alone would silently break the link to the CLI/JSON name the
        # same knob is addressed by everywhere else.
        c1, c2 = st.columns(2)
        with c1:
            unload_frac = st.number_input(
                L("unload_sensitivity"), value=d.unload_frac, format="%.3f",
                help=L("unload_sensitivity_help"))
            st.caption("`unload_frac`")
            major_cycle_frac = st.number_input(
                L("min_cycle_size"), value=d.major_cycle_frac, format="%.3f",
                help=L("min_cycle_size_help"))
            st.caption("`major_cycle_frac`")
            stiff_lo_frac = st.number_input(
                L("stiff_lo"), value=d.stiff_lo_frac, format="%.2f",
                help=L("stiff_lo_help"))
            st.caption("`stiff_lo_frac`")
            stiff_hi_frac = st.number_input(
                L("stiff_hi"), value=d.stiff_hi_frac, format="%.2f",
                help=L("stiff_hi_help"))
            st.caption("`stiff_hi_frac`")
        with c2:
            residual_stress_frac = st.number_input(
                L("reference_stress"), value=d.residual_stress_frac, format="%.2f",
                help=L("reference_stress_help"))
            st.caption("`residual_stress_frac`")
            hold_tol_frac = st.number_input(
                L("hold_tol"), value=d.hold_tol_frac, format="%.3f",
                help=L("hold_tol_help"))
            st.caption("`hold_tol_frac`")
            h0_text = st.text_input(
                L("h0_override"), value="",
                placeholder=L("h0_placeholder"))
            st.caption("`h0_mm`")
    h0_mm = None
    if h0_text.strip():
        try:
            h0_mm = float(h0_text)
        except ValueError:
            st.error(L("h0_error", value=h0_text))
    return Config(
        unload_frac=unload_frac,
        major_cycle_frac=major_cycle_frac,
        stiff_lo_frac=stiff_lo_frac,
        stiff_hi_frac=stiff_hi_frac,
        residual_stress_frac=residual_stress_frac,
        hold_tol_frac=hold_tol_frac,
        h0_mm=h0_mm,
        detect_holds=detect_holds,
    )


def dot(i: int) -> str:
    """An inline HTML colour swatch for categorical slot i, e.g. for a
    specimen or comparison-group label. Cycles past 8 rather than raising --
    a label swatch overshooting the palette is a cosmetic rollover, not the
    hard stop MAX_SPECIMENS enforces where the swatch is load-bearing."""
    colour = CATEGORICAL_LIGHT[i % len(CATEGORICAL_LIGHT)]
    return f'<span class="ct-dot" style="background:{colour}"></span>'


def utm_press_html(caption: str = "Analysing…") -> str:
    """A large, centered looping animation of a UTM crosshead compressing a
    specimen -- shown in a `st.empty()` placeholder around a call that
    blocks the script (`ingest()`, `preview()`), since that is the one place
    a CSS animation can run independently of Python: once this markup has
    actually reached the browser, the animation keeps looping in its own
    render loop for as long as the blocking call takes, with no further
    communication from the (busy) Python side needed to keep it moving.

    Rendered as a fixed, full-viewport overlay -- not inline content where
    it landed in the page -- centred regardless of scroll position or
    screen size: this used to sit right where the triggering button was,
    which on a long form (Ingest's Preview/Commit) could be well below the
    fold, invisible until someone scrolled down to it. `position:fixed` on
    the backdrop is what actually fixes that; centring an inline element
    with a scroll-into-view call would only have chased the symptom on one
    viewport size and broken on the next.

    The card itself is still the centrepiece of whatever moment it appears
    in -- a big, unmissable stand-in for "the machine is working" while
    nothing else on the page can update. The stroke shape doubles as a hint
    at what the tool is analysing rather than a generic spinner:
    down-hold-up, not a smooth back-and-forth -- echoing the
    load-dwell-unload cycle shape every specimen this tool ingests actually
    goes through.
    """
    return f"""
<div class="ct-utm-overlay">
<div class="ct-utm-wrap">
  <style>
  .ct-utm-overlay{{
    position:fixed; inset:0; z-index:99999;
    display:flex; align-items:center; justify-content:center;
    animation:ctUtmFadeIn .15s ease;
  }}
  .ct-utm-wrap{{
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:1.1rem;padding:2.8rem 2.4rem;border-radius:1.1rem;
    max-width:min(92vw,440px);
    background:#f9f9f7;
    box-shadow:0 24px 64px rgba(0,0,0,.35);
  }}
  @media (prefers-color-scheme: dark){{
    .ct-utm-wrap{{background:#1a1a19;}}
  }}
  @keyframes ctUtmFadeIn{{ from{{opacity:0}} to{{opacity:1}} }}
  .ct-utm-svg{{flex:none;width:220px;height:220px;overflow:visible;
    filter:drop-shadow(0 6px 18px rgba(0,0,0,.12));}}
  .ct-utm-cap{{font-size:1.15rem;font-weight:650;opacity:.85;letter-spacing:-.01em;}}
  .ct-utm-head{{animation:ctUtmPress 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 0%;}}
  .ct-utm-specimen{{animation:ctUtmSquash 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 100%;}}
  .ct-utm-glow{{animation:ctUtmGlow 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 100%;}}
  .ct-utm-specimen, .ct-utm-glow{{fill:#2a78d6;}}
  /* var(--text-color) does not actually resolve in this Streamlit build
     (confirmed empty via DOM inspection, same gap as --primary-color and
     --secondary-background-color elsewhere in this file) -- its fallback,
     a near-black, is what always rendered, on every theme. Harmless in
     light mode; in dark mode it drew the whole machine frame as
     near-black on a near-black card, effectively invisible. Explicit
     per-theme fill on this class is the actual fix, not the var(). */
  .ct-utm-ink{{fill:#0b0b0b;}}
  @media (prefers-color-scheme: dark){{
    .ct-utm-specimen, .ct-utm-glow{{fill:#3987e5;}}
    .ct-utm-ink{{fill:#f4f2f1;}}
  }}
  @keyframes ctUtmPress{{
    0%{{transform:translateY(0)}}
    35%{{transform:translateY(30px)}}
    50%{{transform:translateY(33px)}}
    65%{{transform:translateY(30px)}}
    100%{{transform:translateY(0)}}
  }}
  @keyframes ctUtmSquash{{
    0%{{transform:scaleY(1)}}
    35%{{transform:scaleY(.78)}}
    50%{{transform:scaleY(.74)}}
    65%{{transform:scaleY(.78)}}
    100%{{transform:scaleY(1)}}
  }}
  @keyframes ctUtmGlow{{
    0%{{opacity:.18;transform:scaleX(1)}}
    35%{{opacity:.5;transform:scaleX(1.35)}}
    50%{{opacity:.6;transform:scaleX(1.42)}}
    65%{{opacity:.5;transform:scaleX(1.35)}}
    100%{{opacity:.18;transform:scaleX(1)}}
  }}
  @media (prefers-reduced-motion: reduce){{
    .ct-utm-head, .ct-utm-specimen, .ct-utm-glow{{animation:none;}}
  }}
  </style>
  <svg class="ct-utm-svg" viewBox="0 0 100 100" role="img" aria-label="Compressing specimen">
    <ellipse class="ct-utm-glow" cx="50" cy="84" rx="20" ry="4"/>
    <rect class="ct-utm-ink" x="14" y="6" width="8" height="82" rx="2" opacity=".35"/>
    <rect class="ct-utm-ink" x="78" y="6" width="8" height="82" rx="2" opacity=".35"/>
    <rect class="ct-utm-ink" x="10" y="2" width="80" height="8" rx="2" opacity=".55"/>
    <rect class="ct-utm-ink" x="30" y="80" width="40" height="8" rx="2" opacity=".55"/>
    <rect class="ct-utm-specimen" x="38" y="62" width="24" height="18" rx="2"/>
    <g class="ct-utm-head">
      <rect class="ct-utm-ink" x="26" y="24" width="48" height="10" rx="2" opacity=".78"/>
    </g>
  </svg>
  <span class="ct-utm-cap">{caption}</span>
</div>
</div>
"""


def with_utm_animation(caption: str, fn):
    """Runs `fn()` (a blocking call) with the UTM press animation shown for
    its duration -- the same overlay Ingest's Preview/Commit already use,
    now shared so every other blocking action in the app (Config's
    Re-analyse, Reindex, and the two Rebuild-export buttons) gets the same
    "the machine is working" feedback instead of Streamlit's own silent
    blocking rerun, which previously left those four buttons with no
    visual feedback at all while they ran.

    The animation is CSS-driven and keeps looping in the browser's own
    render loop once this markup has reached it, independent of Python
    being busy; the placeholder is what lets it disappear again the moment
    `fn()` returns, success or failure alike.
    """
    placeholder = st.empty()
    placeholder.markdown(utm_press_html(caption), unsafe_allow_html=True)
    try:
        return fn()
    finally:
        placeholder.empty()


_LANG_KEY = "app_lang"


def language_picker() -> str:
    """Render the ONE EN/DE toggle for the whole app session, the same
    "called exactly once, from app.py" pattern workspace_picker() above
    uses -- so switching language is one control, not a separate one on
    Compare and a separate one again inside every embedded dashboard.

    Returns the two-letter code ('en'/'de'), also stashed in
    st.session_state[_LANG_KEY] for dashboard_lang()/inject_dashboard_lang()
    to read from views that do not call this directly.
    """
    choice = st.radio(
        "Language", ["EN", "DE"], horizontal=True, key="app_lang_choice",
        label_visibility="collapsed",
    )
    lang = "de" if choice == "DE" else "en"
    st.session_state[_LANG_KEY] = lang
    return lang


def dashboard_lang() -> str:
    """The current shared language, for a view that reads it without
    rendering the toggle itself (every view except app.py's sidebar)."""
    return st.session_state.get(_LANG_KEY, "en")


def inject_dashboard_lang(html_text: str) -> str:
    """Seeds results_dashboard.html's own LANG with the shared toggle's
    current value, by prepending a small global read ahead of the
    template's own `const DATA = ...` line -- see that file's `let LANG =`
    init, which checks `window.__CT_LANG__` before its own localStorage
    default. Applied at every Streamlit call site that embeds the
    template (Results, Ingest's Preview, the Materials dashboard) so all
    three start in the language the sidebar is set to, instead of each
    iframe defaulting independently.

    A file that never passes through here -- opened directly from disk,
    outside Streamlit, e.g. reports/<material>.html mailed to a colleague
    -- keeps the template's own localStorage-based default and its own
    in-page EN/DE buttons still work there; there is no Streamlit session
    to read in that case.
    """
    marker = "<script>\nconst DATA"
    if marker not in html_text:
        return html_text
    lang = dashboard_lang()
    return html_text.replace(
        marker, f"<script>\nwindow.__CT_LANG__={json.dumps(lang)};\nconst DATA", 1,
    )


_SAMPLE_SUFFIX = re.compile(r"[_\s-](S\d+)$", re.IGNORECASE)


def short_tag(label: str, fallback_index: int) -> str:
    """A short, stable tag for one specimen label -- 'S1', 'S2', ... pulled
    from the label's own trailing _S<n> (the convention every ingested label
    already carries), or '#N' if a label does not follow it.

    Exists because a picker showing full labels side by side breaks when
    labels share a long common prefix (the same source filename): a
    truncated dropdown or chip cuts off exactly the suffix that told two
    specimens apart, leaving entries that read as identical. Put first, this
    survives that truncation.
    """
    m = _SAMPLE_SUFFIX.search(label)
    return m.group(1).upper() if m else f"#{fallback_index}"


def workspace_picker() -> Workspace:
    """Render the ONE Workspace input for the whole app session.

    Called exactly once, from app.py, never from inside a view. A
    `key="workspace_root"` widget re-declared at a second call site (e.g. a
    second `st.text_input(key="workspace_root")` inside a different view
    function) is, to Streamlit, a *different* widget instance sharing that
    key -- and switching to it drops the session_state value back to the
    widget's own default instead of carrying it over. Confirmed with an
    isolated repro: the same key at two call sites resets on every switch; a
    single call site does not. Every view receives the resolved `Workspace`
    as a plain argument instead of calling this again.
    """
    st.session_state.setdefault("workspace_root", DEFAULT_WORKSPACE)
    # Plain st.text_input, not st.sidebar.text_input: this is always called
    # from inside app.py's own `with st.sidebar:` block now, and mixing the
    # explicit st.sidebar.* API with an ambient `with st.sidebar:` context
    # was the remaining cause of the reset -- each re-enters the sidebar
    # container by a slightly different path, which is exactly the kind of
    # position-sensitivity a keyed widget's carried-over value turned out to
    # be sensitive to. Nesting it one level deeper inside an expander below
    # does not reintroduce that: the widget is still instantiated on every
    # run regardless of whether the expander is visually open (Streamlit
    # still executes a collapsed expander's body), and nothing about ITS
    # position relative to the nav loop above changed -- confirmed live,
    # switching tabs repeatedly still carries the value forward.
    #
    # Collapsed by default and labelled "Advanced": most people editing this
    # form should never need to touch it (COMPRESSION_TOOL_WORKSPACE already
    # sets where it opens), and a text box inviting an arbitrary path is
    # exactly the thing worth NOT presenting as the first thing in the
    # sidebar. check_workspace_allowed() below is the actual enforcement --
    # this is only making the deliberate override read as deliberate.
    with st.expander("Advanced: change workspace", expanded=False):
        root = st.text_input(
            "Workspace",
            key="workspace_root",
            help="Where Raw exports/, Records/ and reports/ live: every "
            "specimen's JSON, CSV, Excel workbook and HTML report land here on "
            "every Commit, nowhere else. The same path every time this app is "
            "opened shows the same tests; pointing it elsewhere switches "
            "workspaces, it does not copy anything between them. A relative path "
            "like the default is resolved against wherever `streamlit run` was "
            "launched from; see the resolved path below if that's unclear. "
            "Everything here stays local: no network calls, nothing uploaded. "
            "The search index is kept separately, on this machine only; see "
            "the note below the resolved path.",
        )
    try:
        check_workspace_allowed(root)
    except WorkspacePathNotAllowed as exc:
        st.error(str(exc))
        st.stop()
    # The searchable index is deliberately NOT under `root`: `root` is
    # expected to be a synced or shared folder (OneDrive, a network drive),
    # and syncing a SQLite file while it is being written is a well-known
    # way to corrupt it. The index is disposable and rebuilt from the JSON
    # records under `root` -- moving it off the synced path costs nothing.
    # Scoped per workspace (workspace_index_root, not the bare
    # default_index_root() base) so switching this field to a different
    # folder can never pick up a previous workspace's already-built index.
    ws = Workspace.at(root, index_root=workspace_index_root(root))
    if not ws.db_path.exists() and ws.processed.exists() and any(ws.processed.iterdir()):
        # First time this workspace has been opened from THIS machine (or
        # the local index cache was cleared): there is real data under
        # `root` but nothing indexed locally yet. Build it once now rather
        # than showing an empty app in front of a non-empty workspace.
        rebuild_index(ws)
    # A relative path (the default, "./data") silently depends on the launch
    # directory -- shown resolved so it is never ambiguous where a run
    # actually landed on disk.
    st.caption(f"→ `{ws.root.resolve()}`")
    st.caption(f"Index (this machine only) → `{ws.db_path.parent}`")
    return ws


def connect_readonly(ws: Workspace):
    """A connection for browsing, or None if this workspace has nothing
    indexed yet. Read-only views say so plainly rather than create an empty
    database just by being opened.

    A schema mismatch (the index was built under an older/newer
    SCHEMA_VERSION than this code) shows a clear message and halts the
    script with `st.stop()` -- not returning None -- so it reads as its
    own error, not silently as "nothing indexed yet" (every caller's own
    `if conn is None` fallback is exactly that message, which would be
    actively misleading here: this workspace is not empty, its index just
    needs rebuilding). This is the one call site that catches it: Materials'
    and Config's own knowledge_base.connect() calls (material_export.py,
    reports_overview.py) are not routed through here and would still raise
    it raw, as an ordinary Streamlit traceback.
    """
    if not ws.db_path.exists():
        return None
    try:
        return knowledge_base.connect(ws.db_path)
    except knowledge_base.SchemaVersionMismatch as exc:
        st.error(str(exc))
        st.stop()
