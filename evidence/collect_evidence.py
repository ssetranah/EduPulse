#!/usr/bin/env python3
"""Collect machine-readable evidence for a milestone into ``evidence/phase-<N>/``.

Runs the test-suite (per marker), lint, the synthetic generator (twice, to prove determinism)
and compose validation, then writes ``evidence.json`` plus supporting artifacts. Anything that
cannot run in the current environment is recorded as ``not_run`` with a reason -- never faked.

Usage:  python evidence/collect_evidence.py --phase 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ingestion.synthetic import (  # noqa: E402
    SYNTHETIC_MARKER,
    GeneratorConfig,
    generate,
    validate_dataset,
    write_dataset,
)

MARKERS = ["unit", "integration", "data_quality"]
SAMPLE_ROWS = 5


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False, **kwargs)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_results(out: Path) -> dict:
    results: dict = {}
    for marker in [*MARKERS, None]:
        label = marker or "all"
        junit = out / f"junit-{label}.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}"]
        if marker:
            cmd += ["-m", marker]
        started = time.perf_counter()
        proc = run(cmd)
        elapsed = round(time.perf_counter() - started, 2)
        suite = ET.parse(junit).getroot()  # noqa: S314 - JUnit file written by our own pytest run
        suite = suite if suite.tag == "testsuite" else suite[0]
        results[label] = {
            "tests": int(suite.get("tests", 0)),
            "failures": int(suite.get("failures", 0)),
            "errors": int(suite.get("errors", 0)),
            "skipped": int(suite.get("skipped", 0)),
            "seconds": elapsed,
            "passed": proc.returncode == 0,
        }
        if marker:  # keep one junit file; per-marker ones are summarized above
            junit.unlink(missing_ok=True)
    return results


def lint_results() -> dict:
    ruff = shutil.which("ruff")
    if not ruff:
        return {"status": "not_run", "reason": "ruff not installed"}
    check = run([ruff, "check", "."])
    fmt = run([ruff, "format", "--check", "."])
    return {
        "status": "pass" if check.returncode == 0 and fmt.returncode == 0 else "fail",
        "ruff_version": run([ruff, "--version"]).stdout.strip(),
        "check_ok": check.returncode == 0,
        "format_ok": fmt.returncode == 0,
    }


def synthetic_results(out: Path) -> dict:
    config = GeneratorConfig()
    with tempfile.TemporaryDirectory() as tmp:
        a = write_dataset(generate(config), Path(tmp) / "a")
        b = write_dataset(generate(config), Path(tmp) / "b")
        problems = validate_dataset(Path(tmp) / "a")
        deterministic = a == b

        shutil.copy(Path(tmp) / "a" / "manifest.json", out / "synthetic-manifest.json")
        sample_dir = out / "sample"
        shutil.rmtree(sample_dir, ignore_errors=True)
        for entry in a["files"]:
            src = Path(tmp) / "a" / entry["path"]
            dst = sample_dir / entry["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            keep = SAMPLE_ROWS + (1 if entry["format"] == "csv" else 0)  # + CSV header
            dst.write_text("".join(src.read_text().splitlines(keepends=True)[:keep]))
        (sample_dir / "README.txt").write_text(
            f"{SYNTHETIC_MARKER}: first {SAMPLE_ROWS} rows of each synthetic extract.\n"
        )

    return {
        "marker": SYNTHETIC_MARKER,
        "config": config.as_dict(),
        "total_rows": a["total_rows"],
        "rows_per_file": {f["path"]: f["rows"] for f in a["files"]},
        "validator_problems": problems,
        "deterministic_across_runs": deterministic,
        "files_sha256": {f["path"]: f["sha256"] for f in a["files"]},
    }


def compose_results() -> dict:
    docker = shutil.which("docker")
    if not docker:
        return {"config_valid": "not_run", "reason": "docker CLI not installed"}
    env_example = str(REPO / ".env.example")
    base = [docker, "compose", "--env-file", env_example]
    default = run([*base, "config", "--quiet"])
    profiles = run([*base, "--profile", "postgres", "--profile", "tools", "config", "--quiet"])
    daemon = run([docker, "info", "--format", "{{.ServerVersion}}"])
    return {
        "config_valid": default.returncode == 0 and profiles.returncode == 0,
        "config_stderr": (default.stderr + profiles.stderr).strip() or None,
        "docker_daemon_reachable": daemon.returncode == 0,
        "images_built_or_run": "not_run"
        if daemon.returncode != 0
        else "not_attempted_by_collector",
    }


def environment() -> dict:
    def has_module(name: str) -> bool:
        # Probe from outside the repo in isolated mode: otherwise `import dbt` would find our
        # own `dbt/` project directory (a namespace package) and wrongly report dbt as installed.
        probe = subprocess.run(
            [sys.executable, "-I", "-c", f"import {name}"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            check=False,
        )
        return probe.returncode == 0

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tools_available": {
            "ruff": bool(shutil.which("ruff")),
            "docker_cli": bool(shutil.which("docker")),
            "dbt": has_module("dbt"),
            "duckdb": has_module("duckdb"),
            "fastapi": has_module("fastapi"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True)
    args = parser.parse_args()

    out = REPO / "evidence" / f"phase-{args.phase}"
    out.mkdir(parents=True, exist_ok=True)

    tests = pytest_results(out)
    evidence = {
        "phase": args.phase,
        "collected_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": environment(),
        "tests": tests,
        "lint": lint_results(),
        "synthetic_data": synthetic_results(out),
        "docker_compose": compose_results(),
        "dbt": {"parse": "not_run", "reason": "dbt CLI not installed in this environment"}
        if not environment()["tools_available"]["dbt"]
        else {"parse": "not_attempted_by_collector"},
        "ci": {"github_actions_run": "not_run", "reason": "repository not pushed to GitHub"},
        "screenshots": sorted(
            {p.name: sha256(p) for p in (out / "screenshots").glob("*.png")}.items()
        )
        if (out / "screenshots").is_dir()
        else [],
    }
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    t = tests["all"]
    print(
        f"tests: {t['tests']} run, {t['failures']} failed, {t['errors']} errors, "
        f"{t['skipped']} skipped | lint: {evidence['lint']['status']} | "
        f"synthetic rows: {evidence['synthetic_data']['total_rows']} | "
        f"deterministic: {evidence['synthetic_data']['deterministic_across_runs']}"
    )
    print(f"evidence written to {out.relative_to(REPO)}/evidence.json")
    ok = t["passed"] and evidence["lint"]["status"] != "fail"
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
