"""Shared path utilities for teams and tasks directories."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

CLAUDE_DIR = Path.home() / ".claude"
TEAMS_DIR = CLAUDE_DIR / "teams"
TASKS_DIR = CLAUDE_DIR / "tasks"


def teams_dir(base_dir: Path | None = None) -> Path:
    """Return the teams directory path.
    
    Args:
        base_dir: Optional base directory for testing. If None, uses ~/.claude
        
    Returns:
        Path to the teams directory
    """
    return (base_dir / "teams") if base_dir else TEAMS_DIR


def tasks_dir(base_dir: Path | None = None) -> Path:
    """Return the tasks directory path.
    
    Args:
        base_dir: Optional base directory for testing. If None, uses ~/.claude
        
    Returns:
        Path to the tasks directory
    """
    return (base_dir / "tasks") if base_dir else TASKS_DIR


def model_to_json(model: BaseModel) -> str:
    """Convert a Pydantic model to JSON string with standard formatting.
    
    Args:
        model: Pydantic model to serialize
        
    Returns:
        JSON string with camelCase aliases and None values excluded
    """
    return json.dumps(model.model_dump(by_alias=True, exclude_none=True))

