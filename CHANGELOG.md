# Changelog

All notable changes to ModelDiff are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- `normalize_whitespace` option for `exact` checks: collapses runs of whitespace, including newlines, before comparing ([#16](https://github.com/OpenWave-India/modeldiff/pull/16), by [@anant-rajj](https://github.com/anant-rajj), fixes [#4](https://github.com/OpenWave-India/modeldiff/issues/4)).
- `extract: code_fence` option for `json`, `required_fields` and `tool_call` checks: parses the content of a single Markdown code fence ([#17](https://github.com/OpenWave-India/modeldiff/pull/17), by [@chiragjangiir](https://github.com/chiragjangiir), fixes [#3](https://github.com/OpenWave-India/modeldiff/issues/3)).
- `modeldiff run` warns when a replay file has outputs for case IDs that are not in the suite ([#20](https://github.com/OpenWave-India/modeldiff/pull/20), by [@Mrun25](https://github.com/Mrun25), fixes [#5](https://github.com/OpenWave-India/modeldiff/issues/5)).
- `modeldiff schema {suite,config,result}` prints the JSON Schema for each file format, for editor validation ([#18](https://github.com/OpenWave-India/modeldiff/pull/18), by [@faisalorakzai-lab](https://github.com/faisalorakzai-lab), fixes [#6](https://github.com/OpenWave-India/modeldiff/issues/6)).
- Second example suite, `examples/extraction`, for structured invoice and address extraction ([#19](https://github.com/OpenWave-India/modeldiff/pull/19), by [@faisalorakzai-lab](https://github.com/faisalorakzai-lab), fixes [#7](https://github.com/OpenWave-India/modeldiff/issues/7)).
- File format reference in `docs/file-formats.md` ([#21](https://github.com/OpenWave-India/modeldiff/pull/21), by [@SwetaKumari7](https://github.com/SwetaKumari7), fixes [#8](https://github.com/OpenWave-India/modeldiff/issues/8)).
- Comparison reports show per-case warnings from the candidate run, such as an exceeded latency limit, and mark the ones that are new compared with the baseline ([#23](https://github.com/OpenWave-India/modeldiff/pull/23), by [@Mrun25](https://github.com/Mrun25), fixes [#2](https://github.com/OpenWave-India/modeldiff/issues/2)).
- `modeldiff validate` accepts several suite files, reports every invalid one, and exits 2 if any is invalid ([#33](https://github.com/OpenWave-India/modeldiff/pull/33), by [@Doomguy-BFG9000](https://github.com/Doomguy-BFG9000), fixes [#29](https://github.com/OpenWave-India/modeldiff/issues/29)).

## [0.1.0] - 2026-10-29

First public release.

### Added

- Test suites in YAML or JSON, validated with JSON Schema, with error messages that name the case and field.
- Six check types: `exact`, `json` (with optional JSON Schema), `classification`, `required_fields`, `tool_call`, and `custom` checks registered from Python with `--plugin`.
- `modeldiff run`, `validate`, `compare` and `report` commands, with exit codes for CI (0 no regressions, 1 regressions, 2 invalid input).
- Baseline vs candidate comparison that separates regressions, fixes, cases still failing, behavior changes, classification label changes, and missing cases.
- `modeldiff.run/v1` result files with configurable output capture (`full`, `hash`, `none`) and redaction of secret-looking config keys.
- Reports in text, Markdown and JSON.
- Latency comparison with an opt-in threshold, and optional process memory measurement via `psutil`.
- `replay` provider for recorded outputs, and an optional `ollama` provider for local models.
- An offline quickstart example with recorded fixtures.

### Fixed

- `required_fields` values and `tool_call` arguments now compare by JSON type, so `true` no longer matches `1` ([#14](https://github.com/OpenWave-India/modeldiff/pull/14), by [@bfs2021](https://github.com/bfs2021), fixes [#1](https://github.com/OpenWave-India/modeldiff/issues/1)).

[Unreleased]: https://github.com/OpenWave-India/modeldiff/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OpenWave-India/modeldiff/releases/tag/v0.1.0
