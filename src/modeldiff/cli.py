"""Command-line interface.

Exit codes:
  0  success (for ``compare``: no regressions)
  1  regressions found, or failures when ``run --fail-on-failure`` is used
  2  invalid input: malformed suite, config, or result file, or bad arguments
"""

from __future__ import annotations

import argparse
import copy
import importlib
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from modeldiff._version import __version__
from modeldiff.cases import SUITE_SCHEMA, load_suite
from modeldiff.compare import compare_runs
from modeldiff.config import CAPTURE_MODES, CONFIG_SCHEMA, load_config
from modeldiff.errors import ModelDiffError, ValidationError
from modeldiff.providers import ReplayProvider, build_provider
from modeldiff.report import (
    COMPARISON_FORMATS,
    FORMATS,
    render_comparison,
    render_run,
)
from modeldiff.results import RESULT_SCHEMA, load_run, save_run
from modeldiff.runner import run_suite

EXIT_OK, EXIT_REGRESSION, EXIT_INVALID = 0, 1, 2


def load_plugin(spec: str) -> None:
    """Import a module (``pkg.checks``) or a file (``./checks.py``) that registers checks."""
    if spec.endswith(".py") or Path(spec).suffix == ".py":
        path = Path(spec)
        if not path.is_file():
            raise ValidationError(spec, ["plugin file not found"])
        module_spec = importlib.util.spec_from_file_location(f"modeldiff_plugin_{path.stem}", path)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return
    try:
        importlib.import_module(spec)
    except ImportError as exc:
        raise ValidationError(spec, [f"cannot import plugin module: {exc}"]) from None


def _emit(text: str, output: str | None) -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {path}", file=sys.stderr)
    else:
        print(text)


def cmd_validate(args: argparse.Namespace) -> int:
    for plugin in args.plugin:
        load_plugin(plugin)
    status = EXIT_OK
    for path in args.suite:
        try:
            suite = load_suite(path)
        except ValidationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            status = EXIT_INVALID
            continue
        types: dict[str, int] = {}
        for case in suite.cases:
            types[case.type] = types.get(case.type, 0) + 1
        breakdown = ", ".join(f"{n} {t}" for t, n in sorted(types.items()))
        print(
            f"OK: {path} defines suite {suite.name!r} with {len(suite.cases)} cases ({breakdown})"
        )
    return status


def cmd_run(args: argparse.Namespace) -> int:
    for plugin in args.plugin:
        load_plugin(plugin)
    full_suite = load_suite(args.suite)
    suite = full_suite.filter(args.tag)
    if not suite.cases:
        raise ValidationError(args.suite, [f"no cases match tag(s): {', '.join(args.tag)}"])
    config = load_config(args.config)
    if args.capture:
        config = type(config)(
            config.id, config.provider, config.settings, args.capture, config.base_dir
        )
    provider = build_provider(config)
    if isinstance(provider, ReplayProvider):
        unknown_ids = provider.recorded_case_ids - {case.id for case in full_suite.cases}
        if unknown_ids:
            print(
                f"warning: {provider.path} has outputs for unknown case(s): "
                + ", ".join(sorted(unknown_ids)),
                file=sys.stderr,
            )
    run = run_suite(suite, config, provider, enforce_performance=args.enforce_performance)
    save_run(run, args.output)
    print(render_run(run, "text"))
    print(f"Results written to {args.output}")
    counts = run.counts()
    if args.fail_on_failure and (counts["fail"] or counts["error"]):
        return EXIT_REGRESSION
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    baseline, candidate = load_run(args.baseline), load_run(args.candidate)
    comparison = compare_runs(
        baseline,
        candidate,
        latency_threshold_pct=args.latency_threshold,
        min_latency_delta_ms=args.min_latency_delta,
        allow_suite_mismatch=args.allow_suite_mismatch,
    )
    _emit(render_comparison(comparison, args.format), args.output)
    if args.exit_zero:
        return EXIT_OK
    failed = bool(comparison.regressions) or (args.fail_on_latency and bool(comparison.slowdowns))
    return EXIT_REGRESSION if failed else EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    run = load_run(args.results)
    _emit(render_run(run, args.format), args.output)
    return EXIT_OK


SCHEMAS = {"suite": SUITE_SCHEMA, "config": CONFIG_SCHEMA, "result": RESULT_SCHEMA}


def cmd_schema(args: argparse.Namespace) -> int:
    schema = copy.deepcopy(SCHEMAS[args.kind])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = {
        "suite": "ModelDiff test suite",
        "config": "ModelDiff run configuration",
        "result": "ModelDiff run result",
    }[args.kind]
    _emit(json.dumps(schema, indent=2), args.output)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modeldiff", description="Regression testing for local AI models and configurations."
    )
    parser.add_argument("--version", action="version", version=f"modeldiff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    plugin_help = "module or .py file that registers custom checks (repeatable)"

    p = sub.add_parser("validate", help="check suite files for errors")
    p.add_argument("suite", nargs="+", help="suite files (.yaml, .yml or .json)")
    p.add_argument("--plugin", action="append", default=[], help=plugin_help)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("run", help="run a suite with one configuration and save the results")
    p.add_argument("suite", help="suite file")
    p.add_argument("--config", "-c", required=True, help="run configuration file")
    p.add_argument("--output", "-o", required=True, help="where to write the result JSON")
    p.add_argument(
        "--tag", action="append", default=[], help="only run cases with this tag (repeatable)"
    )
    p.add_argument("--capture", choices=CAPTURE_MODES, help="override the config's capture_output")
    p.add_argument("--plugin", action="append", default=[], help=plugin_help)
    p.add_argument(
        "--enforce-performance",
        action="store_true",
        help="fail cases that exceed performance.max_latency_ms (default: warn only)",
    )
    p.add_argument(
        "--fail-on-failure", action="store_true", help="exit 1 if any case fails or errors"
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("compare", help="compare a baseline result file with a candidate")
    p.add_argument("baseline", help="baseline result JSON")
    p.add_argument("candidate", help="candidate result JSON")
    p.add_argument("--format", "-f", choices=COMPARISON_FORMATS, default="text")
    p.add_argument("--output", "-o", help="write the report to a file instead of stdout")
    p.add_argument(
        "--latency-threshold",
        type=float,
        metavar="PCT",
        help="flag cases whose latency grew by more than PCT percent",
    )
    p.add_argument(
        "--min-latency-delta",
        type=float,
        default=0.0,
        metavar="MS",
        help="ignore latency increases smaller than MS milliseconds (default 0)",
    )
    p.add_argument(
        "--fail-on-latency", action="store_true", help="exit 1 when cases are flagged as slower"
    )
    p.add_argument(
        "--allow-suite-mismatch",
        action="store_true",
        help="compare runs of different suite versions",
    )
    p.add_argument("--exit-zero", action="store_true", help="always exit 0 (report only)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("report", help="summarize a single result file")
    p.add_argument("results", help="result JSON")
    p.add_argument("--format", "-f", choices=FORMATS, default="text")
    p.add_argument("--output", "-o", help="write the report to a file instead of stdout")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("schema", help="print a JSON Schema for a ModelDiff file")
    p.add_argument("kind", choices=sorted(SCHEMAS), help="schema to print")
    p.add_argument("--output", "-o", help="write the schema to a file instead of stdout")
    p.set_defaults(func=cmd_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "latency_threshold", None) is not None and args.latency_threshold < 0:
        parser.error("--latency-threshold must be >= 0")
    if getattr(args, "fail_on_latency", False) and args.latency_threshold is None:
        parser.error("--fail-on-latency requires --latency-threshold")
    try:
        return args.func(args)
    except ModelDiffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
