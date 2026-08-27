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

import streamlit as st

_PASSWORD_ENV = "COMPRESSION_TOOL_PASSWORD"
_SESSION_KEY = "_ct_authenticated"


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

    st.title("CompressLab")
    st.caption(
        "This deployment is password-protected. Ask whoever administers "
        "it for the password if you do not have it."
    )
    entered = st.text_input("Password", type="password", key="_ct_password_attempt")
    submitted = st.button("Enter", type="primary")
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
            st.error("Incorrect password.")
    st.stop()
