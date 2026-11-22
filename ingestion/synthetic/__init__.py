"""Synthetic, clearly fake K-12 data for development, tests and demos.

No real student data may ever be committed to this repository; everything here is generated.
See ``docs/data-dictionary.md`` for the shape of each extract.
"""

from .config import GeneratorConfig
from .constants import SCHEMA_VERSION, SYNTHETIC_MARKER
from .generator import GeneratedData, generate
from .validate import validate_dataset
from .writers import write_dataset

__all__ = [
    "SCHEMA_VERSION",
    "SYNTHETIC_MARKER",
    "GeneratedData",
    "GeneratorConfig",
    "generate",
    "validate_dataset",
    "write_dataset",
]
