"""tmux message injector for external agents.

Formats inbox messages and sends them to a tmux pane via `send-keys`.
Messages are queued by tmux when the target pane is busy (mid-turn)
and processed when the pane becomes ready for input.
"""

from __future__ import annotations

import logging
import subprocess
import time

from claude_teams.common.models import InboxMessage

logger = logging.getLogger(__name__)

# tmux send-keys -l splits text into paste events at this boundary.
# Codex TUI treats paste events >1024 bytes as "[Pasted Content]" and
# refuses to submit them.  Keep chunks at or below this limit.
_TMUX_SEND_KEYS_MAX = 1024

# Short pause between chunks so the TUI can absorb each one.
_CHUNK_DELAY = 0.2


def format_message_for_injection(msg: InboxMessage) -> str:
    """Format an inbox message for tmux injection.

    Returns plain text in the format: [Message from <sender>]: <content>
    """
    return f"[Message from {msg.from_}]: {msg.text}"


def _send_text_chunked(pane_id: str, text: str) -> None:
    """Send text to a tmux pane in chunks to avoid the 1024-byte paste limit."""
    for offset in range(0, len(text), _TMUX_SEND_KEYS_MAX):
        chunk = text[offset : offset + _TMUX_SEND_KEYS_MAX]
        subprocess.run(
            ["tmux", "send-keys", "-t", pane_id, "-l", chunk],
            capture_output=True,
            text=True,
            check=True,
        )
        if offset + _TMUX_SEND_KEYS_MAX < len(text):
            time.sleep(_CHUNK_DELAY)


def inject_message(pane_id: str, msg: InboxMessage) -> bool:
    """Inject a single message into a tmux pane via send-keys.

    Returns True if the injection succeeded, False otherwise.
    """
    text = format_message_for_injection(msg)

    try:
        # Step 1: Send text literally in chunks (-l prevents key name interpretation)
        _send_text_chunked(pane_id, text)
        # Wait for TUI to render the input text before pressing Enter
        time.sleep(0.5)
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
