"""Run result records and their JSON file format (``modeldiff.run/v1``)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from modeldiff.errors import ValidationError

RESULT_FORMAT = "modeldiff.run/v1"
STATUSES = ("pass", "fail", "error")

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["format", "suite", "suite_sha256", "config", "started_at", "results"],
    "properties": {
        "format": {"const": RESULT_FORMAT},
        "suite": {"type": "string"},
        "suite_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "config": {
            "type": "object",
            "required": ["id", "provider"],
            "properties": {"id": {"type": "string"}, "provider": {"type": "string"}},
        },
        "capture_output": {"enum": ["full", "hash", "none"]},
        "runtime": {"type": "object"},
        "started_at": {"type": "string"},
        "finished_at": {"type": ["string", "null"]},
        "unavailable_metrics": {"type": "array", "items": {"type": "string"}},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["case_id", "type", "status", "timestamp"],
                "properties": {
                    "case_id": {"type": "string"},
                    "case_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
                    "type": {"type": "string"},
                    "status": {"enum": list(STATUSES)},
                    "reason": {"type": ["string", "null"]},
                    "output": {"type": ["string", "null"]},
                    "output_sha256": {"type": ["string", "null"]},
                    "duration_ms": {"type": ["number", "null"], "minimum": 0},
                    "duration_source": {"enum": ["measured", "recorded", None]},
                    "memory": {"type": ["object", "null"]},
                    "details": {"type": "object"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                    "error": {"type": ["string", "null"]},
                    "timestamp": {"type": "string"},
                    "provider_metadata": {"type": "object"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


@dataclass
class CaseResult:
    case_id: str
    type: str
    status: str  # "pass", "fail", or "error" (no output could be evaluated)
    timestamp: str
    reason: str | None = None
    output: str | None = None
    output_sha256: str | None = None
    duration_ms: float | None = None
    duration_source: str | None = None  # "measured" or "recorded"
    memory: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    case_sha256: str | None = None


@dataclass
class RunResult:
    suite: str
    suite_sha256: str
    config: dict[str, Any]
    started_at: str
    results: list[CaseResult]
    capture_output: str = "full"
    runtime: dict[str, Any] = field(default_factory=dict)
    finished_at: str | None = None
    unavailable_metrics: list[str] = field(default_factory=list)
    format: str = RESULT_FORMAT

    @property
    def config_id(self) -> str:
        return self.config["id"]

    def by_id(self) -> dict[str, CaseResult]:
        return {r.case_id: r for r in self.results}

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATUSES, 0)
        for r in self.results:
            counts[r.status] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Keep "format" first so the file identifies itself at a glance.
        return {"format": data.pop("format"), **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "<memory>") -> RunResult:
        validator = jsonschema.Draft202012Validator(RESULT_SCHEMA)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
        if errors:
            problems = []
            for e in errors[:20]:
                where = "/".join(str(p) for p in e.absolute_path) or "<root>"
                problems.append(f"{where}: {e.message}")
            raise ValidationError(source, problems)
        ids = [r["case_id"] for r in data["results"]]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValidationError(source, [f"duplicate case_id(s): {', '.join(dupes)}"])
        fields = set(CaseResult.__dataclass_fields__)
        results = [
            CaseResult(**{k: v for k, v in r.items() if k in fields}) for r in data["results"]
        ]
        run_fields = set(cls.__dataclass_fields__) - {"results"}
        kwargs = {k: v for k, v in data.items() if k in run_fields}
        return cls(results=results, **kwargs)


def save_run(run: RunResult, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_run(path: str | Path) -> RunResult:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValidationError(str(path), ["file not found"]) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            str(path), [f"malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"]
        ) from None
    if not isinstance(data, dict):
        raise ValidationError(str(path), ["top level must be an object"])
    return RunResult.from_dict(data, str(path))
