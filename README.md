<div align="center">

# claude-teams

This a fork of https://github.com/cs50victor/claude-code-teams-mcp, thanks to great work by cs50victor!!

MCP server that implements Claude Code's [agent teams](https://code.claude.com/docs/en/agent-teams) protocol for any MCP client.

</div>



https://github.com/user-attachments/assets/531ada0a-6c36-45cd-8144-a092bb9f9a19



Claude Code has a built-in agent teams feature (shared task lists, inter-agent messaging, tmux-based spawning), but the protocol is internal and tightly coupled to its own tooling. This MCP server reimplements that protocol as a standalone [MCP](https://modelcontextprotocol.io/) server, making it available to any MCP client: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), or anything else that speaks MCP. Based on a [deep dive into Claude Code's internals](https://gist.github.com/cs50victor/0a7081e6824c135b4bdc28b566e1c719). PRs welcome.

## Install

> **Pin to a release tag** (e.g. `@v0.1.1`), not `main`. There are breaking changes between releases.

Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "claude-teams": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/cs50victor/claude-code-teams-mcp@v0.1.1", "claude-teams"]
    }
  }
}
```

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.claude-teams]
command = "uvx"
args = ["--from", "git+https://github.com/cs50victor/claude-code-teams-mcp@v0.1.1", "claude-teams"]
```

## Requirements

- Python 3.12+
- [tmux](https://github.com/tmux/tmux)
- At least one coding agent on PATH: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) or [Codex CLI](https://github.com/openai/codex) (`codex`)
- Codex teammates require the `claude-teams` MCP server configured in `~/.codex/config.toml`

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CLAUDE_TEAMS_BACKENDS` | Comma-separated enabled backends (`claude`, `codex`) | Auto-detect from connecting client |
| `USE_TMUX_WINDOWS` | Spawn teammates in tmux windows instead of panes | *(unset)* |
| `CLAUDE_TEAMS_DANGEROUSLY_SKIP_PERMISSIONS` | Skip permission prompts for Claude Code teammates | *(unset)* |

Without `CLAUDE_TEAMS_BACKENDS`, the server auto-detects the connecting client and enables only its backend. Set it explicitly to enable multiple backends:

```json
{
  "mcpServers": {
    "claude-teams": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/cs50victor/claude-code-teams-mcp@v0.1.1", "claude-teams"],
      "env": {
        "CLAUDE_TEAMS_BACKENDS": "claude,codex"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `team_create` | Create a new agent team (one per session) |
| `team_delete` | Delete team and all data (fails if teammates active) |
| `spawn_teammate` | Spawn a teammate in tmux |
| `send_message` | Send DMs, broadcasts (lead only), shutdown/plan responses |
| `read_inbox` | Read messages from an agent's inbox |
| `check_teammate` | Check teammate status, messages, and terminal output |
| `read_config` | Read team config and member list |
| `task_create` | Create a task (auto-incrementing ID) |
| `task_update` | Update task status, owner, dependencies, or metadata |
| `task_list` | List all tasks |
| `task_get` | Get full task details |
| `force_kill_teammate` | Kill a teammate's tmux pane/window and clean up |
| `process_shutdown_approved` | Remove teammate after graceful shutdown |

## Backends

### Claude Code (`claude`)

Claude Code teammates use native agent teams flags (`--agent-id`, `--team-name`, etc.) and participate in the team protocol directly.

### Codex CLI (`codex`)

Codex teammates are spawned as interactive `codex` processes in tmux with `--dangerously-bypass-approvals-and-sandbox --no-alt-screen`. They receive team coordination instructions via a prompt wrapper that teaches them to use MCP tools (`read_inbox`, `send_message`, `task_update`, etc.) from the `claude-teams` MCP server.

## Architecture

- **Spawning**: Teammates launch in tmux panes (default) or windows (`USE_TMUX_WINDOWS`). Each gets a unique agent ID and color.
- **Messaging**: JSON inboxes at `~/.claude/teams/<team>/inboxes/`. Lead messages anyone; teammates message only lead.
- **Tasks**: JSON files at `~/.claude/tasks/<team>/`. Status tracking, ownership, and dependency management.
- **Concurrency**: Atomic writes via `tempfile` + `os.replace`. Cross-platform file locks via `filelock`.

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

## License

[MIT](./LICENSE)
