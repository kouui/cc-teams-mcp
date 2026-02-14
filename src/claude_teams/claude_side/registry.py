"""Mock agent registration for Claude Code's native team config.

Registers/unregisters external (non-Claude) agents in the team config
without spawning a process. This allows Claude Code's native SendMessage
to write to the agent's inbox, which is later bridged via tmux injection.
"""

from __future__ import annotations

from pathlib import Path
import time

from claude_teams.common import messaging, teams
from claude_teams.common.models import COLOR_PALETTE, TeammateMember


def _next_color(team_name: str, base_dir: Path | None = None) -> str:
    """Pick the next color from the palette based on current member count."""
    config = teams.read_config(team_name, base_dir)
    count = sum(1 for m in config.members if isinstance(m, TeammateMember))
    return COLOR_PALETTE[count % len(COLOR_PALETTE)]


def register_external_agent(
    team_name: str,
    name: str,
    *,
    agent_type: str = "general-purpose",
    cwd: str = "",
    prompt: str = "",
    base_dir: Path | None = None,
) -> TeammateMember:
    """Register a non-Claude agent in the team config and create its inbox.

    The agent is added to config.json with backendType="external" and
    tmuxPaneId="" (no running process yet). Its inbox file is created
    so Claude Code's SendMessage can write to it immediately.

    Raises ValueError if the name already exists in the team.
    """
    color = _next_color(team_name, base_dir)
    now_ms = int(time.time() * 1000)

    member = TeammateMember(
        agent_id=f"{name}@{team_name}",
        name=name,
        agent_type=agent_type,
        prompt=prompt,
        color=color,
        plan_mode_required=False,
        joined_at=now_ms,
        tmux_pane_id="",
        cwd=cwd or str(Path.cwd()),
        backend_type="external",
        is_active=False,
    )

    # add_member raises ValueError if name already exists
    teams.add_member(team_name, member, base_dir)
    messaging.ensure_inbox(team_name, name, base_dir)

    return member


def unregister_external_agent(
    team_name: str,
    name: str,
    base_dir: Path | None = None,
) -> None:
    """Remove an external agent from the team config.

    Does NOT delete the inbox file (messages may still be needed for audit).
    Raises ValueError if trying to remove team-lead.
    """
    teams.remove_member(team_name, name, base_dir)
