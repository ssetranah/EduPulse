"""Line-coverage measurement with the standard library only (``sys.monitoring``, Python 3.12+).

Why this exists: the evidence-collection environment could not install ``coverage.py``. This
script measures *statement/line* coverage of a package while running pytest in-process, so
Phase 2 can ship a real coverage report. In CI, ``pytest --cov`` (pytest-cov / coverage.py,
which also measures branches and subprocesses) is the authoritative tool; see ``make coverage``.

Honest limitations (also written into the report):
* line coverage only -- no branch coverage;
* code that runs in *subprocesses* (the CLI subprocess tests) is not measured, so the CLI is
  additionally exercised by in-process tests;
* a line counts as executable if the compiler emitted bytecode for it, which can differ slightly
  from coverage.py's statement model; lines tagged ``# pragma: no cover`` are excluded.

Usage::

    python evidence/line_coverage.py --package ingestion --out evidence/phase-2/coverage -- tests -q
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from collections import defaultdict
from pathlib import Path

PRAGMA = "pragma: no cover"


def executable_lines(path: Path) -> set[int]:
    source = path.read_text(encoding="utf-8")
    excluded = {i for i, text in enumerate(source.splitlines(), 1) if PRAGMA in text}
    lines: set[int] = set()
    stack = [compile(source, str(path), "exec")]
    while stack:
        code = stack.pop()
        lines.update(line for _, _, line in code.co_lines() if line)
        stack.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
    return lines - excluded


def measure(package_dir: Path, pytest_args: list[str]) -> tuple[dict[str, set[int]], int]:
    mon = sys.monitoring
    tool = mon.COVERAGE_ID
    targets = {str(p.resolve()) for p in package_dir.rglob("*.py")}
    hits: dict[str, set[int]] = defaultdict(set)

    def on_line(code: types.CodeType, line: int) -> object:
        if code.co_filename in targets:
            hits[code.co_filename].add(line)
        return mon.DISABLE  # each (code, line) only needs to be seen once

    mon.use_tool_id(tool, "edupulse-line-coverage")
    mon.register_callback(tool, mon.events.LINE, on_line)
    mon.set_events(tool, mon.events.LINE)
    try:
        import pytest

        exit_code = int(pytest.main(pytest_args))
    finally:
        mon.set_events(tool, 0)
        mon.free_tool_id(tool)
    return hits, exit_code


def build_report(package_dir: Path, hits: dict[str, set[int]], root: Path) -> dict:
    files = []
    total_exec = total_hit = 0
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        executable = executable_lines(path)
        covered = executable & hits.get(str(path.resolve()), set())
        missing = sorted(executable - covered)
        total_exec += len(executable)
        total_hit += len(covered)
        files.append(
            {
                "file": path.relative_to(root).as_posix(),
                "statements": len(executable),
                "covered": len(covered),
                "percent": round(100 * len(covered) / len(executable), 1) if executable else 100.0,
                "missing_lines": missing,
            }
        )
    return {
        "tool": "evidence/line_coverage.py (sys.monitoring, line coverage)",
        "python": sys.version.split()[0],
        "package": package_dir.name,
        "total_statements": total_exec,
        "total_covered": total_hit,
        "total_percent": round(100 * total_hit / total_exec, 1) if total_exec else 100.0,
        "limitations": [
            "line coverage only; no branch coverage",
            "subprocess code (CLI subprocess tests) is not measured",
            "executable-line model may differ slightly from coverage.py",
            "authoritative CI measurement: pytest --cov (coverage.py), see `make coverage`",
        ],
        "files": files,
    }


def render_text(report: dict) -> str:
    width = max(len(f["file"]) for f in report["files"])
    lines = [
        f"Line coverage for package '{report['package']}' "
        f"({report['tool']}, Python {report['python']})",
        "",
        f"{'File':<{width}}  {'Stmts':>6} {'Miss':>6} {'Cover':>7}  Missing",
        "-" * (width + 40),
    ]
    for f in report["files"]:
        missing = ",".join(map(str, f["missing_lines"]))
        if len(missing) > 60:
            missing = missing[:57] + "..."
        lines.append(
            f"{f['file']:<{width}}  {f['statements']:>6} {f['statements'] - f['covered']:>6} "
            f"{f['percent']:>6.1f}%  {missing}"
        )
    lines += [
        "-" * (width + 40),
        f"{'TOTAL':<{width}}  {report['total_statements']:>6} "
        f"{report['total_statements'] - report['total_covered']:>6} "
        f"{report['total_percent']:>6.1f}%",
        "",
        "Limitations:",
        *[f"  - {x}" for x in report["limitations"]],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, pytest_args = argv[:cut], argv[cut + 1 :]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--package", default="ingestion")
    parser.add_argument("--out", type=Path, help="directory for coverage_report.json/.txt")
    parser.add_argument("--fail-under", type=float, default=0.0)
    args = parser.parse_args(argv)

    root = Path.cwd()
    package_dir = (root / args.package).resolve()
    sys.path.insert(0, str(root))
    hits, exit_code = measure(package_dir, pytest_args or ["tests", "-q"])
    report = build_report(package_dir, hits, root)
    report["pytest_exit_code"] = exit_code
    text = render_text(report)
    print("\n" + text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "coverage_report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        (args.out / "coverage_report.txt").write_text(text + "\n", "utf-8")
    if exit_code != 0:
        return exit_code
    return 0 if report["total_percent"] >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
