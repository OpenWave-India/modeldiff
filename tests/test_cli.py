from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from modeldiff.cases import read_document
from modeldiff.cli import main


@pytest.fixture
def quickstart(example_dir):
    return {
        "suite": str(example_dir / "suite.yaml"),
        "plugin": str(example_dir / "checks.py"),
        "baseline": str(example_dir / "configs" / "baseline.yaml"),
        "candidate": str(example_dir / "configs" / "candidate.yaml"),
    }


@pytest.fixture
def runs(tmp_path, quickstart):
    base, cand = tmp_path / "base.json", tmp_path / "cand.json"
    q = quickstart
    assert (
        main(["run", q["suite"], "-c", q["baseline"], "-o", str(base), "--plugin", q["plugin"]])
        == 0
    )
    assert (
        main(["run", q["suite"], "-c", q["candidate"], "-o", str(cand), "--plugin", q["plugin"]])
        == 0
    )
    return base, cand


def test_validate_ok(quickstart, capsys):
    assert main(["validate", quickstart["suite"], "--plugin", quickstart["plugin"]]) == 0
    assert "with 9 cases" in capsys.readouterr().out


def test_validate_without_plugin_explains_how_to_fix(quickstart, capsys):
    assert main(["validate", quickstart["suite"]]) == 2
    err = capsys.readouterr().err
    assert "custom check 'max_words' is not registered" in err and "--plugin" in err


@pytest.mark.parametrize(
    ("kind", "document"),
    [
        (
            "suite",
            {"suite": "example", "cases": [{"id": "greeting", "type": "exact", "input": "hi"}]},
        ),
        ("config", {"id": "replay", "provider": "replay", "outputs": "outputs.json"}),
        (
            "result",
            {
                "format": "modeldiff.run/v1",
                "suite": "example",
                "suite_sha256": "a" * 64,
                "config": {"id": "replay", "provider": "replay"},
                "started_at": "2026-01-01T00:00:00Z",
                "results": [],
            },
        ),
    ],
)
def test_schema_command_emits_valid_schema(kind, document, capsys):
    assert main(["schema", kind]) == 0
    schema = json.loads(capsys.readouterr().out)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"].startswith("ModelDiff")
    Draft202012Validator(schema).validate(document)


def test_schema_suite_accepts_quickstart(quickstart, capsys):
    assert main(["schema", "suite"]) == 0
    schema = json.loads(capsys.readouterr().out)
    Draft202012Validator(schema).validate(read_document(Path(quickstart["suite"])))


def test_validate_invalid_file_exit_2(write_yaml, capsys):
    path = write_yaml("s.yaml", {"suite": "s", "cases": []})
    assert main(["validate", str(path)]) == 2
    assert "suite contains no cases" in capsys.readouterr().err


def test_plugin_file_can_be_loaded_twice(quickstart):
    # Regression: re-executing a plugin file re-registered its checks with new
    # function objects and raised "already registered".
    for _ in range(2):
        assert main(["validate", quickstart["suite"], "--plugin", quickstart["plugin"]]) == 0


def test_missing_plugin_file(quickstart, capsys):
    assert main(["validate", quickstart["suite"], "--plugin", "nope/checks.py"]) == 2
    assert "plugin file not found" in capsys.readouterr().err


def test_missing_plugin_module(quickstart, capsys):
    assert main(["validate", quickstart["suite"], "--plugin", "no_such_module_xyz"]) == 2
    assert "cannot import plugin module" in capsys.readouterr().err


def test_run_writes_results(runs):
    base, _ = runs
    data = json.loads(base.read_text(encoding="utf-8"))
    assert data["config"]["id"] == "baseline-q8"
    assert len(data["results"]) == 9


def test_run_fail_on_failure(tmp_path, quickstart):
    q = quickstart
    out = str(tmp_path / "r.json")
    args = ["run", q["suite"], "-c", q["candidate"], "-o", out, "--plugin", q["plugin"]]
    assert main(args + ["--fail-on-failure"]) == 1


def test_run_tag_filter(tmp_path, quickstart):
    q = quickstart
    out = tmp_path / "r.json"
    assert (
        main(
            [
                "run",
                q["suite"],
                "-c",
                q["baseline"],
                "-o",
                str(out),
                "--tag",
                "tools",
                "--plugin",
                q["plugin"],
            ]
        )
        == 0
    )
    assert [r["case_id"] for r in json.loads(out.read_text())["results"]] == [
        "tool-refund",
        "tool-lookup",
    ]
    assert (
        main(
            [
                "run",
                q["suite"],
                "-c",
                q["baseline"],
                "-o",
                str(out),
                "--tag",
                "nope",
                "--plugin",
                q["plugin"],
            ]
        )
        == 2
    )


def test_run_capture_override(tmp_path, quickstart):
    q = quickstart
    out = tmp_path / "r.json"
    assert (
        main(
            [
                "run",
                q["suite"],
                "-c",
                q["baseline"],
                "-o",
                str(out),
                "--capture",
                "hash",
                "--plugin",
                q["plugin"],
            ]
        )
        == 0
    )
    data = json.loads(out.read_text())
    assert data["capture_output"] == "hash"
    assert all(r["output"] is None and r["output_sha256"] for r in data["results"])


def test_run_invalid_config(write_yaml, quickstart, tmp_path, capsys):
    cfg = write_yaml("c.yaml", {"id": "x", "provider": "replay"})
    assert (
        main(
            [
                "run",
                quickstart["suite"],
                "-c",
                str(cfg),
                "-o",
                str(tmp_path / "r.json"),
                "--plugin",
                quickstart["plugin"],
            ]
        )
        == 2
    )
    assert "'outputs' is a required property" in capsys.readouterr().err


def test_compare_detects_quickstart_regressions(runs, capsys):
    base, cand = runs
    assert main(["compare", str(base), str(cand)]) == 1
    out = capsys.readouterr().out
    assert "REGRESSIONS FOUND (4)" in out
    for case_id in ("ticket-json", "ticket-required-fields", "intent-billing", "tool-lookup"):
        assert f"{case_id} [" in out
    assert "sentiment-positive: 'neutral' -> 'positive'" in out


def test_compare_same_run_has_no_regressions(runs):
    base, _ = runs
    assert main(["compare", str(base), str(base)]) == 0


def test_compare_exit_zero_and_markdown_export(runs, tmp_path):
    base, cand = runs
    report = tmp_path / "reports" / "diff.md"
    assert (
        main(
            [
                "compare",
                str(base),
                str(cand),
                "--format",
                "markdown",
                "-o",
                str(report),
                "--exit-zero",
            ]
        )
        == 0
    )
    assert report.read_text(encoding="utf-8").startswith(
        "# ModelDiff comparison: support-assistant"
    )


def test_compare_fail_on_latency(runs, tmp_path):
    base, _ = runs
    # Same outcomes as the baseline but every case twice as slow.
    data = json.loads(base.read_text())
    for r in data["results"]:
        r["duration_ms"] *= 2
    slow = tmp_path / "slow.json"
    slow.write_text(json.dumps(data))
    assert main(["compare", str(base), str(slow), "--latency-threshold", "50"]) == 0
    assert (
        main(["compare", str(base), str(slow), "--latency-threshold", "50", "--fail-on-latency"])
        == 1
    )
    assert (
        main(["compare", str(base), str(slow), "--latency-threshold", "150", "--fail-on-latency"])
        == 0
    )


def test_fail_on_latency_requires_threshold(runs):
    base, cand = runs
    with pytest.raises(SystemExit) as exc:
        main(["compare", str(base), str(cand), "--fail-on-latency"])
    assert exc.value.code == 2


def test_compare_missing_or_incomplete_results(runs, tmp_path, capsys):
    base, _ = runs
    assert main(["compare", str(base), str(tmp_path / "missing.json")]) == 2
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "modeldiff.run/v1", "results": []}))
    assert main(["compare", str(base), str(broken)]) == 2
    assert "is a required property" in capsys.readouterr().err


def test_report_command(runs, capsys):
    base, _ = runs
    assert main(["report", str(base)]) == 0
    assert "support-assistant @ baseline-q8: 8/9 passed" in capsys.readouterr().out


def test_no_command_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_offline_end_to_end(runs, monkeypatch, tmp_path, quickstart):
    """The example workflow must not touch the network."""

    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    q = quickstart
    out = tmp_path / "offline.json"
    assert (
        main(["run", q["suite"], "-c", q["candidate"], "-o", str(out), "--plugin", q["plugin"]])
        == 0
    )
    base, _ = runs
    assert main(["compare", str(base), str(out), "--exit-zero"]) == 0


def test_python_dash_m_entry_point(quickstart):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "modeldiff",
            "validate",
            quickstart["suite"],
            "--plugin",
            quickstart["plugin"],
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK:")
