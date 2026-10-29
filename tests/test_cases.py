from __future__ import annotations

import pytest

from modeldiff.cases import load_suite, parse_suite
from modeldiff.errors import ValidationError


def suite(*cases, name="s"):
    return {"suite": name, "cases": list(cases)}


EXACT = {"id": "a", "type": "exact", "input": "hi", "expected": "hello"}


def problems_of(data) -> list[str]:
    with pytest.raises(ValidationError) as exc:
        parse_suite(data, "test.yaml")
    return exc.value.problems


def test_loads_example_suite(example_dir):
    import modeldiff.checks as checks

    @checks.register_check("max_words")
    def _(case, output):
        return True

    s = load_suite(example_dir / "suite.yaml")
    assert s.name == "support-assistant"
    assert len(s.cases) == 9
    assert {c.type for c in s.cases} == {
        "exact",
        "json",
        "classification",
        "required_fields",
        "tool_call",
        "custom",
    }


def test_yaml_and_json_files_give_same_hash(write_yaml, write_json):
    data = suite(EXACT)
    assert (
        load_suite(write_yaml("s.yaml", data)).sha256
        == load_suite(write_json("s.json", data)).sha256
    )


def test_hash_changes_when_case_changes():
    a = parse_suite(suite(EXACT))
    b = parse_suite(suite({**EXACT, "expected": "hello!"}))
    assert a.sha256 != b.sha256


def test_malformed_json_reports_line_and_column(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"suite": "s",\n "cases": [}', encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_suite(path)
    assert "malformed JSON at line 2" in exc.value.problems[0]


def test_malformed_yaml_reports_location(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("suite: s\ncases:\n  - id: a\n    input: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_suite(path)
    assert "malformed YAML at line" in exc.value.problems[0]


def test_missing_file(tmp_path):
    with pytest.raises(ValidationError, match="file not found"):
        load_suite(tmp_path / "nope.yaml")


def test_unsupported_extension(tmp_path):
    path = tmp_path / "suite.txt"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError, match="unsupported file extension"):
        load_suite(path)


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError, match="top level must be a mapping"):
        load_suite(path)


def test_empty_suite_is_rejected():
    assert problems_of(suite()) == ["suite contains no cases"]


def test_missing_required_case_fields_name_the_case():
    problems = problems_of(suite({"id": "a", "type": "exact"}))
    assert problems == ["case #1 ('a'): 'input' is a required property"]


def test_missing_suite_name():
    problems = problems_of({"cases": [EXACT]})
    assert any("'suite' is a required property" in p for p in problems)


def test_unknown_type_lists_supported_types():
    problems = problems_of(suite({**EXACT, "type": "semantic"}))
    assert "unknown type 'semantic'" in problems[0]
    assert "tool_call" in problems[0]


def test_unknown_field_is_rejected_to_catch_typos():
    problems = problems_of(suite({**EXACT, "expectd": "x"}))
    assert "'expectd' was unexpected" in problems[0]


def test_invalid_case_id():
    problems = problems_of(suite({**EXACT, "id": "has space"}))
    assert "case #1" in problems[0] and "id" in problems[0]


def test_duplicate_ids():
    problems = problems_of(suite(EXACT, EXACT))
    assert problems == ["case #2: duplicate id 'a' (first used by case #1)"]


def test_reports_all_problems_at_once():
    problems = problems_of(
        suite(
            {**EXACT, "expected": 3},
            {"id": "b", "type": "json", "input": "x", "schema": {"type": "nonsense"}},
        )
    )
    assert len(problems) == 2


def test_exact_requires_string_expected():
    assert "'expected' must be a string" in problems_of(suite({**EXACT, "expected": None}))[0]


def test_json_case_with_invalid_schema():
    problems = problems_of(suite({"id": "j", "type": "json", "input": "x", "schema": {"type": 12}}))
    assert "'schema' is not a valid JSON Schema" in problems[0]


def test_classification_expected_must_be_in_labels():
    case = {
        "id": "c",
        "type": "classification",
        "input": "x",
        "expected": "maybe",
        "labels": ["yes", "no"],
    }
    assert "expected label 'maybe' is not in 'labels'" in problems_of(suite(case))[0]


def test_unknown_option_is_rejected():
    problems = problems_of(suite({**EXACT, "options": {"ignorecase": True}}))
    assert "unknown option(s) ignorecase" in problems[0]


def test_exact_known_options_accepted():
    s = parse_suite(
        suite(
            {
                **EXACT,
                "options": {
                    "strip": True,
                    "ignore_case": True,
                    "normalize_whitespace": True,
                },
            }
        ),
        "test.yaml",
    )
    assert len(s.cases) == 1


def test_json_rejects_invalid_extract_option_value():
    problems = problems_of(
        suite({"id": "j", "type": "json", "input": "x", "options": {"extract": "foo"}})
    )
    assert any(
        "invalid value 'foo' for option 'extract' (supported: code_fence)" in p for p in problems
    )


def test_json_accepts_code_fence_extract_option():
    s = parse_suite(
        suite({"id": "j", "type": "json", "input": "x", "options": {"extract": "code_fence"}})
    )
    assert s.cases[0].options == {"extract": "code_fence"}


def test_required_fields_needs_something_to_check():
    problems = problems_of(suite({"id": "r", "type": "required_fields", "input": "x"}))
    assert "needs 'required', 'values', or both" in problems[0]


def test_tool_call_needs_name():
    problems = problems_of(
        suite({"id": "t", "type": "tool_call", "input": "x", "expected": {"args": {}}})
    )
    assert "string 'name'" in problems[0]


def test_tool_call_rejects_unknown_expected_keys():
    case = {"id": "t", "type": "tool_call", "input": "x", "expected": {"name": "f", "args": {}}}
    assert "unknown key(s) in 'expected': args" in problems_of(suite(case))[0]


def test_unregistered_custom_check_suggests_plugin():
    problems = problems_of(suite({"id": "c", "type": "custom", "input": "x", "check": "nope"}))
    assert "not registered" in problems[0] and "--plugin" in problems[0]


def test_performance_limit_must_be_positive():
    problems = problems_of(suite({**EXACT, "performance": {"max_latency_ms": 0}}))
    assert "performance" in problems[0]


def test_filter_by_tag():
    s = parse_suite(suite({**EXACT, "tags": ["smoke"]}, {**EXACT, "id": "b"}))
    assert [c.id for c in s.filter(["smoke"]).cases] == ["a"]
    assert s.filter(None) is s
