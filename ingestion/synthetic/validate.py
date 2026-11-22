"""Independent validation of a generated synthetic dataset.

Used by the CLI (``validate``), by CI, and by the test-suite. It re-reads the files from disk
instead of trusting in-memory state, so it also catches tampering or accidental edits.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .constants import MANIFEST_FILENAME, SAFE_EMAIL_DOMAIN, SYNTHETIC_MARKER
from .spec import ENTITIES, ENTITIES_BY_KEY, SCHOOLS, STUDENT_CHILD_KEYS, STUDENTS, EntitySpec
from .writers import sha256_of

# Patterns that must never appear in synthetic output. The generator emits none of these, so a
# match means something real (or realistic-looking) leaked in.
_PII_PATTERNS = {
    "ssn-like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone-like": re.compile(r"(?<!\d)\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"),
    "email-like": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
}


def read_rows(spec: EntitySpec, path: Path) -> list[dict[str, Any]]:
    if spec.fmt == "csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _scan_for_pii(path: Path) -> list[str]:
    problems = []
    text = path.read_text(encoding="utf-8")
    for label, pattern in _PII_PATTERNS.items():
        for match in pattern.finditer(text):
            if label == "email-like" and match.group(0).endswith("@" + SAFE_EMAIL_DOMAIN):
                continue
            problems.append(f"{path.name}: {label} pattern found: {match.group(0)!r}")
            break
    return problems


def validate_dataset(out_dir: str | Path) -> list[str]:
    """Return a list of problems found; an empty list means the dataset is valid."""
    out = Path(out_dir)
    errors: list[str] = []
    manifest_path = out / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return [f"missing {MANIFEST_FILENAME} in {out}"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("synthetic_marker") != SYNTHETIC_MARKER:
        errors.append("manifest is not marked SYNTHETIC_TEST_DATA")

    listed = {f["path"]: f for f in manifest.get("files", [])}
    for spec in ENTITIES:
        if spec.relative_path not in listed:
            errors.append(f"manifest does not list {spec.relative_path}")

    tables: dict[str, list[dict[str, Any]]] = {}
    for rel, meta in listed.items():
        path = out / rel
        spec = next((s for s in ENTITIES if s.relative_path == rel), None)
        if spec is None:
            errors.append(f"unexpected file in manifest: {rel}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {rel}")
            continue
        if sha256_of(path) != meta["sha256"]:
            errors.append(f"checksum mismatch: {rel}")
        rows = read_rows(spec, path)
        tables[spec.key] = rows
        if len(rows) != meta["rows"]:
            errors.append(f"row count mismatch in {rel}: {len(rows)} != {meta['rows']}")
        unmarked = sum(1 for r in rows if r.get("synthetic_marker") != SYNTHETIC_MARKER)
        if unmarked:
            errors.append(f"{rel}: {unmarked} rows missing synthetic_marker")
        if rows and tuple(rows[0]) != spec.fields:
            errors.append(f"{rel}: columns do not match spec")
        errors.extend(_scan_for_pii(path))

    errors.extend(_check_references(tables))
    return errors


def _check_references(tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Every child row must point at a student/school that exists *in the same tenant*."""
    errors: list[str] = []
    if STUDENTS.key not in tables or SCHOOLS.key not in tables:
        return errors
    students = {(r["tenant_id"], r["student_id"], r["school_id"]) for r in tables[STUDENTS.key]}
    schools = {(r["tenant_id"], r["school_id"]) for r in tables[SCHOOLS.key]}

    for row in tables[STUDENTS.key]:
        if (row["tenant_id"], row["school_id"]) not in schools:
            errors.append(f"student {row['student_id']} references unknown school")
            break

    for key in STUDENT_CHILD_KEYS:
        spec = ENTITIES_BY_KEY[key]
        orphans = [
            r
            for r in tables.get(key, [])
            if (r["tenant_id"], r["student_id"], r["school_id"]) not in students
        ]
        if orphans:
            errors.append(f"{spec.key}: {len(orphans)} rows reference an unknown student/tenant")
    return errors
