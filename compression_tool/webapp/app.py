"""Entry point.

    streamlit run compression_tool/webapp/app.py

Absolute imports only, deliberately: Streamlit's script runner execs this
file directly (even under `streamlit run -m`) without setting `__package__`,
so `from . import ...` fails here even though it works everywhere else in
the package. `compression_tool` just needs to be importable -- installed, or
this repo's root on PYTHONPATH.
"""

from __future__ import annotations

import streamlit as st

from compression_tool.webapp import compare_view, config_view, ingest_view, results_view

st.set_page_config(page_title="Compression Tool", page_icon="📊", layout="wide")

VIEWS = {
    "Ingest": ingest_view.render,
    "Results": results_view.render,
    "Compare": compare_view.render,
    "Config": config_view.render,
}


def main() -> None:
    st.sidebar.title("Compression Tool")
    page = st.sidebar.radio("View", list(VIEWS), label_visibility="collapsed")
    st.sidebar.divider()
    VIEWS[page]()


if __name__ == "__main__":
    main()
