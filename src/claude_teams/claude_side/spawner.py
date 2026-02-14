"""Spawner for external (non-Claude) agent instances in tmux.

Currently supports codex backend. To add a new backend, extend BackendType
and add an elif branch in build_spawn_command / wrap_prompt / discover_backend_binaries.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Literal

from claude_teams.claude_side.registry import register_external_agent, unregister_external_agent
from claude_teams.common import teams
from claude_teams.common.models import TeammateMember
from claude_teams.common.teams import _VALID_NAME_RE

# ---------------------------------------------------------------------------
# Backend type definition
# ---------------------------------------------------------------------------

BackendType = Literal["codex"]

_CODEX_BINARY_NAME = "codex"

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_CODEX_PROMPT_TEMPLATE = """\
You are team member '{name}' on team '{team_name}'.
Your role: {agent_type}

## Team Members
{teammates_section}

## Communication
Use MCP tools from the 'claude-teams-external' server:
- send_message(team_name="{team_name}", sender="{name}", recipient="<name>", content="...", summary="...") — Send a message to any teammate
- task_list(team_name="{team_name}") — View team tasks
- task_update(team_name="{team_name}", task_id="...", status="...") — Update task status
- task_get(team_name="{team_name}", task_id="...") — Get task details
- task_create(team_name="{team_name}", subject="...", description="...") — Create a new task

## Rules
1. Messages from other agents will appear as user input in format: [Message from <name>]: <content>
2. When you receive a message, respond using send_message tool
3. When assigned a task, update its status to "in_progress" when starting and "completed" when done
4. Report progress to team-lead periodically via send_message

---

{prompt}"""


def _format_teammates_section(teammates: list[dict[str, str]]) -> str:
    """Format the team members section for the prompt."""
    if not teammates:
        return "(no other teammates yet)"
    lines = []
    for t in teammates:
        backend = t.get("backendType", "unknown")
        lines.append(f"- {t['name']} ({t['agentType']}, {backend})")
    return "\n".join(lines)


def wrap_prompt(
    backend_type: BackendType,
    name: str,
    team_name: str,
    prompt: str,
    agent_type: str = "general-purpose",
    teammates: list[dict[str, str]] | None = None,
) -> str:
    """Wrap a raw prompt with team context for the given backend."""
    if backend_type == "codex":
        template = _CODEX_PROMPT_TEMPLATE
    else:
        raise ValueError(f"Unknown backend type: {backend_type!r}")
    teammates_section = _format_teammates_section(teammates or [])
    return template.format(
        name=name,
        team_name=team_name,
        agent_type=agent_type,
        teammates_section=teammates_section,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Spawn command builders
# ---------------------------------------------------------------------------


def build_spawn_command(backend_type: BackendType, binary: str, prompt: str, cwd: str) -> str:
    """Build the shell command to spawn an external agent."""
    if backend_type == "codex":
        return (
            f"cd {shlex.quote(cwd)} && "
            f"{shlex.quote(binary)} "
            f"--dangerously-bypass-approvals-and-sandbox "
            f"--no-alt-screen "
            f"{shlex.quote(prompt)}"
        )
    raise ValueError(f"Unknown backend type: {backend_type!r}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def discover_backend_binaries() -> dict[str, str]:
    """Discover available backend binaries on PATH."""
    found: dict[str, str] = {}
    # Add new backends here
    path = shutil.which(_CODEX_BINARY_NAME)
    if path:
        found["codex"] = path
    return found


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


def _validate_spawn_args(name: str, binary: str | None, backend_type: BackendType) -> None:
    """Validate spawn_external arguments, raising ValueError on failure."""
    if not _VALID_NAME_RE.match(name):
        raise ValueError(f"Invalid agent name: {name!r}. Use only letters, numbers, hyphens, underscores.")
    if len(name) > 64:
        raise ValueError(f"Agent name too long ({len(name)} chars, max 64)")
    if name == "team-lead":
        raise ValueError("Agent name 'team-lead' is reserved")
    if not binary:
        raise ValueError(f"Cannot spawn {backend_type} teammate: binary not found on PATH.")


# ---------------------------------------------------------------------------
# Main spawn function
# ---------------------------------------------------------------------------


def spawn_external(
    team_name: str,
    name: str,
    prompt: str,
    backend_type: BackendType,
    binaries: dict[str, str],
    *,
    subagent_type: str = "general-purpose",
    cwd: str | None = None,
    base_dir: Path | None = None,
) -> TeammateMember:
    """Spawn an external (non-Claude) agent in a tmux pane.

    1. Registers the agent via registry (config + inbox)
    2. Spawns the agent process in tmux (prompt via CLI args)
    3. Updates config with tmux pane ID

    Returns the TeammateMember with tmux_pane_id populated.
    """
    binary = binaries.get(backend_type)
    _validate_spawn_args(name, binary, backend_type)
    assert binary is not None  # guaranteed by _validate_spawn_args

    resolved_cwd = cwd or str(Path.cwd())

    # Step 1: Register in team config + create inbox
    member = register_external_agent(
        team_name,
        name,
        agent_type=subagent_type,
        cwd=resolved_cwd,
        prompt=prompt,
        base_dir=base_dir,
    )

    try:
        # Step 2: Spawn process in tmux
        config = teams.read_config(team_name, base_dir)
        teammates = [
            {"name": m.name, "agentType": m.agent_type, "backendType": getattr(m, "backend_type", "claude")}
            for m in config.members
            if m.name != name  # exclude self
        ]
        wrapped = wrap_prompt(
            backend_type,
            name,
            team_name,
            prompt,
            agent_type=subagent_type,
            teammates=teammates,
        )
        cmd = build_spawn_command(backend_type, binary, wrapped, resolved_cwd)
        result = subprocess.run(
            build_tmux_spawn_args(cmd, name),
            capture_output=True,
            text=True,
            check=True,
        )
        pane_id = result.stdout.strip()

        # Step 3: Update config with pane ID
        config = teams.read_config(team_name, base_dir)
        for m in config.members:
            if isinstance(m, TeammateMember) and m.name == name:
                m.tmux_pane_id = pane_id
                break
        teams.write_config(team_name, config, base_dir)
    except Exception:
        # Rollback: unregister on failure
        try:
            unregister_external_agent(team_name, name, base_dir)
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
