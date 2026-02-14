"""tmux message injector for external agents.

Formats inbox messages and sends them to a tmux pane via `send-keys`.
Messages are queued by tmux when the target pane is busy (mid-turn)
and processed when the pane becomes ready for input.
"""

from __future__ import annotations

import logging
import subprocess

from claude_teams.common.models import InboxMessage

logger = logging.getLogger(__name__)


def format_message_for_injection(msg: InboxMessage) -> str:
    """Format an inbox message for tmux injection.

    Returns plain text in the format: [Message from <sender>]: <content>
    """
    return f"[Message from {msg.from_}]: {msg.text}"


def inject_message(pane_id: str, msg: InboxMessage) -> bool:
    """Inject a single message into a tmux pane via send-keys.

    Returns True if the injection succeeded, False otherwise.
    """
    text = format_message_for_injection(msg)

    try:
        # Step 1: Send text literally (-l prevents key name interpretation)
        subprocess.run(
            ["tmux", "send-keys", "-t", pane_id, "-l", text],
            capture_output=True,
            text=True,
            check=True,
        )
        # Step 2: Send Enter key separately (without -l so "Enter" is a key name)
        subprocess.run(
            ["tmux", "send-keys", "-t", pane_id, "Enter"],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error("tmux send-keys failed for pane %s: %s", pane_id, e.stderr.strip())
        return False
    except FileNotFoundError:
        logger.error("tmux binary not found")
        return False


def inject_messages(pane_id: str, messages: list[InboxMessage]) -> int:
    """Inject multiple messages into a tmux pane.

    Returns the number of successfully injected messages.
    """
    count = 0
    for msg in messages:
        if inject_message(pane_id, msg):
            count += 1
        else:
            logger.warning("Stopping injection after failure at message %d/%d", count + 1, len(messages))
            break
    return count
