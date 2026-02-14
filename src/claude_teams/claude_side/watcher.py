"""Inbox file watcher for external agents.

Monitors inbox files of external (non-Claude) agents for new unread messages.
When new messages are detected, they are read (marked as read) and injected
into the agent's tmux pane via the injector module.

Each external agent gets its own watcher task managed via asyncio.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from claude_teams.claude_side.injector import inject_messages
from claude_teams.common import messaging

logger = logging.getLogger(__name__)

# Active watcher tasks keyed by (team_name, agent_name)
_watchers: dict[tuple[str, str], asyncio.Task] = {}

# Poll interval in seconds
_POLL_INTERVAL = 1.0


async def _watch_loop(
    team_name: str,
    agent_name: str,
    pane_id: str,
    base_dir: Path | None = None,
) -> None:
    """Poll an agent's inbox file for new unread messages and inject them."""
    inbox = messaging.inbox_path(team_name, agent_name, base_dir)
    last_mtime: float = 0

    logger.info("Watcher started for %s@%s (pane=%s)", agent_name, team_name, pane_id)

    try:
        while True:
            try:
                if inbox.exists():
                    current_mtime = inbox.stat().st_mtime
                    if current_mtime > last_mtime:
                        last_mtime = current_mtime
                        new_msgs = messaging.read_inbox(
                            team_name,
                            agent_name,
                            unread_only=True,
                            mark_as_read=False,
                            base_dir=base_dir,
                        )
                        if new_msgs:
                            logger.info(
                                "Injecting %d message(s) to %s@%s",
                                len(new_msgs),
                                agent_name,
                                team_name,
                            )
                            injected = inject_messages(pane_id, new_msgs)
                            if injected > 0:
                                messaging.mark_messages_as_read(team_name, agent_name, injected, base_dir)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in watcher for %s@%s", agent_name, team_name)

            await asyncio.sleep(_POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("Watcher stopped for %s@%s", agent_name, team_name)


def start_watcher(
    team_name: str,
    agent_name: str,
    pane_id: str,
    base_dir: Path | None = None,
) -> asyncio.Task:
    """Start watching an external agent's inbox for new messages.

    Returns the asyncio Task. If a watcher is already running for this
    agent, it is stopped first.
    """
    key = (team_name, agent_name)

    # Stop existing watcher if any
    existing = _watchers.get(key)
    if existing is not None and not existing.done():
        existing.cancel()

    task = asyncio.create_task(
        _watch_loop(team_name, agent_name, pane_id, base_dir),
        name=f"watcher-{agent_name}@{team_name}",
    )
    _watchers[key] = task
    return task


def stop_watcher(team_name: str, agent_name: str) -> bool:
    """Stop the inbox watcher for an external agent.

    Returns True if a watcher was found and cancelled, False if none was running.
    """
    key = (team_name, agent_name)
    task = _watchers.pop(key, None)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def stop_all_watchers() -> int:
    """Stop all active watchers. Returns the number of watchers stopped."""
    count = 0
    for key in list(_watchers):
        task = _watchers.pop(key)
        if not task.done():
            task.cancel()
            count += 1
    return count


def is_watching(team_name: str, agent_name: str) -> bool:
    """Check if a watcher is currently active for an agent."""
    key = (team_name, agent_name)
    task = _watchers.get(key)
    return task is not None and not task.done()
