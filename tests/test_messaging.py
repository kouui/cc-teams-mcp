from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from claude_teams.common.messaging import (
    append_message,
    ensure_inbox,
    inbox_path,
    mark_messages_as_read,
    now_iso,
    read_inbox,
    send_plain_message,
)
from claude_teams.common.models import InboxMessage


@pytest.fixture
def team_dir(tmp_claude_dir):
    d = tmp_claude_dir / "teams" / "test-team"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_ensure_inbox_creates_directory_and_file(tmp_claude_dir):
    path = ensure_inbox("test-team", "alice", base_dir=tmp_claude_dir)
    assert path.exists()
    assert path.parent.name == "inboxes"
    assert path.name == "alice.json"
    assert json.loads(path.read_text()) == []


def test_ensure_inbox_idempotent(tmp_claude_dir):
    ensure_inbox("test-team", "alice", base_dir=tmp_claude_dir)
    path = ensure_inbox("test-team", "alice", base_dir=tmp_claude_dir)
    assert path.exists()
    assert json.loads(path.read_text()) == []


def test_append_message_accumulates(tmp_claude_dir):
    msg1 = InboxMessage(from_="lead", text="hello", timestamp=now_iso(), read=False, summary="hi")
    msg2 = InboxMessage(from_="lead", text="world", timestamp=now_iso(), read=False, summary="yo")
    append_message("test-team", "bob", msg1, base_dir=tmp_claude_dir)
    append_message("test-team", "bob", msg2, base_dir=tmp_claude_dir)
    raw = json.loads(inbox_path("test-team", "bob", base_dir=tmp_claude_dir).read_text())
    assert len(raw) == 2


def test_append_message_does_not_overwrite(tmp_claude_dir):
    msg1 = InboxMessage(from_="lead", text="first", timestamp=now_iso(), read=False, summary="1")
    msg2 = InboxMessage(from_="lead", text="second", timestamp=now_iso(), read=False, summary="2")
    append_message("test-team", "bob", msg1, base_dir=tmp_claude_dir)
    append_message("test-team", "bob", msg2, base_dir=tmp_claude_dir)
    raw = json.loads(inbox_path("test-team", "bob", base_dir=tmp_claude_dir).read_text())
    texts = [m["text"] for m in raw]
    assert "first" in texts
    assert "second" in texts


def test_read_inbox_returns_all_by_default(tmp_claude_dir):
    msg1 = InboxMessage(from_="lead", text="a", timestamp=now_iso(), read=False, summary="s1")
    msg2 = InboxMessage(from_="lead", text="b", timestamp=now_iso(), read=True, summary="s2")
    append_message("test-team", "carol", msg1, base_dir=tmp_claude_dir)
    append_message("test-team", "carol", msg2, base_dir=tmp_claude_dir)
    msgs = read_inbox("test-team", "carol", mark_as_read=False, base_dir=tmp_claude_dir)
    assert len(msgs) == 2


def test_read_inbox_unread_only(tmp_claude_dir):
    msg1 = InboxMessage(from_="lead", text="a", timestamp=now_iso(), read=True, summary="s1")
    msg2 = InboxMessage(from_="lead", text="b", timestamp=now_iso(), read=False, summary="s2")
    append_message("test-team", "dave", msg1, base_dir=tmp_claude_dir)
    append_message("test-team", "dave", msg2, base_dir=tmp_claude_dir)
    msgs = read_inbox("test-team", "dave", unread_only=True, mark_as_read=False, base_dir=tmp_claude_dir)
    assert len(msgs) == 1
    assert msgs[0].text == "b"


def test_read_inbox_marks_as_read(tmp_claude_dir):
    msg = InboxMessage(from_="lead", text="unread", timestamp=now_iso(), read=False, summary="s")
    append_message("test-team", "eve", msg, base_dir=tmp_claude_dir)
    read_inbox("test-team", "eve", mark_as_read=True, base_dir=tmp_claude_dir)
    remaining = read_inbox("test-team", "eve", unread_only=True, mark_as_read=False, base_dir=tmp_claude_dir)
    assert len(remaining) == 0


def test_read_inbox_nonexistent_returns_empty(tmp_claude_dir):
    msgs = read_inbox("test-team", "ghost", base_dir=tmp_claude_dir)
    assert msgs == []


def test_send_plain_message_appears_in_inbox(tmp_claude_dir):
    send_plain_message("test-team", "lead", "frank", "hey there", summary="greeting", base_dir=tmp_claude_dir)
    msgs = read_inbox("test-team", "frank", mark_as_read=False, base_dir=tmp_claude_dir)
    assert len(msgs) == 1
    assert msgs[0].from_ == "lead"
    assert msgs[0].text == "hey there"
    assert msgs[0].summary == "greeting"
    assert msgs[0].read is False


def test_send_plain_message_with_color(tmp_claude_dir):
    send_plain_message("test-team", "lead", "gina", "colorful", summary="c", color="blue", base_dir=tmp_claude_dir)
    msgs = read_inbox("test-team", "gina", mark_as_read=False, base_dir=tmp_claude_dir)
    assert msgs[0].color == "blue"


def test_should_not_lose_message_appended_during_mark_as_read(tmp_claude_dir):
    from filelock import FileLock

    msg_a = InboxMessage(from_="lead", text="A", timestamp=now_iso(), read=False, summary="a")
    append_message("test-team", "race", msg_a, base_dir=tmp_claude_dir)

    path = inbox_path("test-team", "race", base_dir=tmp_claude_dir)
    lock_path = path.parent / ".lock"

    completed = threading.Event()

    def do_read():
        read_inbox("test-team", "race", mark_as_read=True, base_dir=tmp_claude_dir)
        completed.set()

    lock = FileLock(str(lock_path))
    lock.acquire()
    try:
        reader = threading.Thread(target=do_read)
        reader.start()
        completed_without_lock = completed.wait(timeout=1.0)
    finally:
        lock.release()

    reader.join(timeout=5)

    assert not completed_without_lock, "read_inbox(mark_as_read=True) completed without acquiring the inbox lock"


def test_now_iso_format():
    import re

    ts = now_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts)


class TestMarkMessagesAsRead:
    def test_marks_first_n_unread(self, tmp_claude_dir) -> None:
        for i in range(3):
            msg = InboxMessage(from_="lead", text=f"msg-{i}", timestamp=now_iso(), read=False, summary=f"s{i}")
            append_message("test-team", "agent", msg, base_dir=tmp_claude_dir)

        mark_messages_as_read("test-team", "agent", 2, base_dir=tmp_claude_dir)
        msgs = read_inbox("test-team", "agent", mark_as_read=False, base_dir=tmp_claude_dir)
        read_count = sum(1 for m in msgs if m.read)
        unread_count = sum(1 for m in msgs if not m.read)
        assert read_count == 2
        assert unread_count == 1

    def test_marks_zero_is_noop(self, tmp_claude_dir) -> None:
        msg = InboxMessage(from_="lead", text="x", timestamp=now_iso(), read=False, summary="s")
        append_message("test-team", "agent2", msg, base_dir=tmp_claude_dir)

        mark_messages_as_read("test-team", "agent2", 0, base_dir=tmp_claude_dir)
        msgs = read_inbox("test-team", "agent2", unread_only=True, mark_as_read=False, base_dir=tmp_claude_dir)
        assert len(msgs) == 1

    def test_nonexistent_inbox_is_noop(self, tmp_claude_dir) -> None:
        mark_messages_as_read("test-team", "ghost", 5, base_dir=tmp_claude_dir)
