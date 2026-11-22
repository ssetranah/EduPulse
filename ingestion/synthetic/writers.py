"""Write generated data to disk as source-system style files, plus a checksummed manifest."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .constants import BANNER_FILENAME, MANIFEST_FILENAME, SCHEMA_VERSION, SYNTHETIC_MARKER
from .generator import GeneratedData
from .spec import ENTITIES, EntitySpec

_BANNER = f"""\
{SYNTHETIC_MARKER}
=====================
Everything in this directory was produced by EduPulse AI's synthetic-data generator.
All students, schools, names, scores and events are FICTIONAL. Every record carries
synthetic_marker = "{SYNTHETIC_MARKER}". There is no real student data here.
Any resemblance to a real person or institution is coincidental.
Do not combine this data with, or load it into, any system holding real student records.
"""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_rows(spec: EntitySpec, rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if tuple(row) != spec.fields:
            raise ValueError(f"{spec.key}: row columns {tuple(row)} do not match spec")
    if spec.fmt == "csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=spec.fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    elif spec.fmt == "jsonl":
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    else:  # pragma: no cover - guarded by the spec table
        raise ValueError(f"unsupported format {spec.fmt!r}")


def write_dataset(data: GeneratedData, out_dir: str | Path) -> dict[str, Any]:
    """Write all entities under ``out_dir`` and return (and persist) the manifest.

    The manifest deliberately contains no timestamps or host details, so identical config and
    seed produce an identical manifest -- which makes reproducibility checkable by hash.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = []
    for spec in ENTITIES:
        rows = data.tables[spec.key]
        path = out / spec.relative_path
        _write_rows(spec, rows, path)
        files.append(
            {
                "path": spec.relative_path,
                "source_system": spec.source_system,
                "entity": spec.name,
                "format": spec.fmt,
                "rows": len(rows),
                "sha256": sha256_of(path),
            }
        )

    manifest = {
        "synthetic_marker": SYNTHETIC_MARKER,
        "schema_version": SCHEMA_VERSION,
        "generator": "ingestion.synthetic",
        "config": data.config.as_dict(),
        "defects_injected": data.defects,
        "total_rows": sum(f["rows"] for f in files),
        "files": files,
    }
    (out / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / BANNER_FILENAME).write_text(_BANNER, encoding="utf-8")
    return manifest
