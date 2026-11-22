"""Command line interface.

    edupulse ingest --source <name|all> --config <path> [options]
    edupulse verify-bronze --root <dir>

Exit codes: 0 success, 1 one or more sources/entities failed, 2 configuration or usage error,
3 completed but with rejected records under ``--strict``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .bronze import verify_bronze
from .config import load_config
from .errors import IngestionError
from .models import Status
from .reporting import render_text_summary
from .runner import IngestionRunner

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_REJECTS = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edupulse", description="EduPulse AI command line (synthetic-data MVP)."
    )
    parser.add_argument("--version", action="version", version=f"edupulse {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="run configured source connectors into Bronze")
    ing.add_argument("--source", required=True, help="source name from the config, or 'all'")
    ing.add_argument("--config", required=True, help="path to the ingestion YAML config")
    ing.add_argument("--output-dir", help="storage root (default: storage.root from the config)")
    ing.add_argument("--run-id", help="explicit run id (letters, digits, . _ -)")
    ing.add_argument(
        "--dry-run", action="store_true", help="validate and count; write no Bronze/state"
    )
    ing.add_argument("--strict", action="store_true", help="exit 3 if any record was rejected")
    ing.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="override a config value, e.g. sources.mock_sis.mock.as_of=2026-01-04T00:00:00",
    )
    ing.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ing.add_argument("--quiet", action="store_true", help="no console log lines (file log is kept)")

    ver = sub.add_parser("verify-bronze", help="verify checksums of committed Bronze batches")
    ver.add_argument("--root", required=True, help="storage root containing bronze/")
    return parser


def _ingest(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config, overrides=args.override)
        runner = IngestionRunner(
            config,
            config_path=str(args.config),
            output_dir=args.output_dir,
            run_id=args.run_id,
            dry_run=args.dry_run,
            env=os.environ,
            log_level=args.log_level,
            console=sys.stderr,
            quiet=args.quiet,
        )
        names = None if args.source == "all" else [s.strip() for s in args.source.split(",")]
        report = runner.run(names)
    except IngestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print(render_text_summary(report))
    print(f"\nrun report:        {runner.run_dir / 'run_report.json'}")
    print(f"validation report: {runner.run_dir / 'validation_report.json'}")
    if report.status in (Status.FAILED.value, Status.PARTIAL_FAILURE.value):
        return EXIT_FAILED
    if args.strict and report.totals.rejected:
        return EXIT_REJECTS
    return EXIT_OK


def _verify(args: argparse.Namespace) -> int:
    problems = verify_bronze(Path(args.root))
    if problems:
        for line in problems:
            print(f"PROBLEM: {line}")
        return EXIT_FAILED
    print("bronze verified: all batch checksums and row counts match")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        return _ingest(args)
    return _verify(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
