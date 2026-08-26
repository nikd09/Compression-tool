"""
compression_tool
================
Ingestion, metrics and persistence for load-controlled cyclic / multi-stage
compression tests exported from a Zwick Z100.

    from compression_tool import Config, ingest, preview

    preview(["Mehrstufiger.xlsx"])              # look before committing
    ingest(["Mehrstufiger.xlsx"], "./data", material="PEEK-GF30")

The calculation engine lives in `compression_tool.core` and is the validated
reference implementation; everything else is built around it.
"""

from .audit import list_entries as list_audit_entries
from .core import Config, TestData, analyse_test, detect_format, load_tests, segment_cycles
from .material_export import export_material
from .material_registry import add_material, load_materials
from .persistence import Workspace
from .pipeline import IngestResult, SpecimenResult, ingest, preview, preview_dashboard_data, rebuild_index
from .reports_overview import build_overview
from .schema import SCHEMA_VERSION

__all__ = [
    "Config",
    "TestData",
    "Workspace",
    "IngestResult",
    "SpecimenResult",
    "analyse_test",
    "detect_format",
    "load_tests",
    "segment_cycles",
    "export_material",
    "add_material",
    "load_materials",
    "build_overview",
    "list_audit_entries",
    "ingest",
    "preview",
    "preview_dashboard_data",
    "rebuild_index",
    "SCHEMA_VERSION",
]

__version__ = "0.2.0"
