"""Render runs and comparisons as text, Markdown, or JSON."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from modeldiff.compare import CaseComparison, Comparison
from modeldiff.results import RunResult

FORMATS = ("text", "markdown", "json")
COMPARISON_FORMATS = (*FORMATS, "junit")


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} ms"


def _short(text: str | None, limit: int = 100) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|")


# --- single run --------------------------------------------------------------


def render_run(run: RunResult, fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(run.to_dict(), indent=2, ensure_ascii=False)
    counts = run.counts()
    total = len(run.results)
    header = f"{run.suite} @ {run.config_id}: {counts['pass']}/{total} passed, {counts['fail']} failed, {counts['error']} errors"
    problems = [r for r in run.results if r.status != "pass"]
    if fmt == "markdown":
        lines = [f"# ModelDiff run: {run.suite} @ `{run.config_id}`", "", header, ""]
        if problems:
            lines += ["| Case | Type | Status | Reason |", "| --- | --- | --- | --- |"]
            lines += [
                f"| `{r.case_id}` | {r.type} | {r.status} | {_md_cell(_short(r.reason or r.error))} |"
                for r in problems
            ]
        return "\n".join(lines)
    lines = [header]
    for r in problems:
        lines.append(
            f"  {r.status.upper():5} {r.case_id} [{r.type}]: {_short(r.reason or r.error)}"
        )
    for r in run.results:
        for w in r.warnings:
            lines.append(f"  WARN  {r.case_id}: {w}")
    if run.unavailable_metrics:
        lines.append(f"Unavailable metrics: {', '.join(run.unavailable_metrics)}")
    return "\n".join(lines)


# --- comparison --------------------------------------------------------------


def _summary_line(comp: Comparison) -> str:
    c = comp.counts()
    total = len(comp.cases)
    return (
        f"{total} cases: {c['regression']} regressed, {c['fixed']} fixed, "
        f"{c['still_failing']} still failing, {c['unchanged_pass']} passing in both, "
        f"{len(comp.behavior_changes)} passing with changed output"
    )


def _verdict(comp: Comparison) -> str:
    if comp.regressions:
        return f"REGRESSIONS FOUND ({len(comp.regressions)})"
    return "NO REGRESSIONS"


def _transition(case: CaseComparison) -> str:
    return f"{case.baseline_status or 'missing'} -> {case.candidate_status or 'missing'}"


def _latency_lines(comp: Comparison) -> list[str]:
    lat = comp.latency
    if lat is None:
        return ["Latency: no timing data in both runs."]
    pct = lat.change_pct
    change = "n/a" if pct is None else f"{pct:+.1f}%"
    sources = (
        f"baseline {'/'.join(lat.baseline_sources)}, candidate {'/'.join(lat.candidate_sources)}"
    )
    return [
        f"Latency (median over {lat.cases} cases): {_ms(lat.baseline_median_ms)} -> "
        f"{_ms(lat.candidate_median_ms)} ({change}; {sources})"
    ]


def _warning_lines(comp: Comparison, markdown: bool = False) -> list[str]:
    warned = [case for case in comp.cases if case.candidate_warnings]
    if not warned:
        return []
    lines = ["", "## Warnings" if markdown else "Warnings:"]
    for case in warned:
        for warning in case.candidate_warnings:
            marker = " (new)" if warning not in case.baseline_warnings else ""
            prefix = f"- `{case.case_id}`: " if markdown else f"  {case.case_id}: "
            lines.append(f"{prefix}{warning}{marker}")
    return lines


def render_comparison(comp: Comparison, fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(comp.to_dict(), indent=2, ensure_ascii=False)
    if fmt == "junit":
        return _render_junit(comp)
    if fmt == "markdown":
        return _render_markdown(comp)
    return _render_text(comp)


def _render_text(comp: Comparison) -> str:
    lines = [
        f"ModelDiff comparison: {comp.suite}",
        f"Baseline: {comp.baseline_id}    Candidate: {comp.candidate_id}",
        "",
        f"{_verdict(comp)}  -  {_summary_line(comp)}",
    ]

    def section(title: str, items: list[CaseComparison], show_reason: bool = True) -> None:
        if not items:
            return
        lines.extend(["", f"{title} ({len(items)}):"])
        for c in items:
            reason = f": {_short(c.candidate_reason)}" if show_reason and c.candidate_reason else ""
            lines.append(f"  {c.case_id} [{c.type}] {_transition(c)}{reason}")

    section("Regressions", comp.regressions)
    section("Fixed", comp.by_category("fixed"), show_reason=False)
    section("Still failing", comp.by_category("still_failing"))
    section("Passing with changed output (review, not a regression)", comp.behavior_changes, False)
    section("Missing in candidate", comp.by_category("missing_in_candidate"), False)
    section("Missing in baseline", comp.by_category("missing_in_baseline"), False)

    if comp.label_changes:
        lines.extend(["", f"Classification changes ({len(comp.label_changes)}):"])
        for c in comp.label_changes:
            lines.append(f"  {c.case_id}: {c.label_change[0]!r} -> {c.label_change[1]!r}")

    lines.extend(_warning_lines(comp))
    lines.append("")
    lines.extend(_latency_lines(comp))
    if comp.latency_threshold_pct is not None:
        if comp.slowdowns:
            lines.append(f"Slower than +{comp.latency_threshold_pct:g}%:")
            for c in comp.slowdowns:
                lines.append(
                    f"  {c.case_id}: {_ms(c.baseline_duration_ms)} -> {_ms(c.candidate_duration_ms)}"
                )
        else:
            lines.append(f"No case slower than +{comp.latency_threshold_pct:g}%.")
    for note in comp.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def _render_markdown(comp: Comparison) -> str:
    lines = [
        f"# ModelDiff comparison: {comp.suite}",
        "",
        f"- Baseline: `{comp.baseline_id}`",
        f"- Candidate: `{comp.candidate_id}`",
        f"- Result: **{_verdict(comp)}**",
        f"- {_summary_line(comp)}",
        "",
        "| Case | Type | Change | Output changed | Latency | Candidate reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    order = {
        cat: i
        for i, cat in enumerate(
            (
                "regression",
                "fixed",
                "still_failing",
                "missing_in_candidate",
                "missing_in_baseline",
                "unchanged_pass",
            )
        )
    }
    for c in sorted(comp.cases, key=lambda c: order[c.category]):
        changed = {True: "yes", False: "no", None: "unknown"}[c.output_changed]
        if c.label_change:
            changed += f" (`{c.label_change[0]}` -> `{c.label_change[1]}`)"
        latency = f"{_ms(c.baseline_duration_ms)} -> {_ms(c.candidate_duration_ms)}"
        if c.slowdown:
            latency += " (slower)"
        reason = _md_cell(_short(c.candidate_reason)) if c.category != "unchanged_pass" else ""
        lines.append(
            f"| `{c.case_id}` | {c.type} | {c.category.replace('_', ' ')} ({_transition(c)}) "
            f"| {changed} | {latency} | {reason} |"
        )
    lines.extend(_warning_lines(comp, markdown=True))
    lines.append("")
    lines.extend(_latency_lines(comp))
    for note in comp.notes:
        lines.extend(["", f"> Note: {note}"])
    return "\n".join(lines)


def _render_junit(comp: Comparison) -> str:
    """Render a comparison as JUnit XML."""
    root = ET.Element(
        "testsuite",
        {
            "name": comp.suite,
            "tests": str(len(comp.cases)),
            "failures": "0",
            "errors": "0",
            "skipped": "0",
        },
    )

    failures = 0
    errors = 0
    skipped = 0

    for case in comp.cases:
        attributes = {
            "classname": comp.suite,
            "name": case.case_id,
        }

        if case.candidate_duration_ms is not None:
            seconds = max(case.candidate_duration_ms, 0) / 1000
            attributes["time"] = f"{seconds:.6f}"

        testcase = ET.SubElement(root, "testcase", attributes)

        if case.category == "still_failing":
            reason = case.candidate_reason or "Case is still failing"
            element = ET.SubElement(
                testcase,
                "failure",
                {"message": reason, "type": case.category},
            )
            element.text = reason
            failures += 1

        elif case.candidate_status == "error":
            reason = case.candidate_reason or "Candidate case errored"
            element = ET.SubElement(
                testcase,
                "error",
                {"message": reason, "type": "error"},
            )
            element.text = reason
            errors += 1

        elif case.category == "regression" or case.candidate_status == "fail":
            reason = case.candidate_reason or "Candidate case failed"
            element = ET.SubElement(
                testcase,
                "failure",
                {"message": reason, "type": case.category},
            )
            element.text = reason
            failures += 1

        elif case.category == "missing_in_candidate":
            element = ET.SubElement(
                testcase,
                "skipped",
                {"message": "Missing in candidate run"},
            )
            element.text = "No candidate result was available for this case."
            skipped += 1

        elif case.category == "unchanged_pass" and case.output_changed is True:
            note = ET.SubElement(testcase, "system-out")
            note.text = "Output changed between baseline and candidate; both cases passed."

    root.set("failures", str(failures))
    root.set("errors", str(errors))
    root.set("skipped", str(skipped))

    return ET.tostring(root, encoding="unicode")
