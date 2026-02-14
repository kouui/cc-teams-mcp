"""MCP-A: Claude-side bridge server for managing external (non-Claude) agents.

This server is used by Claude Code team-lead to spawn, monitor, and shut down
non-Claude agents (e.g., Codex CLI) that participate in a native Claude Code team.

Claude Code handles team creation, messaging, and task management natively.
This server only bridges external agents into the native team system.
"""

import logging
from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan

from claude_teams.claude_side import watcher
from claude_teams.claude_side.registry import register_external_agent as _register_agent
from claude_teams.claude_side.registry import unregister_external_agent
from claude_teams.claude_side.spawner import (
    BackendType,
    discover_backend_binaries,
    kill_tmux_pane,
    spawn_external,
    use_tmux_windows,
)
from claude_teams.claude_side.tmux_introspection import peek_pane, resolve_pane_target
from claude_teams.common import tasks, teams
from claude_teams.common.models import SpawnResult, TeammateMember

logger = logging.getLogger(__name__)

_lifespan_state: dict[str, Any] = {}


@lifespan
async def app_lifespan(server):
    binaries = discover_backend_binaries()
    if not binaries:
        raise FileNotFoundError(
            "No external agent binary found on PATH. Install at least one supported backend (e.g., Codex CLI 'codex')."
        )
    logger.info("Discovered backend binaries: %s", binaries)

    _lifespan_state.clear()
    _lifespan_state["binaries"] = binaries
    try:
        yield _lifespan_state
    finally:
        # Clean up all watchers on server shutdown
        stopped = watcher.stop_all_watchers()
        if stopped:
            logger.info("Stopped %d inbox watcher(s) on server shutdown", stopped)


mcp = FastMCP(
    name="claude-teams-bridge",
    instructions=(
        "MCP server for managing non-Claude-Code agent teammates (e.g., Codex CLI) "
        "within your native Claude Code agent team. "
        "Use these tools ONLY for external (non-Claude) teammates. "
        "Claude Code teammates are managed via your native TeamCreate, SendMessage, and Task tools — "
        "do NOT use this server for Claude Code agents. "
        "External agents are spawned in tmux and bridged into the team's messaging system."
    ),
    lifespan=app_lifespan,
)


def _get_lifespan(ctx: Context) -> dict[str, Any]:
    return ctx.lifespan_context


def _find_teammate(team_name: str, name: str) -> TeammateMember | None:
    config = teams.read_config(team_name)
    for m in config.members:
        if isinstance(m, TeammateMember) and m.name == name:
            return m
    return None


@mcp.tool
def register_external_agent(
    team_name: str,
    name: str,
    ctx: Context,
    agent_type: str = "general-purpose",
    cwd: str = "",
) -> dict:
    """Register a non-Claude agent in the team config without spawning a process.

    Creates the agent's entry in config.json and its inbox file so that
    Claude Code's native SendMessage can write to it. Use spawn_external_agent
    to actually start the agent process after registration.
    """
    try:
        member = _register_agent(
            team_name,
            name,
            agent_type=agent_type,
            cwd=cwd,
        )
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    return {
        "agent_id": member.agent_id,
        "name": member.name,
        "team_name": team_name,
        "message": f"Agent {name!r} registered in team {team_name!r}. Use spawn_external_agent to start it.",
    }


@mcp.tool
async def spawn_external_agent(
    team_name: str,
    name: str,
    prompt: str,
    ctx: Context,
    backend_type: BackendType = "codex",
    subagent_type: str = "general-purpose",
    cwd: str = "",
) -> dict:
    """Spawn a new external (non-Claude) agent in a tmux {target}.

    The agent is registered in the team config, receives its initial prompt
    via inbox, and begins working autonomously. An inbox watcher is started
    to deliver messages to the agent via tmux injection.

    Args:
        backend_type: CLI backend to use. Currently supported: "codex".
        subagent_type: Role description for the agent (e.g., "code-reviewer").
        cwd: Working directory (must be an absolute path).

    Names must be unique within the team.
    """.format(target="window" if use_tmux_windows() else "pane")
    import os.path

    if not cwd or not os.path.isabs(cwd):
        raise ToolError("cwd is required and must be an absolute path.")
    ls = _get_lifespan(ctx)
    binaries: dict[str, str] = ls.get("binaries", {})
    try:
        member = spawn_external(
            team_name=team_name,
            name=name,
            prompt=prompt,
            backend_type=backend_type,
            binaries=binaries,
            subagent_type=subagent_type,
            cwd=cwd,
        )
    except ValueError as e:
        raise ToolError(str(e))

    # Start inbox watcher for this agent
    if member.tmux_pane_id:
        watcher.start_watcher(team_name, name, member.tmux_pane_id)
        logger.info("Started inbox watcher for %s@%s", name, team_name)

    return SpawnResult(
        agent_id=member.agent_id,
        name=member.name,
        team_name=team_name,
    ).model_dump()


def _check_tmux_status(pane_id_raw: str, include_output: bool, output_lines: int) -> dict:
    """Check tmux pane status and optionally capture output."""
    if not pane_id_raw:
        return {"alive": False, "error": "no tmux target recorded", "output": ""}
    pane_id, resolve_error = resolve_pane_target(pane_id_raw)
    if pane_id is None:
        return {"alive": False, "error": resolve_error, "output": ""}
    pane = peek_pane(pane_id, output_lines if include_output else 1)
    return {
        "alive": pane["alive"],
        "error": pane["error"],
        "output": pane["output"] if include_output else "",
    }


@mcp.tool
async def check_external_agent(
    team_name: str,
    agent_name: str,
    ctx: Context,
    include_output: bool = False,
    output_lines: int = 20,
) -> dict:
    """Check an external agent's status: alive/dead and optionally terminal output.

    Always non-blocking. Use parallel calls to check multiple agents."""
    output_lines = max(1, min(output_lines, 120))

    member = _find_teammate(team_name, agent_name)
    if member is None:
        raise ToolError(f"External agent {agent_name!r} not found in team {team_name!r}")

    tmux = _check_tmux_status(member.tmux_pane_id, include_output, output_lines)

    result: dict = {
        "name": agent_name,
        "alive": tmux["alive"],
        "error": tmux["error"],
        "watching": watcher.is_watching(team_name, agent_name),
    }
    if include_output:
        result["output"] = tmux["output"]
    return result


@mcp.tool
def shutdown_external_agent(team_name: str, agent_name: str, ctx: Context) -> dict:
    """Shut down an external agent by killing its tmux pane/window,
    stopping its inbox watcher, removing it from team config, and resetting its tasks."""
    if agent_name == "team-lead":
        raise ToolError("Cannot shut down team-lead")
    member = _find_teammate(team_name, agent_name)
    if member is None:
        raise ToolError(f"External agent {agent_name!r} not found in team {team_name!r}")

    # Stop inbox watcher first
    watcher.stop_watcher(team_name, agent_name)

    if member.tmux_pane_id:
        kill_tmux_pane(member.tmux_pane_id)
    unregister_external_agent(team_name, agent_name)
    tasks.reset_owner_tasks(team_name, agent_name)
    return {"success": True, "message": f"{agent_name} has been stopped and removed from team."}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
