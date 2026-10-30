from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from modeldiff.compare import compare_runs
from modeldiff.errors import ValidationError
from modeldiff.report import render_comparison, render_run
from modeldiff.results import CaseResult, RunResult

SHA = "a" * 64


def case(
    case_id,
    status,
    output="out",
    type="exact",
    duration=100.0,
    label=None,
    capture=True,
    reason=None,
):
    return CaseResult(
        case_id=case_id,
        type=type,
        status=status,
        timestamp="2026-01-01T00:00:00Z",
        reason=reason,
        output=output if capture else None,
        output_sha256=hashlib.sha256(output.encode()).hexdigest() if capture else None,
        duration_ms=duration,
        duration_source="recorded",
        details={"predicted_label": label} if label else {},
    )


def make_run(config_id, *results, suite="s", sha=SHA, capture="full"):
    return RunResult(
        suite=suite,
        suite_sha256=sha,
        config={"id": config_id, "provider": "replay"},
        started_at="2026-01-01T00:00:00Z",
        results=list(results),
        capture_output=capture,
    )


def test_categories():
    base = make_run(
        "b",
        case("reg", "pass"),
        case("err", "pass"),
        case("fix", "fail"),
        case("still", "fail"),
        case("same", "pass"),
        case("gone", "pass"),
    )
    cand = make_run(
        "c",
        case("reg", "fail", reason="bad"),
        case("err", "error"),
        case("fix", "pass"),
        case("still", "error"),
        case("same", "pass"),
        case("new", "pass"),
    )
    comp = compare_runs(base, cand)
    cats = {c.case_id: c.category for c in comp.cases}
    assert cats == {
        "reg": "regression",
        "err": "regression",
        "fix": "fixed",
        "still": "still_failing",
        "same": "unchanged_pass",
        "gone": "missing_in_candidate",
        "new": "missing_in_baseline",
    }
    assert [c.case_id for c in comp.regressions] == ["reg", "err"]
    assert comp.by_category("fixed")[0].baseline_status == "fail"
    assert comp.regressions[0].candidate_reason == "bad"


def test_changed_output_on_passing_case_is_behavior_change_not_regression():
    comp = compare_runs(
        make_run("b", case("a", "pass", "x")), make_run("c", case("a", "pass", "y"))
    )
    assert comp.regressions == []
    assert [c.case_id for c in comp.behavior_changes] == ["a"]


def test_output_changed_unknown_without_capture():
    comp = compare_runs(
        make_run("b", case("a", "pass", capture=False), capture="none"),
        make_run("c", case("a", "pass", "z")),
    )
    assert comp.cases[0].output_changed is None
    assert any("Output capture was disabled" in n for n in comp.notes)


def test_hash_only_runs_still_detect_changes():
    base = case("a", "pass", "x")
    base.output = None
    comp = compare_runs(make_run("b", base, capture="hash"), make_run("c", case("a", "pass", "y")))
    assert comp.cases[0].output_changed is True


def test_classification_label_changes():
    comp = compare_runs(
        make_run("b", case("a", "pass", "billing", "classification", label="billing")),
        make_run("c", case("a", "fail", "technical", "classification", label="technical")),
    )
    assert comp.label_changes[0].label_change == ("billing", "technical")


def test_suite_name_mismatch_is_an_error():
    with pytest.raises(ValidationError, match="suite names differ"):
        compare_runs(
            make_run("b", case("a", "pass")), make_run("c", case("a", "pass"), suite="other")
        )


def test_suite_hash_mismatch_requires_opt_in():
    base, cand = make_run("b", case("a", "pass")), make_run("c", case("a", "pass"), sha="b" * 64)
    with pytest.raises(ValidationError, match="--allow-suite-mismatch"):
        compare_runs(base, cand)
    comp = compare_runs(base, cand, allow_suite_mismatch=True)
    assert comp.suite_mismatch and any("different case definitions" in n for n in comp.notes)


def test_case_type_conflict_is_an_error():
    with pytest.raises(ValidationError, match="case types differ"):
        compare_runs(
            make_run("b", case("a", "pass")), make_run("c", case("a", "pass", type="json"))
        )


def test_latency_summary_and_threshold():
    base = make_run(
        "b",
        case("a", "pass", duration=100),
        case("b", "pass", duration=1000),
        case("c", "pass", duration=10),
    )
    cand = make_run(
        "c",
        case("a", "pass", duration=130),
        case("b", "pass", duration=1100),
        case("c", "pass", duration=40),
    )
    assert compare_runs(base, cand).slowdowns == []  # no threshold -> nothing flagged
    comp = compare_runs(base, cand, latency_threshold_pct=20, min_latency_delta_ms=25)
    # a: +30% and +30 ms -> flagged; b: +10% -> no; c: +300% but only +30 ms ... also flagged
    assert [c.case_id for c in comp.slowdowns] == ["a", "c"]
    comp = compare_runs(base, cand, latency_threshold_pct=20, min_latency_delta_ms=31)
    assert comp.slowdowns == []
    assert comp.latency.baseline_median_ms == 100
    assert comp.latency.candidate_median_ms == 130
    assert comp.latency.change_pct == pytest.approx(30.0)


def test_latency_missing_durations_are_skipped():
    comp = compare_runs(
        make_run("b", case("a", "pass", duration=None)), make_run("c", case("a", "pass"))
    )
    assert comp.latency is None
    assert "Latency: no timing data" in render_comparison(comp)


def test_empty_runs_compare_cleanly():
    comp = compare_runs(make_run("b"), make_run("c"))
    assert comp.cases == [] and comp.regressions == []
    assert "NO REGRESSIONS" in render_comparison(comp)


# --- rendering ------------------------------------------------------------------


@pytest.fixture
def comparison():
    base = make_run("base", case("reg", "pass"), case("fix", "fail"), case("chg", "pass", "1|2"))
    cand = make_run(
        "cand",
        case("reg", "fail", reason="missing field(s): x|y"),
        case("fix", "pass"),
        case("chg", "pass", "3"),
    )
    return compare_runs(base, cand)


def test_text_report(comparison):
    text = render_comparison(comparison, "text")
    assert "REGRESSIONS FOUND (1)" in text
    assert "reg [exact] pass -> fail: missing field(s): x|y" in text
    assert "Passing with changed output (review, not a regression) (1):" in text


def test_markdown_report_escapes_pipes_and_orders_regressions_first(comparison):
    md = render_comparison(comparison, "markdown")
    rows = [line for line in md.splitlines() if line.startswith("| `")]
    assert rows[0].startswith("| `reg`")
    assert "missing field(s): x\\|y" in md
    assert "- Result: **REGRESSIONS FOUND (1)**" in md


def test_json_report_is_machine_readable(comparison):
    data = json.loads(render_comparison(comparison, "json"))
    assert data["format"] == "modeldiff.comparison/v1"
    assert data["summary"]["regression"] == 1
    assert data["summary"]["behavior_changes"] == 1
    assert data["cases"][0]["duration_delta_ms"] == 0.0


def test_run_report_formats():
    run = make_run("cfg", case("a", "pass"), case("b", "fail", reason="nope"))
    assert render_run(run).startswith("s @ cfg: 1/2 passed, 1 failed, 0 errors")
    assert "| `b` | exact | fail | nope |" in render_run(run, "markdown")
    assert json.loads(render_run(run, "json"))["config"]["id"] == "cfg"


def test_junit_report_is_valid_xml_and_classifies_cases():
    base = make_run(
        "b",
        case("reg", "pass"),
        case("err", "pass"),
        case("fix", "fail"),
        case("still", "fail"),
        case("same", "pass", "baseline output"),
        case("gone", "pass"),
    )
    cand = make_run(
        "c",
        case("reg", "fail", reason="bad <value> & more"),
        case("err", "error", reason="execution error"),
        case("fix", "pass"),
        case("still", "error", reason="still failing"),
        case("same", "pass", "candidate output"),
        case("new", "pass"),
        case("newerr", "error", reason="new execution error"),
    )

    comp = compare_runs(base, cand)
    root = ET.fromstring(render_comparison(comp, "junit"))

    cases = {item.attrib["name"]: item for item in root.findall("testcase")}

    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "8"
    assert root.attrib["failures"] == "2"
    assert root.attrib["errors"] == "2"
    assert root.attrib["skipped"] == "1"

    assert cases["reg"].find("failure") is not None
    assert cases["err"].find("error") is not None
    assert cases["still"].find("failure") is not None
    assert cases["fix"].find("failure") is None
    assert cases["gone"].find("skipped") is not None
    assert cases["newerr"].find("error") is not None

    failure = cases["reg"].find("failure")
    assert failure is not None
    assert failure.text == "bad <value> & more"
    system_out = cases["same"].find("system-out")
    assert system_out is not None
    assert system_out.text == ("Output changed between baseline and candidate; both cases passed.")


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_candidate_warnings_show_only_new_warnings_as_new(fmt):
    base, cand = case("warned", "pass"), case("warned", "pass")
    base.warnings = ["existing warning"]
    cand.warnings = ["existing warning", "latency limit exceeded"]
    report = render_comparison(compare_runs(make_run("b", base), make_run("c", cand)), fmt)
    assert "Warnings" in report
    assert "warned" in report
    assert "existing warning (new)" not in report
    assert "existing warning" in report
    assert "latency limit exceeded (new)" in report


def test_comparison_json_preserves_both_warning_lists():
    base, cand = case("warned", "pass"), case("warned", "pass")
    base.warnings = ["old warning"]
    cand.warnings = ["new warning"]
    data = json.loads(
        render_comparison(compare_runs(make_run("b", base), make_run("c", cand)), "json")
    )
    assert data["cases"][0]["baseline_warnings"] == ["old warning"]
    assert data["cases"][0]["candidate_warnings"] == ["new warning"]


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_no_candidate_warnings_adds_no_warning_section(fmt):
    base, cand = case("clean", "pass"), case("clean", "pass")
    base.warnings = ["resolved warning"]
    assert "Warnings" not in render_comparison(
        compare_runs(make_run("b", base), make_run("c", cand)), fmt
    )


def test_warning_on_case_missing_from_baseline_is_new():
    cand = case("new-case", "pass")
    cand.warnings = ["latency limit exceeded"]
    comp = compare_runs(make_run("b"), make_run("c", cand))
    assert comp.cases[0].baseline_warnings == []
    assert "latency limit exceeded (new)" in render_comparison(comp)


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_same_warning_in_both_runs_is_not_new(fmt):
    base, cand = case("same", "pass"), case("same", "pass")
    base.warnings = ["existing warning"]
    cand.warnings = ["existing warning"]
    report = render_comparison(compare_runs(make_run("b", base), make_run("c", cand)), fmt)
    assert "existing warning" in report
    assert "(new)" not in report


def test_warnings_on_missing_candidate_are_preserved_in_json():
    base = case("gone", "pass")
    base.warnings = ["old warning"]
    comp = compare_runs(make_run("b", base), make_run("c"))
    assert comp.to_dict()["cases"][0]["baseline_warnings"] == ["old warning"]
    assert comp.to_dict()["cases"][0]["candidate_warnings"] == []
    assert comp.regressions == []
