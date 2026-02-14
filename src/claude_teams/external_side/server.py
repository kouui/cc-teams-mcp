"""MCP-B: External-side server for non-Claude agents.

This server provides communication and task management tools for non-Claude
agents (e.g., Codex CLI) that participate in a Claude Code agent team.

Non-Claude agents use this server to:
- Send messages to other team members (writes to inbox files)
- Manage shared tasks (create, update, list, get)

Messages written to inbox files are automatically picked up:
- By Claude Code agents: via native inbox file watching (auto-injected as turns)
- By other external agents: via MCP-A's inbox watcher + tmux injection
"""

import logging

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from claude_teams.common import messaging, tasks, teams
from claude_teams.common.models import SendMessageResult

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="claude-teams-external",
    instructions=(
        "MCP server for non-Claude agent teammates to communicate and manage tasks "
        "within a Claude Code agent team. "
        "Use send_message to communicate with other team members. "
        "Use task tools to view and manage shared tasks."
    ),
)


def _content_metadata(content: str, sender: str) -> str:
    """Append sender signature and reply reminder to outgoing message content."""
    return (
        f"{content}\n\n"
        f"<system_reminder>"
        f"This message was sent from {sender}. "
        f"Use your send_message tool to respond."
        f"</system_reminder>"
    )


@mcp.tool
def send_message(
    team_name: str,
    sender: str,
    recipient: str,
    content: str,
    summary: str,
) -> dict:
    """Send a message to any team member.

    Writes to the recipient's inbox file. Claude Code agents pick this up
    automatically via native file watching. External agents receive it via
    tmux injection.

    Args:
        team_name: The team name.
        sender: Your agent name (must match your registered name).
        recipient: Target agent name (e.g., 'team-lead', or another teammate).
        content: The message content.
        summary: Brief summary of the message.
    """
    if not content:
        raise ToolError("Message content must not be empty")
    if not summary:
        raise ToolError("Message summary must not be empty")
    if not sender:
        raise ToolError("Sender must not be empty")
    if not recipient:
        raise ToolError("Recipient must not be empty")
    if sender == recipient:
        raise ToolError("Cannot send a message to yourself")

    try:
        config = teams.read_config(team_name)
    except FileNotFoundError:
        raise ToolError(f"Team {team_name!r} not found")

    member_names = {m.name for m in config.members}
    if sender not in member_names:
        raise ToolError(f"Sender {sender!r} is not a member of team {team_name!r}")
    if recipient not in member_names:
        raise ToolError(f"Recipient {recipient!r} is not a member of team {team_name!r}")

    enriched = _content_metadata(content, sender)
    messaging.send_plain_message(
        team_name,
        sender,
        recipient,
        enriched,
        summary=summary,
    )
    # CC team-lead when non-lead agents message each other directly
    if sender != "team-lead" and recipient != "team-lead":
        messaging.send_plain_message(
            team_name,
            sender,
            "team-lead",
            enriched,
            summary=f"[CC {sender}->{recipient}] {summary}",
        )
    return SendMessageResult(
        success=True,
        message=f"Message sent to {recipient}",
    ).model_dump(exclude_none=True)


@mcp.tool
def task_create(
    team_name: str,
    subject: str,
    description: str,
    active_form: str = "",
    metadata: dict | None = None,
) -> dict:
    """Create a new task for the team. Tasks are auto-assigned incrementing IDs.
    Optional metadata dict is stored alongside the task."""
    try:
        task = tasks.create_task(team_name, subject, description, active_form, metadata)
    except ValueError as e:
        raise ToolError(str(e))
    return {"id": task.id, "status": task.status}


@mcp.tool
def task_list(team_name: str) -> list[dict]:
    """List all tasks for a team with their current status and assignments."""
    try:
        result = tasks.list_tasks(team_name)
    except ValueError as e:
        raise ToolError(str(e))
    return [t.model_dump(by_alias=True, exclude_none=True) for t in result]


@mcp.tool
def task_get(team_name: str, task_id: str) -> dict:
    """Get full details of a specific task by ID."""
    try:
        task = tasks.get_task(team_name, task_id)
    except FileNotFoundError:
        raise ToolError(f"Task {task_id!r} not found in team {team_name!r}")
    return task.model_dump(by_alias=True, exclude_none=True)


@mcp.tool
def task_update(
    team_name: str,
    task_id: str,
    status: str | None = None,
    owner: str | None = None,
    subject: str | None = None,
    description: str | None = None,
    active_form: str | None = None,
    add_blocks: list[str] | None = None,
    add_blocked_by: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Update a task's fields. Setting status to 'deleted' removes the task.
    Metadata keys are merged into existing metadata (set a key to null to delete it)."""
    try:
        task = tasks.update_task(
            team_name,
            task_id,
            status=status,
            owner=owner,
            subject=subject,
            description=description,
            active_form=active_form,
            add_blocks=add_blocks,
            add_blocked_by=add_blocked_by,
            metadata=metadata,
        )
    except FileNotFoundError:
        raise ToolError(f"Task {task_id!r} not found in team {team_name!r}")
    except ValueError as e:
        raise ToolError(str(e))
    return {"id": task.id, "status": task.status}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
