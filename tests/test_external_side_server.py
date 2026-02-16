"""Tests for MCP-B: External-side server for non-Claude agents."""

from __future__ import annotations

import json
from pathlib import Path
import time

from fastmcp import Client
import pytest

from claude_teams.common import _paths, messaging, tasks, teams
from claude_teams.common.models import TeammateMember
from claude_teams.external_side.server import mcp


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
        backend_type="in-process",
    )


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(_paths, "TEAMS_DIR", tmp_path / "teams")
    monkeypatch.setattr(_paths, "TASKS_DIR", tmp_path / "tasks")
    (tmp_path / "teams").mkdir()
    (tmp_path / "tasks").mkdir()
    async with Client(mcp) as c:
        yield c


def _data(result):
    if result.content:
        return json.loads(result.content[0].text)
    return result.data


def _setup_team(team_name: str, session_id: str = "sess-1"):
    teams.create_team(team_name, session_id=session_id)


class TestSendMessage:
    async def test_should_send_message_to_team_lead(self, client: Client):
        _setup_team("t1")
        teams.add_member("t1", _make_teammate("worker", "t1"))
        result = await client.call_tool(
            "send_message",
            {
                "team_name": "t1",
                "sender": "worker",
                "recipient": "team-lead",
                "content": "done with task",
                "summary": "status update",
            },
        )
        data = _data(result)
        assert data["success"] is True
        inbox = messaging.read_inbox("t1", "team-lead", mark_as_read=False)
        assert len(inbox) == 1
        assert inbox[0].from_ == "worker"
        assert "done with task" in inbox[0].text

    async def test_should_cc_team_lead_on_peer_messages(self, client: Client):
        _setup_team("t2")
        teams.add_member("t2", _make_teammate("alice", "t2"))
        teams.add_member("t2", _make_teammate("bob", "t2"))
        await client.call_tool(
            "send_message",
            {
                "team_name": "t2",
                "sender": "alice",
                "recipient": "bob",
                "content": "hi bob",
                "summary": "greeting",
            },
        )
        # bob should have the message
        bob_msgs = messaging.read_inbox("t2", "bob", mark_as_read=False)
        assert any(m.from_ == "alice" for m in bob_msgs)
        # team-lead should have a CC copy
        lead_msgs = messaging.read_inbox("t2", "team-lead", mark_as_read=False)
        cc_msgs = [m for m in lead_msgs if m.from_ == "alice" and "[CC" in (m.summary or "")]
        assert len(cc_msgs) == 1

    async def test_should_not_cc_team_lead_when_disabled(self, client: Client):
        _setup_team("t2b")
        teams.add_member("t2b", _make_teammate("alice", "t2b"))
        teams.add_member("t2b", _make_teammate("bob", "t2b"))
        await client.call_tool(
            "send_message",
            {
                "team_name": "t2b",
                "sender": "alice",
                "recipient": "bob",
                "content": "private msg",
                "summary": "private",
                "cc_team_lead": False,
            },
        )
        bob_msgs = messaging.read_inbox("t2b", "bob", mark_as_read=False)
        assert len(bob_msgs) == 1
        lead_msgs = messaging.read_inbox("t2b", "team-lead", mark_as_read=False)
        assert len(lead_msgs) == 0

    async def test_should_reject_empty_content(self, client: Client):
        _setup_team("t3")
        teams.add_member("t3", _make_teammate("worker", "t3"))
        result = await client.call_tool(
            "send_message",
            {
                "team_name": "t3",
                "sender": "worker",
                "recipient": "team-lead",
                "content": "",
                "summary": "hi",
            },
            raise_on_error=False,
        )
        assert result.is_error is True

    async def test_should_reject_empty_sender(self, client: Client):
        _setup_team("t4")
        result = await client.call_tool(
            "send_message",
            {
                "team_name": "t4",
                "sender": "",
                "recipient": "team-lead",
                "content": "hi",
                "summary": "test",
            },
            raise_on_error=False,
        )
        assert result.is_error is True

    async def test_should_reject_self_message(self, client: Client):
        _setup_team("t5")
        teams.add_member("t5", _make_teammate("worker", "t5"))
        result = await client.call_tool(
            "send_message",
            {
                "team_name": "t5",
                "sender": "worker",
                "recipient": "worker",
                "content": "self talk",
                "summary": "self",
            },
            raise_on_error=False,
        )
        assert result.is_error is True

    async def test_should_reject_nonexistent_team(self, client: Client):
        result = await client.call_tool(
            "send_message",
            {
                "team_name": "ghost-team",
                "sender": "worker",
                "recipient": "team-lead",
                "content": "hi",
                "summary": "test",
            },
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "not found" in result.content[0].text.lower()


class TestTaskTools:
    async def test_should_create_and_list_tasks(self, client: Client):
        _setup_team("tt1")
        await client.call_tool(
            "task_create",
            {"team_name": "tt1", "subject": "first", "description": "d1"},
        )
        await client.call_tool(
            "task_create",
            {"team_name": "tt1", "subject": "second", "description": "d2"},
        )
        result = _data(await client.call_tool("task_list", {"team_name": "tt1"}))
        assert len(result) == 2
        assert result[0]["subject"] == "first"

    async def test_should_get_task_details(self, client: Client):
        _setup_team("tt2")
        created = _data(
            await client.call_tool(
                "task_create",
                {"team_name": "tt2", "subject": "mytask", "description": "desc"},
            )
        )
        result = _data(
            await client.call_tool(
                "task_get",
                {"team_name": "tt2", "task_id": created["id"]},
            )
        )
        assert result["subject"] == "mytask"

    async def test_should_update_task_status(self, client: Client):
        _setup_team("tt3")
        created = _data(
            await client.call_tool(
                "task_create",
                {"team_name": "tt3", "subject": "S", "description": "d"},
            )
        )
        result = _data(
            await client.call_tool(
                "task_update",
                {"team_name": "tt3", "task_id": created["id"], "status": "in_progress"},
            )
        )
        assert result["status"] == "in_progress"

    async def test_should_reject_nonexistent_task(self, client: Client):
        _setup_team("tt4")
        result = await client.call_tool(
            "task_get",
            {"team_name": "tt4", "task_id": "999"},
            raise_on_error=False,
        )
        assert result.is_error is True
        assert "not found" in result.content[0].text.lower()

    async def test_should_reject_nonexistent_team_on_create(self, client: Client):
        result = await client.call_tool(
            "task_create",
            {"team_name": "ghost", "subject": "x", "description": "y"},
            raise_on_error=False,
        )
        assert result.is_error is True

    async def test_should_reject_nonexistent_team_on_list(self, client: Client):
        result = await client.call_tool(
            "task_list",
            {"team_name": "ghost"},
            raise_on_error=False,
        )
        assert result.is_error is True
