"""Execute a suite against one provider and record the results."""

from __future__ import annotations

import hashlib
import platform
import time
from datetime import UTC, datetime
from typing import Any

from modeldiff._version import __version__
from modeldiff.cases import Suite, TestCase
from modeldiff.checks import evaluate
from modeldiff.config import RunConfig
from modeldiff.errors import ProviderError
from modeldiff.providers import Provider
from modeldiff.results import CaseResult, RunResult

try:  # Optional: per-case process memory needs psutil (`pip install openwave-modeldiff[memory]`).
    import psutil
except ImportError:  # pragma: no cover - depends on the environment
    psutil = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _process_rss() -> int | None:
    if psutil is None:
        return None
    return psutil.Process().memory_info().rss


def run_case(
    case: TestCase,
    provider: Provider,
    capture_output: str = "full",
    enforce_performance: bool = False,
) -> CaseResult:
    timestamp = _now()
    rss_before = _process_rss()
    start = time.perf_counter()
    try:
        response = provider.generate(case)
    except ProviderError as exc:
        return CaseResult(
            case_id=case.id,
            type=case.type,
            status="error",
            timestamp=timestamp,
            reason="provider error",
            error=str(exc),
            tags=list(case.tags),
            case_sha256=case.sha256,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    rss_after = _process_rss()

    if response.recorded_latency_ms is not None:
        duration_ms, duration_source = float(response.recorded_latency_ms), "recorded"
    else:
        duration_ms, duration_source = round(elapsed_ms, 3), "measured"

    memory = None
    if rss_before is not None and rss_after is not None:
        # This is the ModelDiff process only. A model served by a separate
        # process (e.g. Ollama) or GPU memory is not included.
        memory = {"process_rss_bytes": rss_after, "process_rss_delta_bytes": rss_after - rss_before}

    status, reason, details, error = "pass", None, {}, None
    try:
        check = evaluate(case, response.output)
        status = "pass" if check.passed else "fail"
        reason, details = check.reason, check.details
    except Exception as exc:  # a broken custom check must not abort the whole run
        status, reason, error = "error", "check raised an exception", f"{type(exc).__name__}: {exc}"

    warnings: list[str] = []
    limit = case.performance.get("max_latency_ms")
    if limit is not None and duration_ms > limit:
        message = f"latency {duration_ms:.1f} ms exceeds max_latency_ms {limit}"
        warnings.append(message)
        if enforce_performance and status == "pass":
            status, reason = "fail", message

    return CaseResult(
        case_id=case.id,
        type=case.type,
        status=status,
        timestamp=timestamp,
        reason=reason,
        output=response.output if capture_output == "full" else None,
        output_sha256=(
            hashlib.sha256(response.output.encode("utf-8")).hexdigest()
            if capture_output in ("full", "hash")
            else None
        ),
        duration_ms=duration_ms,
        duration_source=duration_source,
        memory=memory,
        details=details,
        warnings=warnings,
        error=error,
        provider_metadata=response.metadata,
        tags=list(case.tags),
        case_sha256=case.sha256,
    )


def runtime_info(provider: Provider) -> dict[str, Any]:
    return {
        "modeldiff_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        **provider.describe(),
    }


def run_suite(
    suite: Suite,
    config: RunConfig,
    provider: Provider,
    enforce_performance: bool = False,
) -> RunResult:
    started = _now()
    results = [
        run_case(case, provider, config.capture_output, enforce_performance) for case in suite.cases
    ]
    return RunResult(
        suite=suite.name,
        suite_sha256=suite.sha256,
        config=config.public_dict(),
        capture_output=config.capture_output,
        runtime=runtime_info(provider),
        started_at=started,
        finished_at=_now(),
        results=results,
        unavailable_metrics=[] if psutil is not None else ["memory"],
    )
