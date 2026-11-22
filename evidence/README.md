# evidence

One directory per milestone (`phase-1/`, …) produced by `python evidence/collect_evidence.py --phase N`:
`evidence.json` (test/lint/metric results, with `not_run` + reason for anything that could not run),
`junit-all.xml`, `synthetic-manifest.json`, `sample/` (first rows of each synthetic extract) and
`screenshots/`. The human-readable index is `docs/evidence-manifest.md`.
