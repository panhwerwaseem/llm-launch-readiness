# Changelog

All notable changes to this skill are recorded here. Versions follow [Semantic Versioning](https://semver.org).

## 0.1.0 - 2026-11-06

### Added

- `SKILL.md` with the review protocol: six hard rules and a seven-step workflow from scan to verdict.
- `scripts/readiness_scan.py`, a heuristic scan of Python, JavaScript and TypeScript code for nine safeguards and two hazards, with file and line evidence, text and JSON output.
- `scripts/scan_rules.py`, which holds every pattern the scan uses and prints them on request.
- References for the readiness checklist and for production failure modes.
- Six fixture apps (one clean, five with one planted defect each) with eval cases, and trigger queries.
- Unit tests, a format validator, and a CI workflow.
