"""Phase 2 evidence: ingestion logs, run reports, validation report, CLI output, coverage.

Everything here is *produced by running the real CLI* in a scratch directory against synthetic
data; nothing is hand-written. Captured text has the scratch path replaced by ``<workdir>``.

Scenarios (each a real ``python -m ingestion ...`` invocation)
    1. generate synthetic extracts                         -> cli/01
    2. ingest all sources, first load  (mock "now" = day 6; includes injected faults)   -> run-1
    3. ingest again, later "now" (day 20): incremental + updated records            -> run-2
    4. ingest a third time, nothing changed: idempotent, NO_NEW_DATA                -> run-3
    5. dry run, error cases (unknown source, invalid config), verify-bronze, tamper detection
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MAPPINGS = "../../ingestion/configs/mappings"  # relative to evidence/phase-2/demo_config.yaml
EARLY, LATE = "2026-01-06T00:00:00", "2026-01-20T00:00:00"
MOCK_SOURCES = ("mock_sis", "mock_lms", "mock_assessment", "mock_attendance")


def demo_config() -> dict:
    """The shipped config plus deliberate, deterministic faults so the reports show real work."""
    cfg = yaml.safe_load((REPO / "ingestion" / "configs" / "ingestion.yaml").read_text())
    for src in cfg["sources"].values():
        for entity in src["entities"]:
            entity["mapping"] = f"{MAPPINGS}/{Path(entity['mapping']).name}"
    srcs = cfg["sources"]
    srcs["rest_sis_api"]["enabled"] = True
    srcs["mock_sis"]["mock"]["faults"] = {"fail_first_n_calls": 1, "bad_row_rate": 0.02}
    srcs["mock_lms"]["mock"]["faults"] = {"duplicate_row_rate": 0.01}
    srcs["mock_attendance"]["mock"]["faults"] = {"bad_row_rate": 0.005}
    cfg["storage"]["root"] = "data/ingestion"
    cfg["defaults"]["retry"]["base_delay_seconds"] = 0.05  # keep the demo quick
    return cfg


class Scratch:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.dir = Path(tempfile.mkdtemp(prefix="edupulse-evidence-"))
        self.env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONIOENCODING": "utf-8"}
        self.commands: list[dict] = []

    def clean(self, text: str) -> str:
        return text.replace(str(self.dir), "<workdir>").replace(str(REPO), "<repo>")

    def cli(
        self, name: str, args: list[str], *, module: str = "ingestion"
    ) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", module, *args]
        proc = subprocess.run(
            cmd,
            cwd=self.dir,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        shown = (
            "edupulse " + " ".join(args)
            if module == "ingestion"
            else f"python -m {module} " + " ".join(args)
        )
        body = (
            f"$ {shown}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
            f"--- exit code: {proc.returncode} ---\n"
        )
        (self.out / "cli").mkdir(parents=True, exist_ok=True)
        (self.out / "cli" / f"{name}.txt").write_text(self.clean(body), encoding="utf-8")
        self.commands.append(
            {"name": name, "command": self.clean(shown), "exit_code": proc.returncode}
        )
        return proc


def overrides(as_of: str) -> list[str]:
    args: list[str] = []
    for name in MOCK_SOURCES:
        args += ["--override", f"sources.{name}.mock.as_of={as_of}"]
    return args


def copy_run(scratch: Scratch, run_id: str, label: str) -> Path:
    src = scratch.dir / "data" / "ingestion" / "runs" / run_id
    dst = scratch.out / label
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for file in src.iterdir():
        text = scratch.clean(file.read_text(encoding="utf-8"))
        (dst / file.name).write_text(text, encoding="utf-8")
    return dst


def _student_values(scratch: Scratch) -> set[str]:
    """Real values that must never appear in reports/logs: names and student IDs from Bronze."""
    values: set[str] = set()
    for manifest in (scratch.dir / "data" / "ingestion" / "bronze").rglob(
        "table=sis_students/**/_MANIFEST.json"
    ):
        for part in manifest.parent.glob("part-*.jsonl"):
            for line in part.read_text().splitlines():
                raw = json.loads(line)["raw"]
                for key in (
                    "FirstName",
                    "LastSurname",
                    "StudentUniqueId",
                    "StateStudentId",
                    "BirthDate",
                ):
                    value = raw.get(key)
                    if value and len(str(value)) > 3:
                        values.add(str(value))
    return values


def scanned_count(scratch: Scratch) -> int:
    return len(_student_values(scratch))


def leaked_values(scratch: Scratch, report_dir: Path) -> list[str]:
    """Names of report files containing any known student value (empty list == clean)."""
    values = _student_values(scratch)
    return [
        f.name
        for f in report_dir.iterdir()
        if any(v in f.read_text(encoding="utf-8") for v in values)
    ]


def check_invariants(report: dict) -> list[str]:
    problems = []
    for src in report["sources"]:
        for e in src["entities"]:
            r = e["records"]
            if e["status"] in ("FAILED", "FAILED_QUALITY_GATE"):
                continue
            if r["received"] != r["rejected"] + r["accepted"]:
                problems.append(f"{src['source']}/{e['entity']}: received != rejected + accepted")
            if r["accepted"] != r["duplicates"] + r["unchanged_skipped"] + r["loaded"]:
                problems.append(
                    f"{src['source']}/{e['entity']}: accepted != dup + unchanged + loaded"
                )
            if r["transformed"] != r["accepted"]:
                problems.append(f"{src['source']}/{e['entity']}: transformed != accepted")
    return problems


def summarize(report: dict) -> dict:
    return {
        "run_id": report["run_id"],
        "status": report["status"],
        "records": report["records"],
        "duration_seconds": report["duration_seconds"],
        "by_source": {
            s["source"]: {"status": s["status"], **s["records"], "seconds": s["duration_seconds"]}
            for s in report["sources"]
        },
    }


def collect(out: Path) -> dict:
    from ingestion.synthetic.rest_server import MockRestServer

    out.mkdir(parents=True, exist_ok=True)
    scratch = Scratch(out)
    result: dict = {"scenarios": {}, "checks": {}}
    try:
        (out / "demo_config.yaml").write_text(
            "# Generated by evidence/phase2.py: the shipped config + deterministic demo faults.\n"
            + yaml.safe_dump(demo_config(), sort_keys=False, width=120),
            encoding="utf-8",
        )
        config = str(out / "demo_config.yaml")

        gen = scratch.cli(
            "01_generate_synthetic",
            ["generate", "--out", "data/synthetic"],
            module="ingestion.synthetic",
        )
        result["checks"]["synthetic_generated"] = gen.returncode == 0

        with MockRestServer() as server:
            rest = ["--override", f"sources.rest_sis_api.base_url={server.base_url}"]
            runs = {}
            for label, name, as_of, run_id in (
                ("run-1-first-load", "02_ingest_all_first_load", EARLY, "evidence-run-1"),
                ("run-2-incremental", "03_ingest_all_incremental", LATE, "evidence-run-2"),
                ("run-3-no-change", "04_ingest_all_rerun_idempotent", LATE, "evidence-run-3"),
            ):
                proc = scratch.cli(
                    name,
                    [
                        "ingest",
                        "--source",
                        "all",
                        "--config",
                        config,
                        "--run-id",
                        run_id,
                        *overrides(as_of),
                        *rest,
                    ],
                )
                runs[label] = (run_id, proc.returncode)

            for label, (run_id, code) in runs.items():
                dst = copy_run(scratch, run_id, label)
                report = json.loads((dst / "run_report.json").read_text())
                result["scenarios"][label] = {"exit_code": code, **summarize(report)}
                result["checks"][f"{label}_invariants_hold"] = check_invariants(report) == []
                leaked = leaked_values(scratch, dst)
                result["checks"][f"{label}_report_files_contain_no_record_values"] = not leaked
                result["checks"][f"{label}_values_scanned"] = scanned_count(scratch)

            dry = scratch.cli(
                "05_ingest_dry_run",
                [
                    "ingest",
                    "--source",
                    "mock_sis",
                    "--config",
                    config,
                    "--dry-run",
                    "--run-id",
                    "evidence-dry",
                    *overrides(LATE),
                ],
            )
            result["scenarios"]["dry_run"] = {"exit_code": dry.returncode}

        unknown = scratch.cli(
            "06_error_unknown_source", ["ingest", "--source", "does_not_exist", "--config", config]
        )
        bad = scratch.dir / "bad.yaml"
        bad.write_text(
            "sources:\n  s:\n    type: mock_sis\n    surprise_key: 1\n", encoding="utf-8"
        )
        invalid = scratch.cli(
            "07_error_invalid_config", ["ingest", "--source", "all", "--config", str(bad)]
        )
        result["scenarios"]["error_cases"] = {
            "unknown_source_exit": unknown.returncode,
            "invalid_config_exit": invalid.returncode,
        }

        verify = scratch.cli("08_verify_bronze_ok", ["verify-bronze", "--root", "data/ingestion"])
        result["checks"]["bronze_verifies_clean"] = verify.returncode == 0

        tamper_root = scratch.dir / "tampered"
        shutil.copytree(scratch.dir / "data" / "ingestion" / "bronze", tamper_root / "bronze")
        part = next((tamper_root / "bronze").rglob("part-00000.jsonl"))
        os.chmod(part, 0o644)
        part.write_bytes(part.read_bytes().replace(b"SYNTHETIC", b"SYNTHETIX", 1))
        tampered = scratch.cli(
            "09_verify_bronze_detects_tampering", ["verify-bronze", "--root", "tampered"]
        )
        result["checks"]["tampering_detected"] = tampered.returncode == 1

        scratch.cli("10_help", ["ingest", "--help"])

        # dead-letter and Bronze samples (synthetic data; first rows only)
        sample = out / "samples"
        sample.mkdir(exist_ok=True)
        data = scratch.dir / "data" / "ingestion"
        dl = next((data / "dead_letter").rglob("rejected.jsonl"), None)
        if dl is not None:
            lines = dl.read_text().splitlines()[:3]
            (sample / "dead_letter_first3.jsonl").write_text(scratch.clean("\n".join(lines)) + "\n")
            (sample / "dead_letter_manifest.json").write_text(
                scratch.clean((dl.parent / "_MANIFEST.json").read_text())
            )
        manifest = next((data / "bronze").rglob("table=sis_students/**/_MANIFEST.json"))
        (sample / "bronze_manifest_sis_students.json").write_text(
            scratch.clean(manifest.read_text())
        )
        rows = (manifest.parent / "part-00000.jsonl").read_text().splitlines()[:2]
        (sample / "bronze_sis_students_first2.jsonl").write_text("\n".join(rows) + "\n")

        # headline: sample markdown table by source (run 1 and 2)
        md = [
            "# Sample ingestion run: records by source",
            "",
            "Generated by `evidence/phase2.py` from the real run reports. "
            "All data is SYNTHETIC_TEST_DATA.",
            "",
        ]
        for label in ("run-1-first-load", "run-2-incremental", "run-3-no-change"):
            md += [f"## {label}", "", (out / label / "summary.md").read_text().strip(), ""]
        (out / "sample_run_by_source.md").write_text("\n".join(md), encoding="utf-8")
    finally:
        shutil.rmtree(scratch.dir, ignore_errors=True)
    result["commands"] = scratch.commands
    return result


def coverage(out: Path) -> dict:
    cov_dir = out / "coverage"
    proc = subprocess.run(
        [
            sys.executable,
            "evidence/line_coverage.py",
            "--package",
            "ingestion",
            "--out",
            str(cov_dir),
            "--",
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    report_path = cov_dir / "coverage_report.json"
    if not report_path.exists():
        return {"status": "failed", "detail": proc.stderr[-500:]}
    data = json.loads(report_path.read_text())
    phase2_files = [f for f in data["files"] if "synthetic/" not in f["file"]]
    covered = sum(f["covered"] for f in phase2_files)
    stmts = sum(f["statements"] for f in phase2_files)
    return {
        "status": "ok" if proc.returncode == 0 else "tests_failed",
        "tool": data["tool"],
        "total_percent_ingestion_package": data["total_percent"],
        "phase2_framework_percent_excluding_synthetic": round(100 * covered / stmts, 1)
        if stmts
        else None,
        "limitations": data["limitations"],
    }
