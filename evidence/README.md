# evidence

One directory per milestone (`phase-1/`, …) produced by `python evidence/collect_evidence.py --phase N`:
`evidence.json` (test/lint/metric results, with `not_run` + reason for anything that could not run),
`junit-all.xml`, `synthetic-manifest.json`, `sample/` (first rows of each synthetic extract) and
`screenshots/`. Phase 2 adds `cli/` (captured CLI output), `run-*/` (run reports, validation reports,
ingestion logs, lineage events), `coverage/`, `samples/` — produced by `evidence/phase2.py`, which runs
the real CLI in a scratch directory; `evidence/line_coverage.py` is the stdlib line-coverage tool
used because coverage.py could not be installed (CI uses `pytest --cov`). The human-readable index is `docs/evidence-manifest.md`.
