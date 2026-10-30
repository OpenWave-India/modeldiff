"""Compare a baseline run with a candidate run.

Terminology used throughout reports:

* **regression** - the case passed in the baseline and fails (or errors) in
  the candidate. Only these count as regressions.
* **fixed** - failed or errored in the baseline, passes in the candidate.
* **output changed** - the output text differs. This is a behavioral
  difference, not a regression: if both runs pass, the case's criterion is
  still met, so the change is reported for review only.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from modeldiff.errors import ValidationError
from modeldiff.results import CaseResult, RunResult

CATEGORIES = (
    "regression",
    "fixed",
    "still_failing",
    "unchanged_pass",
    "missing_in_candidate",
    "missing_in_baseline",
)


@dataclass
class CaseComparison:
    case_id: str
    type: str
    category: str
    baseline_status: str | None
    candidate_status: str | None
    baseline_reason: str | None = None
    candidate_reason: str | None = None
    output_changed: bool | None = None  # None: not knowable (output not captured)
    label_change: tuple[str, str] | None = None
    baseline_duration_ms: float | None = None
    candidate_duration_ms: float | None = None
    slowdown: bool = False
    baseline_warnings: list[str] = field(default_factory=list)
    candidate_warnings: list[str] = field(default_factory=list)

    @property
    def duration_delta_ms(self) -> float | None:
        if self.baseline_duration_ms is None or self.candidate_duration_ms is None:
            return None
        return self.candidate_duration_ms - self.baseline_duration_ms


@dataclass
class LatencySummary:
    cases: int
    baseline_median_ms: float
    candidate_median_ms: float
    baseline_sources: list[str]
    candidate_sources: list[str]

    @property
    def change_pct(self) -> float | None:
        if self.baseline_median_ms == 0:
            return None
        return (self.candidate_median_ms - self.baseline_median_ms) / self.baseline_median_ms * 100


@dataclass
class Comparison:
    suite: str
    baseline_id: str
    candidate_id: str
    cases: list[CaseComparison]
    latency: LatencySummary | None = None
    latency_threshold_pct: float | None = None
    suite_mismatch: bool = False
    notes: list[str] = field(default_factory=list)

    def by_category(self, category: str) -> list[CaseComparison]:
        return [c for c in self.cases if c.category == category]

    @property
    def regressions(self) -> list[CaseComparison]:
        return self.by_category("regression")

    @property
    def behavior_changes(self) -> list[CaseComparison]:
        """Cases passing in both runs whose output nevertheless changed."""
        return [c for c in self.cases if c.category == "unchanged_pass" and c.output_changed]

    @property
    def label_changes(self) -> list[CaseComparison]:
        return [c for c in self.cases if c.label_change is not None]

    @property
    def slowdowns(self) -> list[CaseComparison]:
        return [c for c in self.cases if c.slowdown]

    def counts(self) -> dict[str, int]:
        return {cat: len(self.by_category(cat)) for cat in CATEGORIES}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["format"] = "modeldiff.comparison/v1"
        data["summary"] = {
            **self.counts(),
            "output_changed": sum(1 for c in self.cases if c.output_changed),
            "behavior_changes": len(self.behavior_changes),
            "label_changes": len(self.label_changes),
            "slowdowns": len(self.slowdowns),
        }
        for case_dict, case in zip(data["cases"], self.cases, strict=True):
            case_dict["duration_delta_ms"] = case.duration_delta_ms
            if case.label_change is not None:
                case_dict["label_change"] = {
                    "from": case.label_change[0],
                    "to": case.label_change[1],
                }
        if self.latency is not None:
            data["latency"]["change_pct"] = self.latency.change_pct
        return data


def _category(base: CaseResult | None, cand: CaseResult | None) -> str:
    if cand is None:
        return "missing_in_candidate"
    if base is None:
        return "missing_in_baseline"
    if base.status == "pass":
        return "unchanged_pass" if cand.status == "pass" else "regression"
    return "fixed" if cand.status == "pass" else "still_failing"


def _output_changed(base: CaseResult, cand: CaseResult) -> bool | None:
    if base.output_sha256 and cand.output_sha256:
        return base.output_sha256 != cand.output_sha256
    if base.output is not None and cand.output is not None:
        return base.output != cand.output
    return None


def compare_runs(
    baseline: RunResult,
    candidate: RunResult,
    latency_threshold_pct: float | None = None,
    min_latency_delta_ms: float = 0.0,
    allow_suite_mismatch: bool = False,
) -> Comparison:
    """Compare two runs of the same suite.

    ``latency_threshold_pct`` flags cases whose candidate latency exceeds
    the baseline by more than that percentage *and* by at least
    ``min_latency_delta_ms``. Flagged cases are reported, not counted as
    regressions.
    """
    problems = []
    if baseline.suite != candidate.suite:
        problems.append(f"suite names differ: {baseline.suite!r} vs {candidate.suite!r}")
    mismatch = baseline.suite_sha256 != candidate.suite_sha256
    if mismatch and not allow_suite_mismatch:
        problems.append(
            "the runs used different case definitions (suite_sha256 differs); re-run both "
            "with the same suite file or pass --allow-suite-mismatch"
        )
    if problems:
        raise ValidationError("comparison", problems)

    base_by_id, cand_by_id = baseline.by_id(), candidate.by_id()
    ordered_ids = list(base_by_id) + [i for i in cand_by_id if i not in base_by_id]

    type_conflicts = [
        f"{i}: {base_by_id[i].type!r} vs {cand_by_id[i].type!r}"
        for i in ordered_ids
        if i in base_by_id and i in cand_by_id and base_by_id[i].type != cand_by_id[i].type
    ]
    if type_conflicts:
        raise ValidationError(
            "comparison", ["case types differ between runs: " + "; ".join(type_conflicts)]
        )

    cases = []
    for case_id in ordered_ids:
        base, cand = base_by_id.get(case_id), cand_by_id.get(case_id)
        ref = base or cand
        comp = CaseComparison(
            case_id=case_id,
            type=ref.type,
            category=_category(base, cand),
            baseline_status=base.status if base else None,
            candidate_status=cand.status if cand else None,
            baseline_reason=(base.reason or base.error) if base else None,
            candidate_reason=(cand.reason or cand.error) if cand else None,
            baseline_warnings=list(base.warnings) if base else [],
            candidate_warnings=list(cand.warnings) if cand else [],
            baseline_duration_ms=base.duration_ms if base else None,
            candidate_duration_ms=cand.duration_ms if cand else None,
        )
        if base and cand:
            comp.output_changed = _output_changed(base, cand)
            old = base.details.get("predicted_label")
            new = cand.details.get("predicted_label")
            if old is not None and new is not None and old != new:
                comp.label_change = (old, new)
            delta = comp.duration_delta_ms
            if (
                latency_threshold_pct is not None
                and delta is not None
                and comp.baseline_duration_ms
            ):
                pct = delta / comp.baseline_duration_ms * 100
                comp.slowdown = pct > latency_threshold_pct and delta >= min_latency_delta_ms
        cases.append(comp)

    timed = [c for c in cases if c.duration_delta_ms is not None]
    latency = None
    if timed:
        latency = LatencySummary(
            cases=len(timed),
            baseline_median_ms=statistics.median(c.baseline_duration_ms for c in timed),
            candidate_median_ms=statistics.median(c.candidate_duration_ms for c in timed),
            baseline_sources=sorted(
                {r.duration_source for r in baseline.results if r.duration_source}
            ),
            candidate_sources=sorted(
                {r.duration_source for r in candidate.results if r.duration_source}
            ),
        )

    notes = []
    if mismatch:
        notes.append("Runs used different case definitions; results may not be comparable.")
    if "none" in (baseline.capture_output, candidate.capture_output):
        notes.append(
            "Output capture was disabled for at least one run; output changes are unknown."
        )
    if latency and (set(latency.baseline_sources) != set(latency.candidate_sources)):
        notes.append("Latency sources differ between runs (measured vs recorded).")

    return Comparison(
        suite=baseline.suite,
        baseline_id=baseline.config_id,
        candidate_id=candidate.config_id,
        cases=cases,
        latency=latency,
        latency_threshold_pct=latency_threshold_pct,
        suite_mismatch=mismatch,
        notes=notes,
    )
