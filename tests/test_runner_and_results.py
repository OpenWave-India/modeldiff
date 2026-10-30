from __future__ import annotations

import json

import pytest

from modeldiff.cases import parse_suite
from modeldiff.checks import register_check
from modeldiff.config import parse_config
from modeldiff.errors import ProviderError, ValidationError
from modeldiff.providers import ProviderResponse
from modeldiff.results import RunResult, load_run, save_run
from modeldiff.runner import run_suite


class DictProvider:
    """In-memory provider for tests."""

    name = "dict"

    def __init__(self, outputs, latency=None):
        self.outputs = outputs
        self.latency = latency or {}

    def generate(self, case):
        if case.id not in self.outputs:
            raise ProviderError(f"nothing for {case.id}")
        return ProviderResponse(self.outputs[case.id], {}, self.latency.get(case.id))

    def describe(self):
        return {"provider": "dict"}


SUITE = parse_suite(
    {
        "suite": "s",
        "cases": [
            {"id": "a", "type": "exact", "input": "x", "expected": "yes"},
            {"id": "b", "type": "classification", "input": "x", "expected": "pos"},
            {"id": "c", "type": "json", "input": "x", "performance": {"max_latency_ms": 100}},
        ],
    }
)


def config(**extra):
    return parse_config({"id": "cfg", "provider": "replay", "outputs": "unused.json", **extra})


def run(outputs, latency=None, enforce=False, **cfg):
    return run_suite(
        SUITE, config(**cfg), DictProvider(outputs, latency), enforce_performance=enforce
    )


def test_statuses_reasons_and_details():
    result = run({"a": "yes", "b": "neg", "c": "{}"})
    by_id = result.by_id()
    assert [r.status for r in result.results] == ["pass", "fail", "pass"]
    assert by_id["b"].details["predicted_label"] == "neg"
    assert result.counts() == {"pass": 2, "fail": 1, "error": 0}
    assert result.suite_sha256 == SUITE.sha256
    assert result.config["id"] == "cfg"
    assert result.runtime["provider"] == "dict"


def test_provider_error_becomes_error_status_and_run_continues():
    result = run({"a": "yes", "c": "{}"})
    b = result.by_id()["b"]
    assert b.status == "error" and b.error == "nothing for b" and b.output is None
    assert result.by_id()["c"].status == "pass"


def test_measured_vs_recorded_latency():
    result = run({"a": "yes", "b": "pos", "c": "{}"}, latency={"a": 42.0})
    a, b = result.by_id()["a"], result.by_id()["b"]
    assert (a.duration_ms, a.duration_source) == (42.0, "recorded")
    assert b.duration_source == "measured" and b.duration_ms >= 0


def test_latency_limit_warns_by_default_and_fails_when_enforced():
    outputs, latency = {"a": "yes", "b": "pos", "c": "{}"}, {"c": 250.0}
    c = run(outputs, latency).by_id()["c"]
    assert c.status == "pass"
    assert c.warnings == ["latency 250.0 ms exceeds max_latency_ms 100"]
    c = run(outputs, latency, enforce=True).by_id()["c"]
    assert c.status == "fail" and c.reason.startswith("latency 250.0 ms")


@pytest.mark.parametrize(
    ("mode", "has_output", "has_hash"),
    [("full", True, True), ("hash", False, True), ("none", False, False)],
)
def test_capture_modes(mode, has_output, has_hash):
    a = run({"a": "yes", "b": "pos", "c": "{}"}, capture_output=mode).by_id()["a"]
    assert (a.output is not None) == has_output
    assert (a.output_sha256 is not None) == has_hash


def test_custom_check_exception_is_recorded_not_raised():
    @register_check("boom")
    def boom(case, output):
        raise RuntimeError("kaput")

    suite = parse_suite(
        {"suite": "s", "cases": [{"id": "a", "type": "custom", "input": "x", "check": "boom"}]}
    )
    result = run_suite(suite, config(), DictProvider({"a": "x"}))
    a = result.results[0]
    assert a.status == "error" and a.error == "RuntimeError: kaput"


def test_memory_metric_is_process_rss_when_psutil_available():
    result = run({"a": "yes", "b": "pos", "c": "{}"})
    try:
        import psutil  # noqa: F401
    except ImportError:
        assert result.unavailable_metrics == ["memory"]
        assert result.results[0].memory is None
    else:
        assert result.unavailable_metrics == []
        assert set(result.results[0].memory) == {"process_rss_bytes", "process_rss_delta_bytes"}


# --- result files ---------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path):
    original = run({"a": "yes", "b": "neg", "c": "{}"})
    path = tmp_path / "nested" / "run.json"
    save_run(original, path)
    loaded = load_run(path)
    assert loaded == original
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "modeldiff.run/v1"


def test_saved_results_contain_no_secrets(tmp_path):
    result = run({"a": "yes", "b": "pos", "c": "{}"}, metadata={"api_key": "sk-live-abc"})
    save_run(result, tmp_path / "r.json")
    assert "sk-live-abc" not in (tmp_path / "r.json").read_text(encoding="utf-8")


def test_load_rejects_wrong_format_and_missing_fields(tmp_path):
    data = run({"a": "yes", "b": "pos", "c": "{}"}).to_dict()
    data["format"] = "something/v9"
    del data["results"][0]["status"]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_run(path)
    joined = "\n".join(exc.value.problems)
    assert "format" in joined and "results/0: 'status' is a required property" in joined


def test_load_rejects_invalid_status_and_duplicates():
    data = run({"a": "yes", "b": "pos", "c": "{}"}).to_dict()
    bad = json.loads(json.dumps(data))
    bad["results"][0]["status"] = "passed"
    with pytest.raises(ValidationError, match="status"):
        RunResult.from_dict(bad)
    dup = json.loads(json.dumps(data))
    dup["results"][1]["case_id"] = "a"
    with pytest.raises(ValidationError, match="duplicate case_id"):
        RunResult.from_dict(dup)


def test_load_malformed_and_missing_files(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValidationError, match="malformed JSON"):
        load_run(path)
    with pytest.raises(ValidationError, match="file not found"):
        load_run(tmp_path / "missing.json")


def test_load_ignores_unknown_fields_for_forward_compatibility():
    data = run({"a": "yes", "b": "pos", "c": "{}"}).to_dict()
    data["extra_run_field"] = 1
    data["results"][0]["extra_case_field"] = 2
    assert RunResult.from_dict(data).results[0].case_id == "a"


def test_case_hashes_survive_save_load_and_cover_provider_errors(tmp_path):
    result = run({"a": "yes", "c": "{}"})
    path = tmp_path / "run.json"
    save_run(result, path)
    loaded = load_run(path)
    assert [r.case_sha256 for r in loaded.results] == [c.sha256 for c in SUITE.cases]
    assert loaded.by_id()["b"].status == "error"


def test_case_hash_is_optional_for_older_files():
    data = run({"a": "yes", "b": "pos", "c": "{}"}).to_dict()
    for result in data["results"]:
        result.pop("case_sha256", None)
    assert all(result.case_sha256 is None for result in RunResult.from_dict(data).results)


def test_invalid_case_hash_is_rejected():
    data = run({"a": "yes", "b": "pos", "c": "{}"}).to_dict()
    data["results"][0]["case_sha256"] = "not-a-hash"
    with pytest.raises(ValidationError, match="case_sha256"):
        RunResult.from_dict(data)
