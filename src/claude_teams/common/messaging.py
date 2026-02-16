from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from claude_teams.common._filelock import file_lock
from claude_teams.common._paths import teams_dir
from claude_teams.common.models import InboxMessage


def now_iso() -> str:
    dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def inbox_path(team_name: str, agent_name: str, base_dir: Path | None = None) -> Path:
    return teams_dir(base_dir) / team_name / "inboxes" / f"{agent_name}.json"


def ensure_inbox(team_name: str, agent_name: str, base_dir: Path | None = None) -> Path:
    path = inbox_path(team_name, agent_name, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]")
    return path


def read_inbox(
    team_name: str,
    agent_name: str,
    unread_only: bool = False,
    mark_as_read: bool = True,
    base_dir: Path | None = None,
) -> list[InboxMessage]:
    path = inbox_path(team_name, agent_name, base_dir)
    if not path.exists():
        return []

    # Always need lock if marking as read
    if mark_as_read:
        lock_path = path.parent / ".lock"
        with file_lock(lock_path):
            raw_list = json.loads(path.read_text())
            all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]

            result = [m for m in all_msgs if not m.read] if unread_only else list(all_msgs)

            # Mark returned messages as read in the full list, then persist.
            # result elements are references into all_msgs, so mutating them
            # updates all_msgs for serialization.
            result_set = set(id(m) for m in result)
            if result:
                for m in all_msgs:
                    if id(m) in result_set:
                        m.read = True
                serialized = [m.model_dump(by_alias=True, exclude_none=True) for m in all_msgs]
                path.write_text(json.dumps(serialized))

            return result
    else:
        # Read-only path doesn't need lock
        raw_list = json.loads(path.read_text())
        all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]
        return [m for m in all_msgs if not m.read] if unread_only else list(all_msgs)


def mark_messages_as_read(
    team_name: str,
    agent_name: str,
    count: int,
    base_dir: Path | None = None,
) -> None:
    """Mark the first `count` unread messages as read.

    Used after successful injection to avoid marking messages as read
    before they are actually delivered.
    """
    path = inbox_path(team_name, agent_name, base_dir)
    if not path.exists():
        return
    lock_path = path.parent / ".lock"
    with file_lock(lock_path):
        raw_list = json.loads(path.read_text())
        all_msgs = [InboxMessage.model_validate(entry) for entry in raw_list]
        marked = 0
        for m in all_msgs:
            if marked >= count:
                break
            if not m.read:
                m.read = True
                marked += 1
        if marked:
            serialized = [m.model_dump(by_alias=True, exclude_none=True) for m in all_msgs]
            path.write_text(json.dumps(serialized))


def append_message(
    team_name: str,
    agent_name: str,
    message: InboxMessage,
    base_dir: Path | None = None,
) -> None:
    path = ensure_inbox(team_name, agent_name, base_dir)
    lock_path = path.parent / ".lock"

    with file_lock(lock_path):
        raw_list = json.loads(path.read_text())
        raw_list.append(message.model_dump(by_alias=True, exclude_none=True))
        path.write_text(json.dumps(raw_list))


def send_plain_message(
    team_name: str,
    from_name: str,
    to_name: str,
    text: str,
    summary: str,
    color: str | None = None,
    base_dir: Path | None = None,
) -> None:
    msg = InboxMessage(
        from_=from_name,
        text=text,
        timestamp=now_iso(),
        read=False,
        summary=summary,
        color=color,
    )
    append_message(team_name, to_name, msg, base_dir)
