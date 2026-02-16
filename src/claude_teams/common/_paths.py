"""Shared path utilities for teams and tasks directories."""

from __future__ import annotations

from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
TEAMS_DIR = CLAUDE_DIR / "teams"
TASKS_DIR = CLAUDE_DIR / "tasks"


def teams_dir(base_dir: Path | None = None) -> Path:
    """Return the teams directory, optionally under a custom base for testing."""
    return (base_dir / "teams") if base_dir else TEAMS_DIR


def tasks_dir(base_dir: Path | None = None) -> Path:
    """Return the tasks directory, optionally under a custom base for testing."""
    return (base_dir / "tasks") if base_dir else TASKS_DIR
