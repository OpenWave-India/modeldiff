"""Must-pass release gates, including old-suite and old-result compatibility."""

import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from modeldiff.cases import parse_suite
from modeldiff.cli import main
from modeldiff.compare import compare_runs
from modeldiff.config import parse_config
from modeldiff.errors import ProviderError, ValidationError
from modeldiff.providers import ProviderResponse
from modeldiff.report import render_comparison
from modeldiff.results import RunResult, load_run, save_run
from modeldiff.runner import run_suite


def result(case_id, status, mandatory=False):
    data = {
        "case_id": case_id,
        "type": "exact",
        "status": status,
        "timestamp": "2026-01-01T00:00:00Z",
        "must_pass": mandatory,
        "reason": "expected yes" if status == "fail" else None,
        "error": "provider unavailable" if status == "error" else None,
    }
    return data


def run(config_id, *cases):
    return RunResult.from_dict(
        {
            "format": "modeldiff.run/v1",
            "suite": "release",
            "suite_sha256": "a" * 64,
            "config": {"id": config_id, "provider": "replay"},
            "started_at": "2026-01-01T00:00:00Z",
            "results": list(cases),
        }
    )


def test_suite_flag_is_optional_boolean_and_preserves_legacy_hashes():
    definition = {"id": "critical", "type": "exact", "input": "x", "expected": "yes"}
    legacy = parse_suite({"suite": "s", "cases": [definition]})
    explicit_false = parse_suite({"suite": "s", "cases": [{**definition, "must_pass": False}]})
    required = parse_suite({"suite": "s", "cases": [{**definition, "must_pass": True}]})
    expected = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert legacy.cases[0].must_pass is False
    assert required.cases[0].must_pass is True
    assert legacy.cases[0].sha256 == expected
    assert explicit_false.sha256 == legacy.sha256
    assert required.sha256 != legacy.sha256


@pytest.mark.parametrize("value", ["true", 1, None, []])
def test_suite_rejects_non_boolean_flags(value):
    with pytest.raises(ValidationError, match="must_pass"):
        parse_suite(
            {
                "suite": "s",
                "cases": [
                    {
                        "id": "critical",
                        "type": "exact",
                        "input": "x",
                        "expected": "yes",
                        "must_pass": value,
                    }
                ],
            }
        )


def test_runner_and_result_files_preserve_flags_for_pass_fail_and_error(tmp_path):
    suite = parse_suite(
        {
            "suite": "release",
            "cases": [
                {"id": name, "type": "exact", "input": "x", "expected": "yes", "must_pass": True}
                for name in ("pass", "fail", "error")
            ],
        }
    )

    class Provider:
        name = "fixture"

        def generate(self, case):
            if case.id == "error":
                raise ProviderError("offline fixture error")
            return ProviderResponse("yes" if case.id == "pass" else "no", {})

        def describe(self):
            return {"provider": "fixture"}

    config = parse_config({"id": "c", "provider": "replay", "outputs": "unused.json"})
    actual = run_suite(suite, config, Provider())
    path = tmp_path / "run.json"
    save_run(actual, path)
    restored = load_run(path)
    assert [r.status for r in restored.results] == ["pass", "fail", "error"]
    assert all(r.must_pass for r in restored.results)
    legacy = actual.to_dict()
    for record in legacy["results"]:
        del record["must_pass"]
    assert not any(r.must_pass for r in RunResult.from_dict(legacy).results)
    legacy["results"][0]["must_pass"] = "true"
    with pytest.raises(ValidationError, match="must_pass"):
        RunResult.from_dict(legacy)


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        ("pass", "fail", "regression"),
        ("fail", "fail", "still failing"),
        ("pass", "error", "error"),
        ("error", "error", "error"),
        ("pass", None, "missing"),
    ],
)
def test_nonpassing_required_cases_block_compare_and_explain_why(
    tmp_path, capsys, before, after, reason
):
    baseline = run("b", result("critical", before, True))
    candidate = run("c", *([result("critical", after, True)] if after else []))
    comparison = compare_runs(baseline, candidate)
    assert [c.case_id for c in comparison.must_pass_blockers] == ["critical"]
    assert comparison.must_pass_blockers[0].blocking_reason == reason
    for fmt in ("text", "markdown", "json", "junit"):
        report = render_comparison(comparison, fmt)
        assert "critical" in report and reason in report
        if fmt in ("text", "markdown", "junit"):
            assert "Blocked by must-pass cases" in report
    base_path, candidate_path = tmp_path / "b.json", tmp_path / "c.json"
    save_run(baseline, base_path)
    save_run(candidate, candidate_path)
    assert main(["compare", str(base_path), str(candidate_path)]) == 1
    assert "Blocked by must-pass cases" in capsys.readouterr().out
    assert main(["compare", str(base_path), str(candidate_path), "--exit-zero"]) == 0


def test_improving_run_lists_critical_regression_first_in_all_reports():
    baseline = run(
        "b", result("fixed-a", "fail"), result("fixed-b", "fail"), result("critical", "pass", True)
    )
    candidate = run(
        "c", result("fixed-a", "pass"), result("fixed-b", "pass"), result("critical", "fail", True)
    )
    comp = compare_runs(baseline, candidate)
    assert len(comp.by_category("fixed")) == 2
    for fmt in ("text", "markdown"):
        report = render_comparison(comp, fmt)
        assert report.index("Blocked by must-pass cases") < report.index("fixed-a")
    data = json.loads(render_comparison(comp, "json"))
    assert data["blocked_by_must_pass"][0]["case_id"] == "critical"
    assert data["blocked_by_must_pass"][0]["reason"] == "regression"
    assert data["cases"][0]["case_id"] == "critical"
    xml = ET.fromstring(render_comparison(comp, "junit"))
    assert xml.find("testcase").get("name") == "critical"
    assert xml.find("testcase/failure").get("type") == "must_pass"
    assert xml.get("tests") == "3" and xml.get("failures") == "1"


def test_ordinary_still_failing_cases_and_legacy_results_do_not_block(tmp_path):
    old = result("ordinary", "fail")
    del old["must_pass"]
    baseline, candidate = run("b", old), run("c", old)
    comp = compare_runs(baseline, candidate)
    assert comp.must_pass_blockers == []
    assert "Blocked by must-pass cases" not in render_comparison(comp)
    paths = [tmp_path / "b.json", tmp_path / "c.json"]
    for item, path in zip((baseline, candidate), paths, strict=True):
        save_run(item, path)
    assert main(["compare", *map(str, paths)]) == 0


def test_required_success_does_not_block_but_candidate_cannot_relax_baseline_flag():
    assert (
        compare_runs(
            run("b", result("critical", "fail", True)), run("c", result("critical", "pass", True))
        ).must_pass_blockers
        == []
    )
    comp = compare_runs(
        run("b", result("critical", "fail", True)), run("c", result("critical", "fail", False))
    )
    assert len(comp.must_pass_blockers) == 1
    added = compare_runs(run("b"), run("c", result("critical", "fail", True)))
    assert len(added.must_pass_blockers) == 1


def test_missing_required_junit_case_fails_instead_of_being_skipped():
    comp = compare_runs(run("b", result("critical", "pass", True)), run("c"))
    xml = ET.fromstring(render_comparison(comp, "junit"))
    assert xml.get("failures") == "1" and xml.get("skipped") == "0"
    assert xml.find("testcase/failure").get("type") == "must_pass"
