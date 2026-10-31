from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from modeldiff.cases import read_document
from modeldiff.cli import main

EXTRACTION = Path(__file__).resolve().parents[1] / "examples" / "extraction"


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


def test_validate_multiple_valid_files(write_yaml, capsys):
    paths = [
        write_yaml(
            f"{name}.yaml",
            {
                "suite": name,
                "cases": [{"id": "c1", "type": "exact", "input": "hi", "expected": "hi"}],
            },
        )
        for name in ("first", "second")
    ]
    assert main(["validate", *map(str, paths)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"OK: {path} defines suite {name!r} with 1 cases (1 exact)"
        for path, name in zip(paths, ("first", "second"), strict=True)
    ]
    assert captured.err == ""


@pytest.mark.parametrize("invalid_index", [0, 1, 2])
def test_validate_reports_all_files_when_one_is_invalid(write_yaml, capsys, invalid_index):
    paths = [
        write_yaml(
            f"{index}.yaml",
            {
                "suite": f"suite-{index}",
                "cases": []
                if index == invalid_index
                else [{"id": "c1", "type": "exact", "input": "hi", "expected": "hi"}],
            },
        )
        for index in range(3)
    ]
    assert main(["validate", *map(str, paths)]) == 2
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 2
    for index, path in enumerate(paths):
        if index == invalid_index:
            assert f"error: {path} is invalid:\n  - suite contains no cases\n" == captured.err
        else:
            assert f"OK: {path} defines suite 'suite-{index}'" in captured.out


def test_validate_reports_missing_and_malformed_files_without_stopping(
    write_yaml, tmp_path, capsys
):
    missing = tmp_path / "missing.yaml"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    valid = write_yaml(
        "valid.yaml",
        {
            "suite": "valid",
            "cases": [{"id": "c1", "type": "exact", "input": "hi", "expected": "hi"}],
        },
    )
    assert main(["validate", str(missing), str(malformed), str(valid)]) == 2
    captured = capsys.readouterr()
    assert captured.err.count("error:") == 2
    assert captured.err.index(str(missing)) < captured.err.index(str(malformed))
    assert "file not found" in captured.err and "malformed JSON" in captured.err
    assert captured.out == f"OK: {valid} defines suite 'valid' with 1 cases (1 exact)\n"


def test_validate_plugin_applies_to_every_file(quickstart, write_yaml, capsys):
    second = write_yaml(
        "custom.yaml",
        {
            "suite": "custom",
            "cases": [{"id": "custom", "type": "custom", "input": "hi", "check": "max_words"}],
        },
    )
    assert (
        main(["validate", quickstart["suite"], str(second), "--plugin", quickstart["plugin"]]) == 0
    )
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 2
    assert f"OK: {quickstart['suite']}" in captured.out and f"OK: {second}" in captured.out
    assert captured.err == ""


def test_validate_single_file_message_stays_the_same(quickstart, capsys):
    assert main(["validate", quickstart["suite"], "--plugin", quickstart["plugin"]]) == 0
    assert capsys.readouterr().out == (
        f"OK: {quickstart['suite']} defines suite 'support-assistant' "
        "with 9 cases (3 classification, 1 custom, 1 exact, 1 json, 1 required_fields, 2 tool_call)\n"
    )


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


def test_extraction_example_reports_regressions_and_behavior_change(tmp_path, capsys):
    baseline = tmp_path / "extraction-baseline.json"
    candidate = tmp_path / "extraction-candidate.json"
    assert main(["validate", str(EXTRACTION / "suite.yaml")]) == 0
    assert (
        main(
            [
                "run",
                str(EXTRACTION / "suite.yaml"),
                "--config",
                str(EXTRACTION / "configs" / "baseline.yaml"),
                "--output",
                str(baseline),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "run",
                str(EXTRACTION / "suite.yaml"),
                "--config",
                str(EXTRACTION / "configs" / "candidate.yaml"),
                "--output",
                str(candidate),
            ]
        )
        == 0
    )
    assert main(["compare", str(baseline), str(candidate)]) == 1
    out = capsys.readouterr().out
    assert "REGRESSIONS FOUND (2)" in out
    assert "invoice-json [json] pass -> fail" in out
    assert "billing-address [required_fields] pass -> fail" in out
    assert "Passing with changed output (review, not a regression) (1)" in out
    assert "payment-status [json] pass -> pass" in out


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


@pytest.mark.parametrize(
    ("extra", "tags", "expected_warning"),
    [
        ({"old-id": "unused", "another-old": "unused"}, [], "another-old, old-id"),
        ({}, [], None),
        ({}, ["--tag", "selected"], None),
        ({"old-id": "unused"}, ["--tag", "selected"], "old-id"),
    ],
)
def test_replay_warns_only_for_ids_absent_from_full_suite(
    write_yaml, tmp_path, capsys, extra, tags, expected_warning
):
    suite = write_yaml(
        "suite.yaml",
        {
            "suite": "replay-warning",
            "cases": [
                {
                    "id": "keep",
                    "type": "exact",
                    "input": "x",
                    "expected": "ok",
                    "tags": ["selected"],
                },
                {"id": "filtered", "type": "exact", "input": "y", "expected": "ok"},
            ],
        },
    )
    outputs = write_yaml("outputs.yaml", {"outputs": {"keep": "ok", "filtered": "ok", **extra}})
    config = write_yaml(
        "config.yaml", {"id": "replay", "provider": "replay", "outputs": outputs.name}
    )
    result = tmp_path / "result.json"
    assert main(["run", str(suite), "-c", str(config), "-o", str(result), *tags]) == 0
    error = capsys.readouterr().err
    if expected_warning is None:
        assert error == ""
    else:
        assert error == f"warning: {outputs} has outputs for unknown case(s): {expected_warning}\n"
    records = json.loads(result.read_text())["results"]
    assert len(records) == (1 if tags else 2)
    assert all(record["status"] == "pass" for record in records)



def test_redirected_stdout_is_utf8_with_non_ascii_report(tmp_path):
    """A non-ASCII report must not crash when stdout is redirected (GH-24).

    With ``PYTHONIOENCODING`` forced to a non-UTF-8 code page, Python would
    encode redirected stdout with cp1252 and raise ``UnicodeEncodeError`` on
    Hindi text. The CLI must reconfigure its streams to UTF-8 and exit 0.
    """
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "suite: hindi\n"
        "cases:\n"
        "  - id: sentiment-hi\n"
        "    type: classification\n"
        '    input: "भावना बताइए"\n'
        "    expected: सकारात्मक\n"
        "    labels: [सकारात्मक, नकारात्मक]\n",
        encoding="utf-8",
    )
    outputs = tmp_path / "out.json"
    outputs.write_text(json.dumps({"outputs": {"sentiment-hi": "नकारात्मक"}}), encoding="utf-8")
    config = tmp_path / "cfg.yaml"
    config.write_text("id: hi\nprovider: replay\noutputs: out.json\n", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "modeldiff",
            "run",
            str(suite),
            "-c",
            str(config),
            "-o",
            str(tmp_path / "r.json"),
        ],
        capture_output=True,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8")
    assert "Results written to" in stdout
    assert "नकारात्मक" in stdout
 


def test_compare_junit_export(runs, tmp_path):
    base, cand = runs
    report = tmp_path / "reports" / "diff.xml"

    assert (
        main(
            [
                "compare",
                str(base),
                str(cand),
                "--format",
                "junit",
                "-o",
                str(report),
            ]
        )
        == 1
    )

    root = ET.parse(report).getroot()

    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "9"
    assert int(root.attrib["failures"]) > 0


def test_compare_full_and_tagged_run_uses_case_hashes(tmp_path, quickstart, capsys):
    base, tagged = tmp_path / "full.json", tmp_path / "tagged.json"
    q = quickstart
    for output, extra in [(base, []), (tagged, ["--tag", "smoke"])]:
        assert (
            main(
                [
                    "run",
                    q["suite"],
                    "-c",
                    q["baseline"],
                    "-o",
                    str(output),
                    "--plugin",
                    q["plugin"],
                    *extra,
                ]
            )
            == 0
        )
    capsys.readouterr()
    assert main(["compare", str(base), str(tagged)]) == 0
    report = capsys.readouterr()
    assert "Missing in candidate (8)" in report.out
    assert not report.err
    assert all(
        len(result["case_sha256"]) == 64 for result in json.loads(base.read_text())["results"]
    )


def test_compare_edited_case_names_definition_change(tmp_path, quickstart, capsys):
    import yaml

    from modeldiff.cases import load_suite

    q = quickstart
    suite = read_document(Path(q["suite"]))
    suite["cases"][0]["expected"] = "changed expected output"
    edited = tmp_path / "edited.yaml"
    edited.write_text(yaml.safe_dump(suite))
    base, candidate = tmp_path / "base.json", tmp_path / "edited.json"
    for path, output in [(q["suite"], base), (str(edited), candidate)]:
        assert (
            main(["run", path, "-c", q["baseline"], "-o", str(output), "--plugin", q["plugin"]])
            == 0
        )
    capsys.readouterr()
    assert main(["compare", str(base), str(candidate)]) == 2
    assert load_suite(edited).cases[0].id in capsys.readouterr().err

