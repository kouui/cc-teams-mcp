"""Shared path and serialization utilities."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

CLAUDE_DIR = Path.home() / ".claude"
TEAMS_DIR = CLAUDE_DIR / "teams"
TASKS_DIR = CLAUDE_DIR / "tasks"


def teams_dir(base_dir: Path | None = None) -> Path:
    """Return the teams directory, optionally under a custom base for testing."""
    return (base_dir / "teams") if base_dir else TEAMS_DIR


def tasks_dir(base_dir: Path | None = None) -> Path:
    """Return the tasks directory, optionally under a custom base for testing."""
    return (base_dir / "tasks") if base_dir else TASKS_DIR


def model_to_json(model: BaseModel, *, indent: int | None = None) -> str:
    """Serialize a Pydantic model to JSON (camelCase aliases, no None values)."""
    if indent is None:
        return model.model_dump_json(by_alias=True, exclude_none=True)
    return json.dumps(model.model_dump(by_alias=True, exclude_none=True), indent=indent)
