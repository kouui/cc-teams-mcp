"""Tests for claude_side/injector.py — tmux message injection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from claude_teams.claude_side.injector import format_message_for_injection, inject_message, inject_messages
from claude_teams.common.models import InboxMessage


def _msg(from_: str = "team-lead", text: str = "hello") -> InboxMessage:
    return InboxMessage(from_=from_, text=text, timestamp="2026-01-01T00:00:00.000Z")


class TestFormatMessage:
    def test_basic_format(self) -> None:
        result = format_message_for_injection(_msg(from_="alice", text="hi bob"))
        assert result == "[Message from alice]: hi bob"

    def test_preserves_newlines(self) -> None:
        result = format_message_for_injection(_msg(text="line1\nline2"))
        assert "line1\nline2" in result


class TestInjectMessage:
    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_calls_tmux_send_keys(self, mock_run: MagicMock) -> None:
        result = inject_message("%42", _msg(text="hello"))
        assert result is True
        assert mock_run.call_count == 2
        # First call: send text literally
        text_args = mock_run.call_args_list[0][0][0]
        assert text_args[:4] == ["tmux", "send-keys", "-t", "%42"]
        assert "-l" in text_args
        assert "hello" in text_args[-1]
        # Second call: send Enter key
        enter_args = mock_run.call_args_list[1][0][0]
        assert enter_args == ["tmux", "send-keys", "-t", "%42", "Enter"]

    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_returns_false_on_failure(self, mock_run: MagicMock) -> None:
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, ["tmux"], stderr="error")
        result = inject_message("%42", _msg())
        assert result is False

    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_returns_false_when_tmux_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("tmux")
        result = inject_message("%42", _msg())
        assert result is False


class TestInjectMessages:
    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_injects_all_messages(self, mock_run: MagicMock) -> None:
        msgs = [_msg(text=f"msg-{i}") for i in range(3)]
        count = inject_messages("%42", msgs)
        assert count == 3
        assert mock_run.call_count == 6  # 2 calls per message (text + Enter)

    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_stops_on_failure(self, mock_run: MagicMock) -> None:
        import subprocess

        # msg1: text ok, enter ok; msg2: text fails; msg3: skipped
        mock_run.side_effect = [
            None,
            None,  # msg1 text + enter
            subprocess.CalledProcessError(1, ["tmux"], stderr="err"),  # msg2 text fails
        ]
        msgs = [_msg(text=f"msg-{i}") for i in range(3)]
        count = inject_messages("%42", msgs)
        assert count == 1  # first succeeded, second failed, third skipped

    @patch("claude_teams.claude_side.injector.subprocess.run")
    def test_empty_list(self, mock_run: MagicMock) -> None:
        count = inject_messages("%42", [])
        assert count == 0
        mock_run.assert_not_called()
