<div align="center">

# cc-teams-mcp

MCP server that implements Claude Code's [agent teams](https://code.claude.com/docs/en/agent-teams) protocol for any MCP client.

Forked from [cs50victor/claude-code-teams-mcp](https://github.com/cs50victor/claude-code-teams-mcp) — thanks to cs50victor for the original work!

</div>

Claude Code has a built-in agent teams feature (shared task lists, inter-agent messaging, tmux-based spawning), but the protocol is internal and tightly coupled to its own tooling. This MCP server reimplements that protocol as a standalone [MCP](https://modelcontextprotocol.io/) server, making it available to any MCP client: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), or anything else that speaks MCP. Based on a [deep dive into Claude Code's internals](https://gist.github.com/cs50victor/0a7081e6824c135b4bdc28b566e1c719).

## Install

Claude Code — project scope (`.mcp.json` in project root) or user scope (`~/.claude.json`):

```json
{
  "mcpServers": {
    "claude-teams": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kouui/cc-teams-mcp",
        "claude-teams"
      ],
      "allowedTools": ["*"]
    }
  }
}
```

> **Note**: `"allowedTools": ["*"]` is required so that Claude Code can call all team coordination tools without prompting for permission on each invocation.

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.claude-teams]
command = "uvx"
args = ["--from", "git+https://github.com/kouui/cc-teams-mcp", "claude-teams"]
```

## Requirements

- Python 3.12+
- [tmux](https://github.com/tmux/tmux)
- At least one coding agent on PATH: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) or [Codex CLI](https://github.com/openai/codex) (`codex`)
- Codex teammates require the `claude-teams` MCP server configured in `~/.codex/config.toml`

## Configuration

| Variable                                    | Description                                          | Default                            |
| ------------------------------------------- | ---------------------------------------------------- | ---------------------------------- |
| `CLAUDE_TEAMS_BACKENDS`                     | Comma-separated enabled backends (`claude`, `codex`) | Auto-detect from connecting client |
| `USE_TMUX_WINDOWS`                          | Spawn teammates in tmux windows instead of panes     | _(unset)_                          |
| `CLAUDE_TEAMS_DANGEROUSLY_SKIP_PERMISSIONS` | Skip permission prompts for Claude Code teammates    | _(unset)_                          |

Without `CLAUDE_TEAMS_BACKENDS`, the server auto-detects the connecting client and enables only its backend. Set it explicitly to enable multiple backends:

```json
{
  "mcpServers": {
    "claude-teams": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kouui/cc-teams-mcp",
        "claude-teams"
      ],
      "env": {
        "CLAUDE_TEAMS_BACKENDS": "claude,codex"
      }
    }
  }
}
```

## Tools

| Tool                        | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| `team_create`               | Create a new agent team (one per session)                    |
| `team_delete`               | Delete team and all data (fails if teammates active)         |
| `spawn_teammate`            | Spawn a teammate in tmux (uses each backend's default model) |
| `send_message`              | Send DMs, broadcasts (lead only), shutdown/plan responses    |
| `read_inbox`                | Read messages from an agent's inbox                          |
| `check_teammate`            | Check teammate status, messages, and terminal output         |
| `read_config`               | Read team config and member list                             |
| `task_create`               | Create a task (auto-incrementing ID)                         |
| `task_update`               | Update task status, owner, dependencies, or metadata         |
| `task_list`                 | List all tasks                                               |
| `task_get`                  | Get full task details                                        |
| `force_kill_teammate`       | Kill a teammate's tmux pane/window and clean up              |
| `process_shutdown_approved` | Remove teammate after graceful shutdown                      |

## Backends

### Claude Code (`claude`)

Claude Code teammates use native agent teams flags (`--agent-id`, `--team-name`, etc.) and participate in the team protocol directly. Each teammate uses the CLI's default model.

### Codex CLI (`codex`)

Codex teammates are spawned as interactive `codex` processes in tmux with `--dangerously-bypass-approvals-and-sandbox --no-alt-screen`. They receive team coordination instructions via a prompt wrapper that teaches them to use MCP tools (`read_inbox`, `send_message`, `task_update`, etc.) from the `claude-teams` MCP server.

## Architecture

- **Spawning**: Teammates launch in tmux panes (default) or windows (`USE_TMUX_WINDOWS`). Each gets a unique agent ID and color from a round-robin palette.
- **Messaging**: JSON inboxes at `~/.claude/teams/<team>/inboxes/`. Lead messages anyone; teammates message only lead. Structured message types for task assignments, shutdown requests/approvals, and plan approvals.
- **Tasks**: JSON files at `~/.claude/tasks/<team>/`. Status lifecycle (`pending` → `in_progress` → `completed`), ownership with auto-notification, and DAG-based dependency management with cycle detection.
- **Concurrency**: Atomic config writes via `tempfile` + `os.replace`. Cross-process file locks via `filelock` for inbox and task operations.

```
~/.claude/
├── teams/<team>/
│   ├── config.json
│   └── inboxes/
│       ├── team-lead.json
│       ├── worker-1.json
│       └── .lock
└── tasks/<team>/
    ├── 1.json
    ├── 2.json
    └── .lock
```

## Development

```bash
uv sync                          # install dependencies
uv run pytest tests/ -x          # run tests
uv run ruff check src/           # lint
uv run pyright src/               # type check
```

## License

[MIT](./LICENSE)
