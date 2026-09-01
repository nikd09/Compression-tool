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
import math
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

# One waveform period, in viewBox units. The drift animation translates by
# exactly this distance, so it is substituted into the CSS below rather than
# written there by hand -- the seamless loop depends on the two agreeing, and
# a hand-copied number is exactly the kind of thing that silently stops
# matching later. The span drawn either side is a whole multiple of it, for
# the same reason (see _trace_path).
_PERIOD = 300.0
_SPAN = (-600.0, 1800.0)


def _trace_path(x0: float, x1: float, period: float, per_period: int) -> str:
    """One acquisition trace: a multi-harmonic (so it reads as a recorded
    signal rather than a decorative sine) periodic waveform, sampled as a
    polyline around y=0.

    Periodicity is the whole point: the drift animation below slides each
    trace by exactly one `period`, so the loop is seamless only if the
    geometry repeats exactly over that distance. It does -- every harmonic
    here completes a whole number of cycles per period, and the sample grid
    is period/per_period, so shifting by one period maps sample i onto
    sample i+per_period exactly. Rounding x for compactness is safe for the
    same reason: the offset is a whole number of units, so the rounded grid
    shifts onto itself.
    """
    n = int(round((x1 - x0) / period * per_period))
    dx = (x1 - x0) / n
    pts = []
    for i in range(n + 1):
        x = x0 + i * dx
        t = 2.0 * math.pi * x / period
        y = (7.5 * math.sin(t)
             + 3.0 * math.sin(2.0 * t + 0.9)
             + 1.6 * math.sin(3.0 * t + 2.1))
        pts.append(f"{x:.1f} {y:.2f}")
    return "M" + " L".join(pts)


def _backdrop_svg() -> str:
    """The login backdrop: stacked measurement traces, drifting.

    Built once at import time as static markup -- no JavaScript, no runtime
    work, nothing fetched. Everything that moves, moves via a CSS transform
    on one of seven group elements (six drift groups plus the whole field's
    slow vertical breathing), never by touching path geometry, so the
    browser composites it instead of re-rasterising 22 polylines a frame.

    The traces are distributed round-robin across the six drift groups, so
    vertically adjacent traces are always in different groups and therefore
    drift at different rates: the field keeps reorganising against itself
    instead of sliding as one rigid pattern, which is what stops it reading
    as a looping decoration.
    """
    d = _trace_path(_SPAN[0], _SPAN[1], _PERIOD, 22)
    rows = 26
    grouped: list[list[str]] = [[] for _ in range(6)]
    for i in range(rows):
        y = 18 + i * 21
        # Golden-angle-ish stride: decorrelates the traces' phases so no two
        # neighbours start in step, without needing a random seed.
        phase = ((i * 137) % int(_PERIOD)) - _PERIOD / 2
        amp = 0.55 + 0.5 * (0.5 + 0.5 * math.sin(i * 1.7 + 0.4))
        # Brightest through the middle band, fading top and bottom -- depth,
        # and it keeps the densest part of the field behind the card.
        opacity = 0.45 + 0.55 * math.sin(math.pi * (i + 0.5) / rows)
        grouped[i % 6].append(
            f'<g transform="translate({phase:.0f} {y}) scale(1 {amp:.2f})">'
            f'<path d="{d}" fill="none" stroke="currentColor" stroke-width="1"'
            f' stroke-opacity="{opacity:.2f}" vector-effect="non-scaling-stroke"/>'
            "</g>"
        )
    groups = "".join(
        f'<g class="ct-bg-drift ct-bg-d{n + 1}">{"".join(rows)}</g>'
        for n, rows in enumerate(grouped)
    )
    return (
        '<svg class="ct-gate-bg" viewBox="0 0 1200 600" '
        'preserveAspectRatio="xMidYMid slice" aria-hidden="true" '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<g class="ct-bg-field">{groups}</g></svg>'
    )


_BACKDROP_SVG = _backdrop_svg()

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
  /* The card is the key="ct_gate_card" container, NOT .block-container
     itself: the backdrop has to be a sibling that the card paints over,
     and anything rendered into .block-container while .block-container IS
     the card would instead be clipped inside it. So .block-container is
     now just the centring column -- transparent, no padding of its own. */
  .block-container{
    max-width:480px!important;
    margin:9vh auto 0!important;
    padding:0!important;
    position:relative;
  }
  [class*="st-key-ct_gate_card"]{
    position:relative; z-index:1;
    padding:2.35rem 2.4rem 2.1rem;
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
  @media (prefers-color-scheme:dark){
    [class*="st-key-ct_gate_card"]{
      background:#1c1c1b; border-color:#302f2c;
      box-shadow:0 1px 2px rgba(0,0,0,.3), 0 12px 32px rgba(0,0,0,.4);
    }
  }
  /* The backdrop field. Pinned to the viewport and below the card's own
     z-index:1, so it fills the page behind it without being clipped by the
     centring column; pointer-events:none keeps every click going to the
     form. Masked to an ellipse so the traces dissolve well before the
     window edges rather than ending at them. */
  .ct-gate-bg{
    position:fixed; inset:0; width:100vw; height:100vh;
    z-index:0; pointer-events:none;
    color:#2a78d6; opacity:.3;
    -webkit-mask-image:radial-gradient(ellipse 64% 64% at 50% 45%, #000 18%, rgba(0,0,0,0) 74%);
    mask-image:radial-gradient(ellipse 64% 64% at 50% 45%, #000 18%, rgba(0,0,0,0) 74%);
  }
  .ct-bg-field{
    transform-box:view-box; transform-origin:50% 50%;
    animation:ctBgBreathe 26s ease-in-out infinite;
  }
  .ct-bg-drift{ transform-box:view-box; }
  /* Six rates, three of them running the other way. All translate by
     exactly one waveform period, so every one of them loops seamlessly
     however long it runs, and their least common multiple is long enough
     that the field never visibly repeats as a whole. */
  .ct-bg-d1{ animation:ctBgDriftA  68s linear infinite; }
  .ct-bg-d2{ animation:ctBgDriftB  91s linear infinite; }
  .ct-bg-d3{ animation:ctBgDriftA 113s linear infinite; }
  .ct-bg-d4{ animation:ctBgDriftB  79s linear infinite; }
  .ct-bg-d5{ animation:ctBgDriftA 102s linear infinite; }
  .ct-bg-d6{ animation:ctBgDriftB 127s linear infinite; }
  @keyframes ctBgDriftA{ from{transform:translateX(0);} to{transform:translateX(-__PERIOD__px);} }
  @keyframes ctBgDriftB{ from{transform:translateX(0);} to{transform:translateX(__PERIOD__px);} }
  /* The one gesture that is actually about compression: the whole field
     settles and releases, very slowly, like a specimen under a long cyclic
     load. Small enough (5.5%) to register as breathing, not as zooming. */
  @keyframes ctBgBreathe{
    0%,100%{ transform:scaleY(1); }
    50%{ transform:scaleY(.945); }
  }
  @media (prefers-color-scheme:dark){
    .ct-gate-bg{ color:#5f9fe4; opacity:.22; }
  }
  /* Static fallback: the field is still there and still composed, it just
     stops moving -- nothing disappears or reflows. */
  @media (prefers-reduced-motion:reduce){
    [class*="st-key-ct_gate_card"]{ animation:none; }
    .ct-gate-bg *{ animation:none!important; }
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
  .ct-gate-intro{
    text-align:center; font-size:.86rem;
    color:var(--secondary-text-color,#7c7b76); margin:0 0 1.3rem;
  }
  div[data-testid="stForm"]{border:none!important; padding:0!important;}
  div[data-testid="stForm"] .stButton>button{width:100%;}
</style>
""".replace("__PERIOD__", f"{_PERIOD:.0f}")


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
    # Rendered before the card, and a sibling of it rather than a child, so
    # the card paints over a field that fills the whole window.
    st.markdown(_BACKDROP_SVG, unsafe_allow_html=True)

    with st.container(key="ct_gate_card"):
        with st.container(key="ct_gate_lang"):
            language_picker()
        lang = dashboard_lang()

        def L(key: str) -> str:
            return _T[key][lang]

        st.markdown(f'<div class="ct-gate-logo">{_LOGO_SVG}</div>', unsafe_allow_html=True)
        st.markdown('<h1 class="ct-gate-title">CompressLab</h1>', unsafe_allow_html=True)
        st.markdown(f'<p class="ct-gate-subtitle">{L("subtitle")}</p>', unsafe_allow_html=True)
        st.markdown(f'<p class="ct-gate-intro">{L("intro")}</p>', unsafe_allow_html=True)

        # st.form, not a bare text_input + button: a form submits on Enter from
        # within any of its own text inputs, which a standalone st.text_input
        # next to an unrelated st.button does not -- confirmed live, pressing
        # Enter in the password field previously did nothing until the button
        # was clicked with the mouse.
        with st.form("ct_login", clear_on_submit=False):
            entered = st.text_input(L("password"), type="password", key="_ct_password_attempt")
            submitted = st.form_submit_button(
                L("enter"), type="primary", use_container_width=True)
        # Inside the card, so a wrong password reports itself within the
        # same panel rather than as a banner floating under it.
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
