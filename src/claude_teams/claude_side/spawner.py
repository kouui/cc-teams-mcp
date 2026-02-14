"""Spawner for external (non-Claude) agent instances in tmux.

Supports multiple backend types. Currently implemented: codex.
To add a new backend, add entries to BACKEND_BINARY_NAMES, _PROMPT_WRAPPERS,
and _SPAWN_COMMAND_BUILDERS.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Literal

from claude_teams.claude_side.registry import register_external_agent, unregister_external_agent
from claude_teams.common import messaging, teams
from claude_teams.common.models import InboxMessage, TeammateMember
from claude_teams.common.teams import _VALID_NAME_RE

# ---------------------------------------------------------------------------
# Backend type definition
# ---------------------------------------------------------------------------

BackendType = Literal["codex"]

# Binary name to search for on PATH, keyed by backend type
BACKEND_BINARY_NAMES: dict[str, str] = {
    "codex": "codex",
}

# ---------------------------------------------------------------------------
# Prompt wrappers per backend
# ---------------------------------------------------------------------------

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

_PROMPT_WRAPPERS: dict[str, str] = {
    "codex": _CODEX_PROMPT_WRAPPER,
}


def wrap_prompt(backend_type: BackendType, name: str, team_name: str, prompt: str) -> str:
    """Wrap a raw prompt with backend-specific team context."""
    template = _PROMPT_WRAPPERS[backend_type]
    return template.format(name=name, team_name=team_name, prompt=prompt)


# ---------------------------------------------------------------------------
# Spawn command builders per backend
# ---------------------------------------------------------------------------


def _build_codex_command(binary: str, prompt: str, cwd: str) -> str:
    return (
        f"cd {shlex.quote(cwd)} && "
        f"{shlex.quote(binary)} "
        f"--dangerously-bypass-approvals-and-sandbox "
        f"--no-alt-screen "
        f"{shlex.quote(prompt)}"
    )


_SPAWN_COMMAND_BUILDERS: dict[str, type[object] | object] = {
    "codex": _build_codex_command,
}


def build_spawn_command(backend_type: BackendType, binary: str, prompt: str, cwd: str) -> str:
    """Build the shell command to spawn an external agent."""
    builder = _SPAWN_COMMAND_BUILDERS[backend_type]
    return builder(binary, prompt, cwd)  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def discover_backend_binaries() -> dict[str, str]:
    """Discover available backend binaries on PATH.

    Returns a dict of {backend_type: binary_path} for all found binaries.
    """
    found: dict[str, str] = {}
    for backend_type, binary_name in BACKEND_BINARY_NAMES.items():
        path = shutil.which(binary_name)
        if path:
            found[backend_type] = path
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
        raise ValueError(
            f"Cannot spawn {backend_type} teammate: '{BACKEND_BINARY_NAMES[backend_type]}' binary not found on PATH."
        )


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
    2. Sends initial prompt to inbox
    3. Spawns the agent process in tmux
    4. Updates config with tmux pane ID

    Args:
        backend_type: Which CLI backend to use (e.g. "codex").
        binaries: Dict of {backend_type: binary_path} from discover_backend_binaries().

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
        # Step 2: Send initial prompt to inbox
        initial_msg = InboxMessage(
            from_="team-lead",
            text=prompt,
            timestamp=messaging.now_iso(),
            read=False,
        )
        messaging.append_message(team_name, name, initial_msg, base_dir)

        # Step 3: Spawn process in tmux
        wrapped = wrap_prompt(backend_type, name, team_name, prompt)
        cmd = build_spawn_command(backend_type, binary, wrapped, resolved_cwd)
        result = subprocess.run(
            build_tmux_spawn_args(cmd, name),
            capture_output=True,
            text=True,
            check=True,
        )
        pane_id = result.stdout.strip()

        # Step 4: Update config with pane ID
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
