"""Run configuration files.

A config names one side of a comparison (for example ``baseline`` or
``llama3.1-8b-q4``) and says which provider produces its outputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from modeldiff.cases import read_document
from modeldiff.errors import ValidationError

CAPTURE_MODES = ("full", "hash", "none")

CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "provider"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"},
        "provider": {"enum": ["replay", "ollama", "openai_compatible"]},
        "description": {"type": "string"},
        "capture_output": {"enum": list(CAPTURE_MODES)},
        "metadata": {"type": "object"},
        # replay
        "outputs": {"type": "string", "minLength": 1},
        # ollama / openai_compatible
        "model": {"type": "string", "minLength": 1},
        "base_url": {"type": "string", "pattern": r"^https?://"},
        "options": {"type": "object"},
        "format": {},
        "system": {"type": "string"},
        "timeout_s": {"type": "number", "exclusiveMinimum": 0},
        # openai_compatible
        "api_key_env": {"type": "string", "minLength": 1},
    },
    "allOf": [
        {
            "if": {"properties": {"provider": {"const": "replay"}}},
            "then": {"required": ["outputs"]},
        },
        {
            "if": {"properties": {"provider": {"const": "ollama"}}},
            "then": {"required": ["model"]},
        },
        {
            "if": {"properties": {"provider": {"const": "openai_compatible"}}},
            "then": {"required": ["model", "base_url"]},
        },
    ],
}

# Keys whose values must never be written to result files.
SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)


@dataclass(frozen=True)
class RunConfig:
    id: str
    provider: str
    settings: dict[str, Any] = field(default_factory=dict)
    capture_output: str = "full"
    base_dir: Path = Path(".")

    def public_dict(self) -> dict[str, Any]:
        """The config as recorded in result files, with secret-looking keys removed."""
        return redact({"id": self.id, "provider": self.provider, **self.settings})


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "<redacted>" if SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def parse_config(data: Any, source: str = "<memory>", base_dir: Path = Path(".")) -> RunConfig:
    if not isinstance(data, dict):
        raise ValidationError(source, ["top level must be a mapping"])
    validator = jsonschema.Draft202012Validator(CONFIG_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        problems = []
        for e in errors:
            where = "/".join(str(p) for p in e.absolute_path)
            problems.append(f"{where}: {e.message}" if where else e.message)
        raise ValidationError(source, problems)
    settings = {k: v for k, v in data.items() if k not in ("id", "provider", "capture_output")}
    return RunConfig(
        id=data["id"],
        provider=data["provider"],
        settings=settings,
        capture_output=data.get("capture_output", "full"),
        base_dir=base_dir,
    )


def load_config(path: str | Path) -> RunConfig:
    path = Path(path)
    return parse_config(read_document(path), str(path), path.parent)
