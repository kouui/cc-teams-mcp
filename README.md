<div align="center">

# cc-teams-mcp

Hybrid bridge that lets non-Claude agents (e.g., Codex CLI) participate in Claude Code's native [agent teams](https://code.claude.com/docs/en/agent-teams).

Forked from [cs50victor/claude-code-teams-mcp](https://github.com/cs50victor/claude-code-teams-mcp) — thanks to cs50victor for the original work!

</div>

## What is this?

Claude Code has a built-in agent teams feature with event-driven messaging — messages are automatically injected into agent context as new conversation turns. This project provides **two MCP servers** that bridge non-Claude agents into this native system:

- **MCP-A (`claude-teams-bridge`)**: Used by Claude Code team-lead to spawn, monitor, and shut down external agents. Watches inbox files and injects messages into external agent tmux panes via `send-keys`.
- **MCP-B (`claude-teams-external`)**: Used by non-Claude agents (e.g., Codex CLI) to send messages and manage tasks. Messages written to inbox files are automatically picked up by Claude Code's native runtime.

### Communication Paths

| From → To               | Mechanism                                               | Latency  |
| ----------------------- | ------------------------------------------------------- | -------- |
| Claude → Claude         | Native message injection (built-in)                     | ~instant |
| Claude → Non-Claude     | Claude writes to inbox → MCP-A watcher → tmux send-keys | ~seconds |
| Non-Claude → Claude     | MCP-B writes to inbox → Claude runtime auto-injects     | ~instant |
| Non-Claude → Non-Claude | MCP-B writes to inbox → MCP-A watcher → tmux send-keys  | ~seconds |

**No polling required for any agent type.**

## Install

### MCP-A: For Claude Code team-lead

Add to `.mcp.json` (project scope) or `~/.claude.json` (user scope):

```json
{
  "mcpServers": {
    "claude-teams-bridge": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kouui/cc-teams-mcp",
        "claude-teams-bridge"
      ],
      "allowedTools": ["*"]
    }
  }
}
```

### MCP-B: For Codex CLI teammates

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.claude-teams-external]
command = "uvx"
args = ["--from", "git+https://github.com/kouui/cc-teams-mcp", "claude-teams-external"]
```

> MCP-B is automatically configured when Codex teammates are spawned via MCP-A.

## Requirements

- Python 3.12+
- [tmux](https://github.com/tmux/tmux)
- At least one external agent CLI on PATH: [Codex CLI](https://github.com/openai/codex) (`codex`)

## Tools

### MCP-A: `claude-teams-bridge`

| Tool                      | Description                                                    |
| ------------------------- | -------------------------------------------------------------- |
| `spawn_external_agent`    | Spawn an external agent in tmux with inbox watcher             |
| `check_external_agent`    | Check agent status: alive/dead, watcher state, terminal output |
| `shutdown_external_agent` | Kill tmux pane, stop watcher, unregister, reset tasks          |

### MCP-B: `claude-teams-external`

| Tool           | Description                                         |
| -------------- | --------------------------------------------------- |
| `send_message` | Send a message to any team member (writes to inbox) |
| `task_create`  | Create a new task                                   |
| `task_list`    | List all tasks                                      |
| `task_get`     | Get task details                                    |
| `task_update`  | Update task status, owner, dependencies             |

## Configuration

| Variable           | Description                            | Default   |
| ------------------ | -------------------------------------- | --------- |
| `USE_TMUX_WINDOWS` | Spawn in tmux windows instead of panes | _(unset)_ |

## Architecture

```
Claude Code (team-lead)
  ├── Native: TeamCreate / SendMessage / Task tools
  │   └── Event-driven message delivery (auto-injected turns)
  │
  ├── MCP-A (claude-teams-bridge)
  │   ├── Tools: spawn, check, shutdown
  │   └── Inbox watcher → tmux send-keys injection
  │
  └── Claude teammates (native communication, no MCP needed)

Non-Claude instance (e.g., Codex in tmux pane)
  ├── MCP-B (claude-teams-external)
  │   ├── Tools: send_message, task_*
  │   └── Writes to inbox files (read: false)
  └── Initial prompt: team info + MCP-B tool usage
```

### File Layout

```
~/.claude/
├── teams/<team>/
│   ├── config.json
│   └── inboxes/
│       ├── team-lead.json
│       ├── codex-worker.json
│       └── .lock
└── tasks/<team>/
    ├── 1.json
    └── .lock
```

### Package Structure

```
src/claude_teams/
  common/          # Shared: models, messaging, tasks, teams, _filelock
  claude_side/     # MCP-A: server, spawner, registry, watcher, injector
  external_side/   # MCP-B: server
```

## Backends

Currently supported: **Codex CLI** (`codex`).

Codex teammates are spawned in tmux with `--dangerously-bypass-approvals-and-sandbox --no-alt-screen`. They receive a prompt wrapper with team context (members list, MCP-B tool usage, communication rules).

Adding a new backend requires extending `BackendType` and adding `elif` branches in `build_spawn_command`, `wrap_prompt`, and `discover_backend_binaries` in `spawner.py`.

## Development

```bash
uv sync                          # install dependencies
uv run pytest tests/ -x          # run tests
uv run ruff check src/           # lint
uv run pyright src/              # type check
```

## License

[MIT](./LICENSE)
