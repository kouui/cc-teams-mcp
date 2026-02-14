"""Tests for MCP-A: Claude-side bridge server."""

from __future__ import annotations

import json
from pathlib import Path
import time

from fastmcp import Client
import pytest

from claude_teams.claude_side.server import mcp
from claude_teams.common import messaging, tasks, teams
from claude_teams.common.models import TeammateMember


def _make_teammate(
    name: str,
    team_name: str,
    pane_id: str = "%1",
) -> TeammateMember:
    return TeammateMember(
        agent_id=f"{name}@{team_name}",
        name=name,
        agent_type="teammate",
        prompt="Do stuff",
        color="blue",
        plan_mode_required=False,
        joined_at=int(time.time() * 1000),
        tmux_pane_id=pane_id,
        cwd="/tmp",
        backend_type="external",
    )


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(teams, "TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr(teams, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(messaging, "TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr(
        "claude_teams.claude_side.server.discover_backend_binaries",
        lambda: {"codex": "/usr/bin/echo"},
    )
    monkeypatch.setattr(
        "claude_teams.claude_side.spawner.subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": "%99\n"})(),
    )
    (tmp_path / "teams").mkdir()
    (tmp_path / "tasks").mkdir()
    async with Client(mcp) as c:
        yield c


def _data(result):
    if result.content:
        return json.loads(result.content[0].text)
    return result.data


def _setup_team(team_name: str, session_id: str = "sess-1"):
    """Create a team using the common module directly (Claude Code does this natively)."""
    teams.create_team(team_name, session_id=session_id)


class TestSpawnExternalAgent:
    async def test_should_spawn_external_agent(self, client: Client):
        _setup_team("t1")
        result = await client.call_tool(
            "spawn_external_agent",
            {
                "team_name": "t1",
                "name": "codex-worker",
                "prompt": "do codex stuff",
                "cwd": "/tmp",
            },
        )
        data = _data(result)
        assert data["name"] == "codex-worker"
        assert data["agent_id"] == "codex-worker@t1"

    async def test_should_reject_missing_cwd(self, client: Client):
        _setup_team("t2")
        result = await client.call_tool(
            "spawn_external_agent",
            {
                "team_name": "t2",
                "name": "worker",
                "prompt": "do stuff",
                "cwd": "",
            },
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "cwd" in result.content[0].text.lower()

    async def test_should_reject_relative_cwd(self, client: Client):
        _setup_team("t3")
        result = await client.call_tool(
            "spawn_external_agent",
            {
                "team_name": "t3",
                "name": "worker",
                "prompt": "do stuff",
                "cwd": "relative/path",
            },
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "cwd" in result.content[0].text.lower()


class TestCheckExternalAgent:
    async def test_should_return_error_for_unknown_agent(self, client: Client):
        _setup_team("tca1")
        result = await client.call_tool(
            "check_external_agent",
            {"team_name": "tca1", "agent_name": "ghost"},
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "ghost" in result.content[0].text

    async def test_should_return_not_alive_for_empty_pane_id(self, client: Client):
        _setup_team("tca2")
        teams.add_member("tca2", _make_teammate("worker", "tca2", pane_id=""))
        result = _data(
            await client.call_tool(
                "check_external_agent",
                {"team_name": "tca2", "agent_name": "worker"},
            )
        )
        assert result["alive"] is False
        assert result["error"] == "no tmux target recorded"


class TestShutdownExternalAgent:
    async def test_should_reject_shutdown_of_team_lead(self, client: Client):
        _setup_team("tsa1")
        result = await client.call_tool(
            "shutdown_external_agent",
            {"team_name": "tsa1", "agent_name": "team-lead"},
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "team-lead" in result.content[0].text

    async def test_should_reject_shutdown_of_nonexistent_agent(self, client: Client):
        _setup_team("tsa2")
        result = await client.call_tool(
            "shutdown_external_agent",
            {"team_name": "tsa2", "agent_name": "ghost"},
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "ghost" in result.content[0].text

    async def test_should_kill_tmux_pane_on_shutdown(self, client: Client, monkeypatch):
        killed = []
        monkeypatch.setattr(
            "claude_teams.claude_side.server.kill_tmux_pane",
            lambda pane_id: killed.append(pane_id),
        )
        _setup_team("tsa3")
        teams.add_member("tsa3", _make_teammate("worker", "tsa3", pane_id="%77"))
        result = await client.call_tool(
            "shutdown_external_agent",
            {"team_name": "tsa3", "agent_name": "worker"},
        )
        assert result.is_error is False
        assert killed == ["%77"]
