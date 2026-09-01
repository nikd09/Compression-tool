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
    "eyebrow": {"en": "Multi-stage compression test analysis",
        "de": "Analyse mehrstufiger Druckversuche"},
    "protected": {
        "en": "This deployment is password-protected. Ask whoever "
              "administers it for the password if you do not have it.",
        "de": "Dieser Zugang ist passwortgeschützt. Bei der zuständigen "
              "Administration nach dem Passwort fragen, falls es nicht "
              "bekannt ist."},
    "password": {"en": "Password", "de": "Passwort"},
    "enter": {"en": "Enter", "de": "Anmelden"},
    "incorrect": {"en": "Incorrect password.", "de": "Falsches Passwort."},
}

# Scoped to render only while the gate itself is on screen -- require_password()
# calls st.stop() right after, so nothing else in this run ever has to share
# the page with .block-container pinned to this width, and no later page load
# reaches this function again once _SESSION_KEY is set.
_GATE_CSS = """
<style>
  .block-container{
    max-width:440px!important;
    margin:7vh auto 0!important;
    padding:2.5rem 2.35rem 2.2rem!important;
    background:var(--background-color,#fcfcfb);
    border:1px solid var(--border-color,#e1e0d9);
    border-radius:1.1rem;
    box-shadow:0 24px 64px rgba(11,11,11,.10), 0 2px 10px rgba(11,11,11,.06);
    position:relative; overflow:hidden;
    animation:ctGateIn .6s cubic-bezier(.22,.61,.36,1) both;
  }
  .block-container::before{
    content:""; position:absolute; top:0; left:0; right:0; height:3px;
    background:linear-gradient(90deg,#2a78d6,#7ab3f2,#2a78d6);
    background-size:220% 100%; animation:ctGateShimmer 3.4s linear infinite;
  }
  @keyframes ctGateIn{
    from{opacity:0; transform:translateY(16px) scale(.97);}
    to{opacity:1; transform:none;}
  }
  @keyframes ctGateShimmer{
    from{background-position:0% 0;} to{background-position:220% 0;}
  }
  @media (prefers-reduced-motion:reduce){
    .block-container{animation:none;}
    .block-container::before{animation:none;}
  }
  @media (prefers-color-scheme:dark){
    .block-container{
      background:#1a1a19; border-color:#2c2c2a;
      box-shadow:0 24px 64px rgba(0,0,0,.55), 0 2px 10px rgba(0,0,0,.35);
    }
  }
  .ct-gate-lang{display:flex; justify-content:flex-end; margin:-.6rem 0 .8rem;}
  .ct-gate-logo{display:flex; justify-content:center; margin-bottom:.9rem;}
  .ct-gate-logo svg{
    width:60px; height:60px;
    filter:drop-shadow(0 6px 16px rgba(42,120,214,.35));
  }
  .ct-gate-eyebrow{
    text-align:center; font-size:.68rem; font-weight:700; letter-spacing:.11em;
    text-transform:uppercase; color:var(--secondary-text-color,#898781);
    margin:0 0 1.3rem;
  }
  .ct-gate-title{
    text-align:center; margin:0 0 .15rem!important;
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

    st.markdown('<div class="ct-gate-lang">', unsafe_allow_html=True)
    language_picker()
    st.markdown("</div>", unsafe_allow_html=True)
    lang = dashboard_lang()

    def L(key: str) -> str:
        return _T[key][lang]

    st.markdown(f'<div class="ct-gate-logo">{_LOGO_SVG}</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="ct-gate-title">CompressLab</h1>', unsafe_allow_html=True)
    st.markdown(f'<p class="ct-gate-eyebrow">{L("eyebrow")}</p>', unsafe_allow_html=True)
    st.caption(L("protected"))

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
