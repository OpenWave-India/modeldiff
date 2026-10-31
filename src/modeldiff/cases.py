"""Loading and validating test suite files.

A suite is a YAML or JSON document with a ``suite`` name and a list of
``cases``. Structure is checked with a JSON Schema; type-specific rules
(for example, that a ``json`` case's ``schema`` is itself a valid JSON
Schema) are checked by the matching check in :mod:`modeldiff.checks`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from modeldiff.errors import ValidationError

CASE_TYPES = ("exact", "json", "classification", "required_fields", "tool_call", "custom")

SUITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["suite", "cases"],
    "additionalProperties": False,
    "properties": {
        "suite": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "cases": {"type": "array", "items": {"$ref": "#/$defs/case"}},
    },
    "$defs": {
        "case": {
            "type": "object",
            "required": ["id", "type", "input"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*$"},
                "type": {"enum": list(CASE_TYPES)},
                "input": {"type": "string"},
                "expected": {},
                "schema": {"type": "object"},
                "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "required": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "values": {"type": "object"},
                "check": {"type": "string", "minLength": 1},
                "params": {"type": "object"},
                "options": {"type": "object"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
                "generation": {"type": "object"},
                "performance": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"max_latency_ms": {"type": "number", "exclusiveMinimum": 0}},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class TestCase:
    """One test case from a suite file."""

    __test__ = False  # keep pytest from collecting this class

    id: str
    type: str
    input: str
    expected: Any = None
    schema: dict[str, Any] | None = None
    labels: list[str] | None = None
    required: list[str] | None = None
    values: dict[str, Any] | None = None
    check: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        """Hash the normalized definition independently of other suite cases."""
        canonical = json.dumps(_case_to_dict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCase:
        return cls(**data)


@dataclass(frozen=True)
class Suite:
    """A validated set of test cases."""

    name: str
    cases: list[TestCase]
    description: str = ""
    source: str = "<memory>"

    @property
    def sha256(self) -> str:
        """Hash of the normalized case definitions.

        Formatting and comments in the source file do not affect it, so two
        runs can be checked for using the same cases and rules.
        """
        payload = [_case_to_dict(c) for c in self.cases]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def filter(self, tags: list[str] | None = None) -> Suite:
        """Return a suite restricted to cases carrying any of ``tags``."""
        if not tags:
            return self
        wanted = set(tags)
        kept = [c for c in self.cases if wanted.intersection(c.tags)]
        return Suite(self.name, kept, self.description, self.source)


def _case_to_dict(case: TestCase) -> dict[str, Any]:
    return {k: v for k, v in case.__dict__.items() if v not in (None, {}, [])}


def read_document(path: Path) -> Any:
    """Read a YAML or JSON file, raising ValidationError on syntax errors."""
    source = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError(source, ["file not found"]) from None
    except OSError as exc:
        raise ValidationError(source, [f"cannot read file: {exc.strerror}"]) from None

    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            raise ValidationError(source, [msg]) from None
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            problem = getattr(exc, "problem", None) or str(exc)
            raise ValidationError(source, [f"malformed YAML{where}: {problem}"]) from None
    raise ValidationError(
        source, [f"unsupported file extension {path.suffix!r}; use .yaml, .yml or .json"]
    )


def format_schema_error(error: jsonschema.ValidationError, data: Any) -> str:
    """Turn a jsonschema error into a message that points at the offending case."""
    path = list(error.absolute_path)
    location = "/".join(str(p) for p in path) or "<root>"
    if len(path) >= 2 and path[0] == "cases" and isinstance(path[1], int):
        case = data["cases"][path[1]]
        case_id = case.get("id") if isinstance(case, dict) else None
        label = f"case #{path[1] + 1}" + (f" ({case_id!r})" if isinstance(case_id, str) else "")
        rest = "/".join(str(p) for p in path[2:])
        location = label + (f" field {rest!r}" if rest else "")
    if error.validator == "additionalProperties":
        return f"{location}: {error.message}"
    if error.validator == "enum" and path and path[-1] == "type":
        return (
            f"{location}: unknown type {error.instance!r}; expected one of {', '.join(CASE_TYPES)}"
        )
    return f"{location}: {error.message}"


def parse_suite(data: Any, source: str = "<memory>") -> Suite:
    """Validate an already-parsed suite document and build a Suite."""
    if not isinstance(data, dict):
        raise ValidationError(source, ["top level must be a mapping with 'suite' and 'cases' keys"])

    validator = jsonschema.Draft202012Validator(SUITE_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        raise ValidationError(source, [format_schema_error(e, data) for e in errors])

    if not data["cases"]:
        raise ValidationError(source, ["suite contains no cases"])

    # Imported here to avoid a cycle: checks need TestCase.
    from modeldiff.checks import validate_case

    problems: list[str] = []
    seen: dict[str, int] = {}
    cases: list[TestCase] = []
    for index, raw in enumerate(data["cases"], start=1):
        case = TestCase.from_dict(raw)
        if case.id in seen:
            problems.append(
                f"case #{index}: duplicate id {case.id!r} (first used by case #{seen[case.id]})"
            )
        else:
            seen[case.id] = index
        problems.extend(f"case #{index} ({case.id!r}): {p}" for p in validate_case(case))
        cases.append(case)

    if problems:
        raise ValidationError(source, problems)
    return Suite(data["suite"], cases, data.get("description", ""), source)


def load_suite(path: str | Path) -> Suite:
    """Load and validate a suite file."""
    path = Path(path)
    return parse_suite(read_document(path), str(path))
