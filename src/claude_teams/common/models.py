from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag

COLOR_PALETTE: list[str] = [
    "blue",
    "green",
    "yellow",
    "purple",
    "orange",
    "pink",
    "cyan",
    "red",
]


class LeadMember(BaseModel):
    model_config = {"populate_by_name": True}

    agent_id: str = Field(alias="agentId")
    name: str
    agent_type: str = Field(alias="agentType")
    model: str
    joined_at: int = Field(alias="joinedAt")
    tmux_pane_id: str = Field(alias="tmuxPaneId", default="")
    cwd: str
    subscriptions: list = Field(default_factory=list)


class TeammateMember(BaseModel):
    model_config = {"populate_by_name": True}

    agent_id: str = Field(alias="agentId")
    name: str
    agent_type: str = Field(alias="agentType")
    model: str = ""
    prompt: str
    color: str
    plan_mode_required: bool = Field(alias="planModeRequired", default=False)
    joined_at: int = Field(alias="joinedAt")
    tmux_pane_id: str = Field(alias="tmuxPaneId")
    cwd: str
    subscriptions: list = Field(default_factory=list)
    backend_type: str = Field(alias="backendType", default="claude")
    is_active: bool = Field(alias="isActive", default=False)


def _discriminate_member(v: Any) -> str:
    if isinstance(v, dict):
        return "teammate" if "prompt" in v else "lead"
    if isinstance(v, TeammateMember):
        return "teammate"
    return "lead"


MemberUnion = Annotated[
    Annotated[LeadMember, Tag("lead")] | Annotated[TeammateMember, Tag("teammate")],
    Discriminator(_discriminate_member),
]


class TeamConfig(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    description: str = ""
    created_at: int = Field(alias="createdAt")
    lead_agent_id: str = Field(alias="leadAgentId")
    lead_session_id: str = Field(alias="leadSessionId")
    members: list[MemberUnion]


class TaskFile(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    subject: str
    description: str
    active_form: str = Field(alias="activeForm", default="")
    status: Literal["pending", "in_progress", "completed", "deleted"] = "pending"
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(alias="blockedBy", default_factory=list)
    owner: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class InboxMessage(BaseModel):
    model_config = {"populate_by_name": True}

    from_: str = Field(alias="from")
    text: str
    timestamp: str
    read: bool = False
    summary: str | None = Field(default=None)
    color: str | None = Field(default=None)


class TeamCreateResult(BaseModel):
    team_name: str
    team_file_path: str
    lead_agent_id: str


class TeamDeleteResult(BaseModel):
    success: bool
    message: str
    team_name: str


class SpawnResult(BaseModel):
    agent_id: str
    name: str
    team_name: str
    message: str = "The agent is now running and will receive instructions via mailbox."


class SendMessageResult(BaseModel):
    success: bool
    message: str
    routing: dict | None = None
    request_id: str | None = None
    target: str | None = None
