"""Command-line interface: ``python -m ingestion.synthetic {generate,validate}``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import GeneratorConfig
from .constants import SYNTHETIC_MARKER
from .generator import generate
from .validate import validate_dataset
from .writers import write_dataset


def _build_parser() -> argparse.ArgumentParser:
    defaults = GeneratorConfig()
    parser = argparse.ArgumentParser(
        prog="python -m ingestion.synthetic",
        description=f"Generate or validate clearly fake ({SYNTHETIC_MARKER}) K-12 source extracts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate a synthetic dataset")
    gen.add_argument("--out", default="data/synthetic", help="output directory")
    gen.add_argument("--config", help="JSON file with generator settings (flags override it)")
    gen.add_argument("--seed", type=int)
    gen.add_argument("--tenants", type=int)
    gen.add_argument("--schools-per-tenant", type=int)
    gen.add_argument("--students-per-school", type=int)
    gen.add_argument("--school-year-start", type=int)
    gen.add_argument("--attendance-days", type=int)
    gen.add_argument("--lms-events-per-student", type=int)
    gen.add_argument(
        "--defect-rate",
        type=float,
        help=f"fraction of rows to corrupt on purpose (default {defaults.defect_rate})",
    )

    val = sub.add_parser("validate", help="validate a previously generated dataset")
    val.add_argument("path", help="dataset directory containing manifest.json")
    return parser


def _config_from_args(args: argparse.Namespace) -> GeneratorConfig:
    values: dict = {}
    if args.config:
        values.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
    for name in (
        "seed",
        "tenants",
        "schools_per_tenant",
        "students_per_school",
        "school_year_start",
        "attendance_days",
        "lms_events_per_student",
        "defect_rate",
    ):
        override = getattr(args, name)
        if override is not None:
            values[name] = override
    return GeneratorConfig.from_mapping(values)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "generate":
        try:
            config = _config_from_args(args)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        manifest = write_dataset(generate(config), args.out)
        print(f"{SYNTHETIC_MARKER}: wrote {manifest['total_rows']} rows to {args.out}")
        for entry in manifest["files"]:
            print(f"  {entry['path']:<45} {entry['rows']:>8} rows")
        return 0

    problems = validate_dataset(args.path)
    if problems:
        print(f"INVALID dataset at {args.path}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"OK: {args.path} is a valid {SYNTHETIC_MARKER} dataset")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
