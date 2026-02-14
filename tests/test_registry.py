"""Tests for claude_side/registry.py — mock agent registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_teams.claude_side.registry import register_external_agent, unregister_external_agent
from claude_teams.common import messaging, teams
from claude_teams.common.models import TeammateMember

TEAM = "test-team"


@pytest.fixture
def team_dir(tmp_claude_dir: Path) -> Path:
    teams.create_team(TEAM, session_id="test-session-id", base_dir=tmp_claude_dir)
    return tmp_claude_dir


class TestRegisterExternalAgent:
    def test_registers_member_in_config(self, team_dir: Path) -> None:
        member = register_external_agent(TEAM, "reviewer", base_dir=team_dir)
        assert member.name == "reviewer"
        assert member.agent_id == "reviewer@test-team"
        assert member.backend_type == "external"

        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "reviewer" in names

    def test_creates_inbox_file(self, team_dir: Path) -> None:
        register_external_agent(TEAM, "worker", base_dir=team_dir)
        inbox = messaging.inbox_path(TEAM, "worker", base_dir=team_dir)
        assert inbox.exists()

    def test_sets_empty_tmux_pane_id(self, team_dir: Path) -> None:
        member = register_external_agent(TEAM, "agent1", base_dir=team_dir)
        assert member.tmux_pane_id == ""

    def test_rejects_duplicate_name(self, team_dir: Path) -> None:
        register_external_agent(TEAM, "dup", base_dir=team_dir)
        with pytest.raises(ValueError, match="already exists"):
            register_external_agent(TEAM, "dup", base_dir=team_dir)

    def test_assigns_colors_sequentially(self, team_dir: Path) -> None:
        m1 = register_external_agent(TEAM, "a1", base_dir=team_dir)
        m2 = register_external_agent(TEAM, "a2", base_dir=team_dir)
        assert m1.color != m2.color

    def test_custom_agent_type(self, team_dir: Path) -> None:
        member = register_external_agent(TEAM, "coder", agent_type="code-reviewer", base_dir=team_dir)
        assert member.agent_type == "code-reviewer"

    def test_custom_cwd(self, team_dir: Path) -> None:
        member = register_external_agent(TEAM, "w", cwd="/opt/project", base_dir=team_dir)
        assert member.cwd == "/opt/project"

    def test_nonexistent_team_raises(self, team_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            register_external_agent("no-such-team", "agent", base_dir=team_dir)

    def test_rejects_invalid_name(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            register_external_agent(TEAM, "bad/../name", base_dir=team_dir)

    def test_rejects_empty_name(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            register_external_agent(TEAM, "", base_dir=team_dir)

    def test_rejects_name_too_long(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="too long"):
            register_external_agent(TEAM, "a" * 65, base_dir=team_dir)

    def test_rejects_reserved_name(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="reserved"):
            register_external_agent(TEAM, "team-lead", base_dir=team_dir)


class TestUnregisterExternalAgent:
    def test_removes_member_from_config(self, team_dir: Path) -> None:
        register_external_agent(TEAM, "temp", base_dir=team_dir)
        unregister_external_agent(TEAM, "temp", base_dir=team_dir)

        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "temp" not in names

    def test_rejects_removing_team_lead(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="team-lead"):
            unregister_external_agent(TEAM, "team-lead", base_dir=team_dir)
