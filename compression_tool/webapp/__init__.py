"""
compression_tool.webapp
========================
The Streamlit UI: Ingest / Results / Compare / Config, steps 3-5 of
HANDOFF.md's build order.

    streamlit run -m compression_tool.webapp.app
    # or, once installed:
    compression-tool-webapp

Every view is a thin layer over the public API (`preview`, `ingest`,
`knowledge_base`, `dashboard_data`) -- nothing here recomputes a metric or
reaches into a record's internals that the rest of the package does not
already expose.
"""
