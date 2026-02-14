"""Tests for claude_side/watcher.py — inbox file monitoring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_teams.claude_side import watcher
from claude_teams.common import messaging, teams

TEAM = "test-team"


@pytest.fixture
def team_dir(tmp_claude_dir: Path) -> Path:
    teams.create_team(TEAM, session_id="test-session-id", base_dir=tmp_claude_dir)
    return tmp_claude_dir


@pytest.fixture(autouse=True)
def clean_watchers():
    """Ensure no leftover watchers between tests."""
    yield
    watcher.stop_all_watchers()


class TestWatcherLifecycle:
    def test_is_watching_false_by_default(self) -> None:
        assert watcher.is_watching(TEAM, "nobody") is False

    async def test_start_and_stop(self, team_dir: Path) -> None:
        messaging.ensure_inbox(TEAM, "agent1", base_dir=team_dir)

        with patch("claude_teams.claude_side.watcher.inject_messages"):
            task = watcher.start_watcher(TEAM, "agent1", "%42", base_dir=team_dir)
            assert watcher.is_watching(TEAM, "agent1") is True

            stopped = watcher.stop_watcher(TEAM, "agent1")
            assert stopped is True
            # Give the task a moment to cancel
            await asyncio.sleep(0.1)
            assert task.done()

    async def test_stop_nonexistent_returns_false(self) -> None:
        assert watcher.stop_watcher(TEAM, "ghost") is False

    async def test_start_replaces_existing(self, team_dir: Path) -> None:
        messaging.ensure_inbox(TEAM, "agent1", base_dir=team_dir)

        with patch("claude_teams.claude_side.watcher.inject_messages"):
            task1 = watcher.start_watcher(TEAM, "agent1", "%42", base_dir=team_dir)
            task2 = watcher.start_watcher(TEAM, "agent1", "%43", base_dir=team_dir)

            # task1 should be cancelled
            await asyncio.sleep(0.1)
            assert task1.done()
            assert not task2.done()
            assert watcher.is_watching(TEAM, "agent1") is True

    async def test_stop_all(self, team_dir: Path) -> None:
        messaging.ensure_inbox(TEAM, "a1", base_dir=team_dir)
        messaging.ensure_inbox(TEAM, "a2", base_dir=team_dir)

        with patch("claude_teams.claude_side.watcher.inject_messages"):
            watcher.start_watcher(TEAM, "a1", "%1", base_dir=team_dir)
            watcher.start_watcher(TEAM, "a2", "%2", base_dir=team_dir)

            stopped = watcher.stop_all_watchers()
            assert stopped == 2
            assert watcher.is_watching(TEAM, "a1") is False
            assert watcher.is_watching(TEAM, "a2") is False


class TestWatcherMessageDelivery:
    async def test_delivers_new_messages(self, team_dir: Path) -> None:
        """Watcher should detect new messages and call inject_messages."""
        messaging.ensure_inbox(TEAM, "codex1", base_dir=team_dir)
        injected: list = []

        def fake_inject(pane_id, msgs):
            injected.extend(msgs)
            return len(msgs)

        with patch("claude_teams.claude_side.watcher.inject_messages", side_effect=fake_inject):
            watcher.start_watcher(TEAM, "codex1", "%50", base_dir=team_dir)

            # Wait for watcher to start
            await asyncio.sleep(0.2)

            # Write a message to the inbox
            messaging.send_plain_message(
                TEAM,
                "team-lead",
                "codex1",
                "Hello codex!",
                summary="greeting",
                base_dir=team_dir,
            )

            # Wait for watcher to pick it up (poll interval is 0.5s)
            await asyncio.sleep(1.5)

            assert len(injected) >= 1
            assert injected[0].text == "Hello codex!"
            assert injected[0].from_ == "team-lead"

    async def test_marks_messages_as_read_after_injection(self, team_dir: Path) -> None:
        """After successful injection, messages should be marked as read."""
        messaging.ensure_inbox(TEAM, "codex2", base_dir=team_dir)

        with patch("claude_teams.claude_side.watcher.inject_messages", return_value=1):
            watcher.start_watcher(TEAM, "codex2", "%51", base_dir=team_dir)
            await asyncio.sleep(0.2)

            messaging.send_plain_message(
                TEAM,
                "team-lead",
                "codex2",
                "task update",
                summary="update",
                base_dir=team_dir,
            )
            await asyncio.sleep(1.5)

        # Check that message is now read
        msgs = messaging.read_inbox(TEAM, "codex2", unread_only=True, mark_as_read=False, base_dir=team_dir)
        assert len(msgs) == 0

    async def test_does_not_mark_as_read_on_injection_failure(self, team_dir: Path) -> None:
        """If injection fails (returns 0), messages should stay unread."""
        messaging.ensure_inbox(TEAM, "codex-fail", base_dir=team_dir)

        with patch("claude_teams.claude_side.watcher.inject_messages", return_value=0):
            watcher.start_watcher(TEAM, "codex-fail", "%53", base_dir=team_dir)
            await asyncio.sleep(0.2)

            messaging.send_plain_message(
                TEAM,
                "team-lead",
                "codex-fail",
                "this should stay unread",
                summary="test",
                base_dir=team_dir,
            )
            await asyncio.sleep(1.5)

        # Messages should still be unread since injection returned 0
        msgs = messaging.read_inbox(TEAM, "codex-fail", unread_only=True, mark_as_read=False, base_dir=team_dir)
        assert len(msgs) == 1

    async def test_retries_after_injection_failure(self, team_dir: Path) -> None:
        """Watcher should retry injection on next poll when inject fails then succeeds."""
        messaging.ensure_inbox(TEAM, "codex-retry", base_dir=team_dir)
        call_count = 0

        def fail_then_succeed(pane_id, msgs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 0  # fail first attempt
            return len(msgs)  # succeed on retry

        with patch("claude_teams.claude_side.watcher.inject_messages", side_effect=fail_then_succeed):
            watcher.start_watcher(TEAM, "codex-retry", "%60", base_dir=team_dir)
            await asyncio.sleep(0.2)

            messaging.send_plain_message(
                TEAM,
                "team-lead",
                "codex-retry",
                "retry me",
                summary="test",
                base_dir=team_dir,
            )
            # Wait long enough for at least 2 poll cycles
            await asyncio.sleep(3.0)

        assert call_count >= 2, f"Expected at least 2 inject attempts, got {call_count}"
        # Message should be marked as read after successful retry
        msgs = messaging.read_inbox(TEAM, "codex-retry", unread_only=True, mark_as_read=False, base_dir=team_dir)
        assert len(msgs) == 0

    async def test_ignores_already_read_messages(self, team_dir: Path) -> None:
        """Watcher should not re-inject already read messages."""
        messaging.ensure_inbox(TEAM, "codex3", base_dir=team_dir)
        # Pre-write a read message
        messaging.send_plain_message(TEAM, "team-lead", "codex3", "old msg", summary="old", base_dir=team_dir)
        # Mark it as read
        messaging.read_inbox(TEAM, "codex3", unread_only=True, mark_as_read=True, base_dir=team_dir)

        injected: list = []

        def fake_inject(pane_id, msgs):
            injected.extend(msgs)
            return len(msgs)

        with patch("claude_teams.claude_side.watcher.inject_messages", side_effect=fake_inject):
            watcher.start_watcher(TEAM, "codex3", "%52", base_dir=team_dir)
            await asyncio.sleep(1.5)

        assert len(injected) == 0
