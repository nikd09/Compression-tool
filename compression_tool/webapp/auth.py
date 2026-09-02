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
        # Deliberately NOT evenly ruled: a small deterministic wobble on the
        # spacing is the difference between "a field" and "a chart with the
        # axes taken off", which is the one thing this must not look like.
        y = 18 + i * 21 + 4.0 * math.sin(i * 2.3)
        # Golden-angle-ish stride: decorrelates the traces' phases so no two
        # neighbours start in step, without needing a random seed.
        phase = ((i * 137) % int(_PERIOD)) - _PERIOD / 2
        amp = 0.55 + 0.5 * (0.5 + 0.5 * math.sin(i * 1.7 + 0.4))
        # Brightest through the middle band, fading top and bottom -- depth,
        # and it keeps the densest part of the field behind the card.
        opacity = 0.45 + 0.55 * math.sin(math.pi * (i + 0.5) / rows)
        grouped[i % 6].append(
            f'<g transform="translate({phase:.0f} {y:.1f}) scale(1 {amp:.2f})">'
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

# Both palettes, keyed by Streamlit's OWN active theme (see _active_theme()).
#
# Every colour the gate paints is named here and substituted into the template
# below, rather than left to `var(--some-streamlit-var, fallback)`: measured
# live, Streamlit 1.62 exposes NONE of --background-color, --text-color,
# --primary-color, --border-color or --secondary-text-color on :root, so every
# one of those vars was silently resolving to its hardcoded fallback. That is
# what made the gate's own surfaces stay light while Streamlit's components
# went dark. The values are the app's own palette, straight out of
# .streamlit/config.toml ([theme] and [theme.dark]), so the gate matches the
# app it is standing in front of.
_PALETTES = {
    "light": {
        "CARD_BG": "#ffffff",
        "CARD_BORDER": "#e6e5e0",
        "CARD_SHADOW": "0 1px 2px rgba(15,15,15,.04), 0 12px 32px rgba(15,15,15,.06)",
        "TITLE_TEXT": "#14140f",
        "ACCENT": "#2a78d6",
        "ACCENT_SOFT": "rgba(42,120,214,.10)",
        "MUTED_TEXT": "#7c7b76",
        "PILL_TEXT": "#8a8a86",
        "LABEL_TEXT": "#0b0b0b",
        "INPUT_BG": "#f2f1ed",
        "INPUT_BORDER": "#e1e0d9",
        "INPUT_TEXT": "#0b0b0b",
        "ICON": "#6f6e69",
        "TRACE": "#2a78d6",
        "TRACE_OPACITY": ".28",
        "LOGO_SHADOW": "0 2px 5px rgba(15,15,15,.14)",
    },
    "dark": {
        # A step lighter than the page (#0d0d0d) so the card lifts off it,
        # which is the same relationship [theme.dark]'s secondaryBackgroundColor
        # already has to its backgroundColor elsewhere in the app.
        "CARD_BG": "#1a1a19",
        "CARD_BORDER": "#2c2c2a",
        "CARD_SHADOW": "0 1px 2px rgba(0,0,0,.35), 0 12px 32px rgba(0,0,0,.45)",
        "TITLE_TEXT": "#ffffff",
        "ACCENT": "#3987e5",
        "ACCENT_SOFT": "rgba(57,135,229,.18)",
        "MUTED_TEXT": "#a8a7a1",
        "PILL_TEXT": "#a8a7a1",
        "LABEL_TEXT": "#ffffff",
        # One step lighter again, so the field reads as an input against the
        # card rather than as a hole punched in it.
        "INPUT_BG": "#232322",
        "INPUT_BORDER": "#3a3a37",
        "INPUT_TEXT": "#ffffff",
        "ICON": "#a8a7a1",
        "TRACE": "#5f9fe4",
        "TRACE_OPACITY": ".22",
        "LOGO_SHADOW": "0 2px 6px rgba(0,0,0,.5)",
    },
}


def _active_theme() -> str:
    """'light' or 'dark' -- whichever Streamlit is ACTUALLY painting in.

    This used to key off `@media (prefers-color-scheme: dark)`, which is the
    BROWSER's preference. Streamlit's theme does not have to agree with it,
    and when it did not -- Streamlit dark, browser light -- the media query
    never fired, so the gate kept a white card and dark title text while
    Streamlit painted the page and every widget it owns dark. That is the
    reported "login screen breaks after refreshing in dark mode", reproduced
    by forcing `--theme.base dark` against a light browser.

    Note that `st.context.theme` does NOT solve it on its own: measured on
    Streamlit 1.62, it reports the browser preference, so on that same forced
    -dark server it still answers "light". Two signals are needed, and either
    one being dark means dark is what gets painted:

      * theme.base -- the configured theme. 'dark' here (config.toml or
        --theme.base) means dark regardless of the browser.
      * context.theme.type -- the browser preference, which is what selects
        the [theme.dark] palette this app declares when the config itself is
        light.

    Both are re-read every run, including the one after a full refresh.
    """
    base = browser = None
    try:
        base = st.get_option("theme.base")
    except Exception:
        pass
    try:
        browser = getattr(st.context.theme, "type", None)
    except Exception:
        pass
    return "dark" if "dark" in (base, browser) else "light"


# Scoped to render only while the gate itself is on screen -- require_password()
# calls st.stop() right after, so nothing else in this run ever has to share
# the page with .block-container pinned to this width, and no later page load
# reaches this function again once _SESSION_KEY is set.
_GATE_CSS_TEMPLATE = """
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
    background:__CARD_BG__;
    border:1px solid __CARD_BORDER__;
    border-radius:.85rem;
    box-shadow:__CARD_SHADOW__;
    animation:ctGateIn .35s ease-out both;
  }
  @keyframes ctGateIn{
    from{opacity:0; transform:translateY(6px);}
    to{opacity:1; transform:none;}
  }
  /* The backdrop field. Pinned to the viewport and below the card's own
     z-index:1, so it fills the page behind it without being clipped by the
     centring column; pointer-events:none keeps every click going to the
     form. The mask is an annulus, not a plain centre-bright ellipse: fully
     clear through the middle where the card sits and immediately around
     it, rising to full only out in the surrounding field, then dissolving
     again by the window edges. So the card is never competing with
     linework right at its border, and the page still reads as having
     something quietly running on it.

     The two ramps are deliberately long and the stops few: a tight ramp
     makes the falloff itself legible as a ring, which is a decoration in
     its own right and exactly the thing being avoided. The ellipse is
     sized so the outer ramp finishes at the viewport edge rather than past
     it -- an earlier, larger ellipse put the edges only ~60% of the way
     down the ramp, so the traces ran right off the sides of the window
     instead of dissolving before them (confirmed live). */
  .ct-gate-bg{
    position:fixed; inset:0; width:100vw; height:100vh;
    z-index:0; pointer-events:none;
    color:__TRACE__; opacity:__TRACE_OPACITY__;
    -webkit-mask-image:radial-gradient(ellipse 54% 54% at 50% 44%, rgba(0,0,0,0) 30%, rgba(0,0,0,.85) 72%, rgba(0,0,0,0) 100%);
    mask-image:radial-gradient(ellipse 54% 54% at 50% 44%, rgba(0,0,0,0) 30%, rgba(0,0,0,.85) 72%, rgba(0,0,0,0) 100%);
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
  /* Both states get an explicit colour: the unselected one used to inherit
     Streamlit's secondary text colour, which is how "DE" ended up invisible
     on the dark card. */
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"]{
    padding:.1rem .55rem; border-radius:999px; margin:0; cursor:pointer;
    font-size:.72rem; font-weight:600;
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"] p{
    color:__PILL_TEXT__!important;
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"][data-selected="true"]{
    background:__ACCENT_SOFT__;
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"][data-selected="true"] p{
    color:__ACCENT__!important;
  }
  [class*="st-key-ct_gate_lang"] label[data-testid="stRadioOption"] div:has(+ div[data-testid="stMarkdownContainer"]){
    display:none;
  }
  /* Logo and wordmark are one group: the gap between them is deliberately
     tighter than the gap below the subtitle, so the three lines read as
     mark-then-name rather than as three evenly spaced items. */
  .ct-gate-logo{display:flex; justify-content:center; margin-bottom:.55rem;}
  .ct-gate-logo svg{
    width:50px; height:50px;
    filter:drop-shadow(__LOGO_SHADOW__);
  }
  .ct-gate-title{
    text-align:center; padding:0!important; margin:0 0 .3rem!important;
    color:__TITLE_TEXT__!important;
  }
  /* Compress + Lab, the same split the sidebar wordmark (logo.svg) uses,
     with "Lab" in the app's own brand blue -- #2a78d6, exactly the fill
     logo.svg gives it, and its [theme.dark] counterpart #3987e5 on the dark
     card, which is the light/dark blue pair used everywhere else in this
     app (see app.py's nav CSS). */
  .ct-gate-title .ct-gate-lab{ color:__ACCENT__; }
  /* Streamlit puts its own "link to heading" anchor INSIDE the <h1>, as an
     inline-flex span after the text (16px icon + 8px gap, measured live).
     It is part of the centred line box, so the visible wordmark was being
     pushed 12px left of true centre -- confirmed by measuring the rendered
     pixels: the logo tile and the subtitle both centred to within 0.5px
     while the wordmark alone sat at -12.5px. That, not the logo, is what
     made the group look off-centre. The anchor is of no use on a login
     screen with nothing to link to, so it comes out of the flow entirely. */
  .ct-gate-title [data-testid="stHeaderActionElements"]{ display:none!important; }
  .ct-gate-subtitle{
    text-align:center; font-size:.9rem; font-weight:450;
    color:__MUTED_TEXT__; margin:0 0 1.35rem;
  }
  .ct-gate-intro{
    text-align:center; font-size:.86rem;
    color:__MUTED_TEXT__; margin:0 0 1.3rem;
  }
  div[data-testid="stForm"]{border:none!important; padding:0!important;}
  div[data-testid="stForm"] .stButton>button{width:100%;}
  /* The password field, its label, and the show/hide eye, all painted
     explicitly rather than inherited. Selectors taken from the live DOM:
     the bordered box is stTextInputRootElement, the field itself is
     stTextInputField, and the eye is a plain <button aria-label="Show
     password"> inside the box whose icon draws in currentColor. */
  div[data-testid="stForm"] label[data-testid="stWidgetLabel"] p{
    color:__LABEL_TEXT__!important;
  }
  div[data-testid="stTextInputRootElement"]{
    background:__INPUT_BG__!important;
    border:1px solid __INPUT_BORDER__!important;
  }
  div[data-testid="stTextInputRootElement"]:focus-within{
    border-color:__ACCENT__!important;
  }
  input[data-testid="stTextInputField"]{
    background:transparent!important; color:__INPUT_TEXT__!important;
    -webkit-text-fill-color:__INPUT_TEXT__!important;
  }
  div[data-testid="stTextInputRootElement"] button{
    color:__ICON__!important; background:transparent!important;
  }
  div[data-testid="stTextInputRootElement"] button svg{ fill:currentColor; }
  /* The submit button is the app's primary blue in both themes; stated here
     so it does not depend on Streamlit's primaryColor resolving. */
  div[data-testid="stForm"] .stButton>button[kind="primaryFormSubmit"]{
    background:__ACCENT__!important; border-color:__ACCENT__!important;
    color:#ffffff!important;
  }
</style>
""".replace("__PERIOD__", f"{_PERIOD:.0f}")


def _gate_css(theme: str) -> str:
    """The gate's stylesheet, with the active theme's palette substituted in."""
    css = _GATE_CSS_TEMPLATE
    for token, value in _PALETTES[theme].items():
        css = css.replace(f"__{token}__", value)
    return css


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
    st.markdown(_gate_css(_active_theme()), unsafe_allow_html=True)
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
        st.markdown(
            '<h1 class="ct-gate-title">Compress<span class="ct-gate-lab">Lab</span></h1>',
            unsafe_allow_html=True)
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
