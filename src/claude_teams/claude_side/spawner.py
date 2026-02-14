"""Spawner for external (non-Claude) agent instances in tmux."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from claude_teams.common import messaging, teams
from claude_teams.common.models import COLOR_PALETTE, InboxMessage, TeammateMember
from claude_teams.common.teams import _VALID_NAME_RE

_CODEX_PROMPT_WRAPPER = """\
You are team member '{name}' on team '{team_name}'.

You have MCP tools from the claude-teams-external server for team coordination:
- send_message(team_name="{team_name}", sender="{name}", recipient="<name>", content="...", summary="...") - Send message to any teammate
- task_list(team_name="{team_name}") - View team tasks
- task_update(team_name="{team_name}", task_id="...", status="...") - Update task status
- task_get(team_name="{team_name}", task_id="...") - Get task details
- task_create(team_name="{team_name}", subject="...", description="...") - Create a new task

Messages from other agents will appear as user input in format: [Message from <name>]: <content>
When you receive a message, respond using send_message tool.

---

{prompt}"""


def discover_harness_binary(name: str) -> str | None:
    return shutil.which(name)


def use_tmux_windows() -> bool:
    """Return True when teammate processes should be spawned in tmux windows."""
    return os.environ.get("USE_TMUX_WINDOWS") is not None


def build_tmux_spawn_args(command: str, name: str) -> list[str]:
    """Build the tmux command used to spawn a teammate process."""
    if use_tmux_windows():
        return [
            "tmux",
            "new-window",
            "-dP",
            "-F",
            "#{window_id}",
            "-n",
            f"@claude-team | {name}",
            command,
        ]
    return ["tmux", "split-window", "-dP", "-F", "#{pane_id}", command]


def assign_color(team_name: str, base_dir: Path | None = None) -> str:
    config = teams.read_config(team_name, base_dir)
    count = sum(1 for m in config.members if isinstance(m, TeammateMember))
    return COLOR_PALETTE[count % len(COLOR_PALETTE)]


def build_codex_spawn_command(
    codex_binary: str,
    prompt: str,
    cwd: str,
) -> str:
    """Build the shell command to spawn a Codex CLI teammate."""
    return (
        f"cd {shlex.quote(cwd)} && "
        f"{shlex.quote(codex_binary)} "
        f"--dangerously-bypass-approvals-and-sandbox "
        f"--no-alt-screen "
        f"{shlex.quote(prompt)}"
    )


def _validate_spawn_args(name: str, codex_binary: str | None) -> None:
    """Validate spawn_external arguments, raising ValueError on failure."""
    if not _VALID_NAME_RE.match(name):
        raise ValueError(f"Invalid agent name: {name!r}. Use only letters, numbers, hyphens, underscores.")
    if len(name) > 64:
        raise ValueError(f"Agent name too long ({len(name)} chars, max 64)")
    if name == "team-lead":
        raise ValueError("Agent name 'team-lead' is reserved")
    if not codex_binary:
        raise ValueError(
            "Cannot spawn codex teammate: 'codex' binary not found on PATH. "
            "Install Codex CLI or ensure it is in your PATH."
        )


def spawn_external(
    team_name: str,
    name: str,
    prompt: str,
    codex_binary: str | None,
    *,
    subagent_type: str = "general-purpose",
    cwd: str | None = None,
    base_dir: Path | None = None,
) -> TeammateMember:
    """Spawn an external (non-Claude) agent in a tmux pane.

    Registers the agent in the team config, creates its inbox,
    sends the initial prompt, and spawns the Codex process.
    """
    _validate_spawn_args(name, codex_binary)
    assert codex_binary is not None  # guaranteed by _validate_spawn_args

    resolved_cwd = cwd or str(Path.cwd())
    color = assign_color(team_name, base_dir)
    now_ms = int(time.time() * 1000)

    member = TeammateMember(
        agent_id=f"{name}@{team_name}",
        name=name,
        agent_type=subagent_type,
        prompt=prompt,
        color=color,
        plan_mode_required=False,
        joined_at=now_ms,
        tmux_pane_id="",
        cwd=resolved_cwd,
        backend_type="external",
        is_active=False,
    )

    member_added = False
    try:
        teams.add_member(team_name, member, base_dir)
        member_added = True

        messaging.ensure_inbox(team_name, name, base_dir)
        initial_msg = InboxMessage(
            from_="team-lead",
            text=prompt,
            timestamp=messaging.now_iso(),
            read=False,
        )
        messaging.append_message(team_name, name, initial_msg, base_dir)

        wrapped = _CODEX_PROMPT_WRAPPER.format(
            name=name,
            team_name=team_name,
            prompt=prompt,
        )
        cmd = build_codex_spawn_command(codex_binary, wrapped, resolved_cwd)
        result = subprocess.run(
            build_tmux_spawn_args(cmd, name),
            capture_output=True,
            text=True,
            check=True,
        )
        pane_id = result.stdout.strip()

        config = teams.read_config(team_name, base_dir)
        for m in config.members:
            if isinstance(m, TeammateMember) and m.name == name:
                m.tmux_pane_id = pane_id
                break
        teams.write_config(team_name, config, base_dir)
    except Exception:
        if member_added:
            try:
                teams.remove_member(team_name, name, base_dir)
            except Exception:
                pass
        raise

    member.tmux_pane_id = pane_id
    return member


def kill_tmux_pane(pane_id: str) -> None:
    if pane_id.startswith("@"):
        subprocess.run(["tmux", "kill-window", "-t", pane_id], check=False)
        return
    subprocess.run(["tmux", "kill-pane", "-t", pane_id], check=False)
