"""A single shared password gate for the whole app, IF one is configured.

Real access control -- who is this person, what are THEY allowed to do --
is not something to build into a Streamlit script; that is what fronting a
hosted deployment with the corporate reverse proxy / SSO is for. This
exists for the narrower, real gap in between: a workspace stood up on a
server before that is in place still has no barrier at all today, and
"one shared secret in front of it" is a small, honest improvement over
"open to anyone who reaches the URL" for that window -- not a replacement
for real authentication once it exists.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import streamlit as st

from .common import dashboard_lang, language_picker, polish

_PASSWORD_ENV = "COMPRESSION_TOOL_PASSWORD"
_SESSION_KEY = "_ct_authenticated"

# The exact same animated press mark used at collapsed-sidebar size
# elsewhere (app.py's st.logo icon_image) -- inlined here, not passed
# through st.image, so its own baked-in CSS keyframes (the same rig
# logo.svg's full wordmark uses) actually play: st.image serves an SVG as
# a static <img>, which does not execute the <style>/keyframes inside it,
# only inline/embedded SVG markup does. Read once at import time; this
# file never changes at runtime.
_LOGO_SVG = (Path(__file__).parent / "static" / "logo-icon.svg").read_text(encoding="utf-8")

_T = {
    "subtitle": {"en": "Compression Test Analysis",
        "de": "Analyse mehrstufiger Druckversuche"},
    "intro": {
        "en": "Enter your access password to continue.",
        "de": "Bitte das Zugangspasswort eingeben, um fortzufahren."},
    "password": {"en": "Password", "de": "Passwort"},
    "enter": {"en": "Continue", "de": "Weiter"},
    "incorrect": {"en": "Incorrect password.", "de": "Falsches Passwort."},
}

# A thin, static line-art motif of a loading/unloading hysteresis loop --
# exactly the shape this tool's own dashboards plot for every cycle -- used
# as a restrained engineering cue instead of a generic icon or illustration.
# No animation, no fill, low-opacity single-colour strokes: it reads as a
# small schematic, not as decoration competing with the text around it.
_LOOP_MOTIF_SVG = """
<svg class="ct-gate-motif" viewBox="0 0 160 40" xmlns="http://www.w3.org/2000/svg"
     preserveAspectRatio="xMidYMid meet">
  <path d="M10,30 C55,6 105,4 150,8"
        fill="none" stroke="currentColor" stroke-width="1.4" opacity=".6"/>
  <path d="M150,8 C105,32 55,34 10,26"
        fill="none" stroke="currentColor" stroke-width="1.4" opacity=".3"/>
</svg>
"""

# Scoped to render only while the gate itself is on screen -- require_password()
# calls st.stop() right after, so nothing else in this run ever has to share
# the page with .block-container pinned to this width, and no later page load
# reaches this function again once _SESSION_KEY is set.
_GATE_CSS = """
<style>
  /* This deployment's Streamlit toolbar (Deploy button, the three-dot
     "..." menu) has no function a first-time, unauthenticated visitor
     needs -- scoped to disappear only while the gate itself is showing,
     the same way everything else in this block is: once _SESSION_KEY is
     set this whole style block is never emitted again. */
  header[data-testid="stHeader"]{ display:none!important; }
  .block-container{
    max-width:480px!important;
    margin:9vh auto 0!important;
    padding:2.35rem 2.4rem 2.1rem!important;
    background:var(--background-color,#fff);
    border:1px solid var(--border-color,#e6e5e0);
    border-radius:.85rem;
    box-shadow:0 1px 2px rgba(15,15,15,.04), 0 12px 32px rgba(15,15,15,.06);
    animation:ctGateIn .35s ease-out both;
  }
  @keyframes ctGateIn{
    from{opacity:0; transform:translateY(6px);}
    to{opacity:1; transform:none;}
  }
  @media (prefers-reduced-motion:reduce){
    .block-container{animation:none;}
  }
  @media (prefers-color-scheme:dark){
    .block-container{
      background:#1c1c1b; border-color:#302f2c;
      box-shadow:0 1px 2px rgba(0,0,0,.3), 0 12px 32px rgba(0,0,0,.4);
    }
  }
  /* Streamlit's radio option markup, inspected live (this version): each
     option is a <label data-testid="stRadioOption"> containing a visually-
     hidden <input>, then a circle indicator div, then the option's own
     stMarkdownContainer -- as SIBLING divs, not nested one inside the
     other. The circle has no testid or stable class of its own, only an
     auto-generated one that is not safe to depend on, so it's targeted by
     position instead: "the div immediately before the text container",
     the same :has()-based relative-targeting this codebase already uses
     in compare_view.py. data-selected="true" (Streamlit's own attribute
     for the active option) drives the pill styling, not :checked --
     confirmed live to be what's actually set here. */
  /* Targeted via the key="ct_gate_lang" container's own st-key-* class
     (see require_password() below), not a hand-opened/closed <div> spanning
     two separate st.markdown() calls -- each st.markdown() call is its own
     sibling node in Streamlit's DOM, so a "<div>" opened in one and closed
     in another never actually wraps the widget rendered in between; the
     first version of this looked unstyled and left-aligned because of
     exactly that (confirmed live). */
  [class*="st-key-ct_gate_lang"]{ display:flex; justify-content:flex-end; margin:0 0 1.5rem; }
  [class*="st-key-ct_gate_lang"] div[data-testid="stRadioGroup"]{ gap:.15rem; }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"]{
    padding:.1rem .55rem; border-radius:999px; margin:0; cursor:pointer;
    font-size:.72rem; font-weight:600; color:var(--secondary-text-color,#8a8a86);
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"][data-selected="true"]{
    background:rgba(42,120,214,.10); color:#2a78d6;
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"] div:has(+ div[data-testid="stMarkdownContainer"]){
    display:none;
  }
  .ct-gate-logo{display:flex; justify-content:center; margin-bottom:.85rem;}
  .ct-gate-logo svg{
    width:44px; height:44px;
    filter:drop-shadow(0 2px 5px rgba(15,15,15,.14));
  }
  .ct-gate-title{
    text-align:center; margin:0 0 .2rem!important;
  }
  .ct-gate-subtitle{
    text-align:center; font-size:.9rem; font-weight:450;
    color:var(--secondary-text-color,#7c7b76); margin:0 0 1.1rem;
  }
  .ct-gate-motif{
    display:block; width:100%; height:32px; margin:0 0 1.3rem;
    color:#2a78d6;
  }
  @media (prefers-color-scheme:dark){
    .ct-gate-motif{ color:#5b9ce6; }
  }
  .ct-gate-intro{
    text-align:center; font-size:.86rem;
    color:var(--secondary-text-color,#7c7b76); margin:0 0 1.3rem;
  }
  div[data-testid="stForm"]{border:none!important; padding:0!important;}
  div[data-testid="stForm"] .stButton>button{width:100%;}
</style>
"""


def require_password() -> None:
    """Blocks the rest of the script with `st.stop()` until the right
    password has been entered THIS session -- a no-op, unconditionally,
    when COMPRESSION_TOOL_PASSWORD is unset. That default matters: every
    local/dev/single-user launch of this app today has never set it and
    must keep working exactly as it does now, unprompted.
    """
    expected = os.environ.get(_PASSWORD_ENV)
    if not expected:
        return
    if st.session_state.get(_SESSION_KEY):
        return

    polish()
    st.markdown(_GATE_CSS, unsafe_allow_html=True)

    with st.container(key="ct_gate_lang"):
        language_picker()
    lang = dashboard_lang()

    def L(key: str) -> str:
        return _T[key][lang]

    st.markdown(f'<div class="ct-gate-logo">{_LOGO_SVG}</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="ct-gate-title">CompressLab</h1>', unsafe_allow_html=True)
    st.markdown(f'<p class="ct-gate-subtitle">{L("subtitle")}</p>', unsafe_allow_html=True)
    st.markdown(_LOOP_MOTIF_SVG, unsafe_allow_html=True)
    st.markdown(f'<p class="ct-gate-intro">{L("intro")}</p>', unsafe_allow_html=True)

    # st.form, not a bare text_input + button: a form submits on Enter from
    # within any of its own text inputs, which a standalone st.text_input
    # next to an unrelated st.button does not -- confirmed live, pressing
    # Enter in the password field previously did nothing until the button
    # was clicked with the mouse.
    with st.form("ct_login", clear_on_submit=False):
        entered = st.text_input(L("password"), type="password", key="_ct_password_attempt")
        submitted = st.form_submit_button(L("enter"), type="primary", use_container_width=True)
    if submitted:
        # hmac.compare_digest, not `==`: a plain string comparison returns
        # as soon as the first differing character is found, so its timing
        # leaks how many leading characters were right -- immaterial for a
        # human mistyping their own password, but there is no reason to
        # accept that leak for free when the constant-time comparison is a
        # one-line stdlib call.
        if hmac.compare_digest(entered, expected):
            st.session_state[_SESSION_KEY] = True
            st.rerun()
        else:
            st.error(L("incorrect"))
    st.stop()
