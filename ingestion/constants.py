"""Constants for the ingestion framework."""

from .synthetic.constants import SYNTHETIC_MARKER

#: canonical field that carries the synthetic marker on every record of a synthetic source
SYNTHETIC_MARKER_FIELD = "synthetic_marker"

#: value of the Bronze ``_data_origin`` column for synthetic sources
DATA_ORIGIN_SYNTHETIC = SYNTHETIC_MARKER
DATA_ORIGIN_PRODUCTION = "PRODUCTION"

#: number of records sampled to infer the observed schema of API-style sources
SCHEMA_SAMPLE_SIZE = 50

__all__ = [
    "DATA_ORIGIN_PRODUCTION",
    "DATA_ORIGIN_SYNTHETIC",
    "SCHEMA_SAMPLE_SIZE",
    "SYNTHETIC_MARKER",
    "SYNTHETIC_MARKER_FIELD",
]
