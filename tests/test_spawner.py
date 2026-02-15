from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_teams.claude_side.registry import _external_agents, _next_color
from claude_teams.claude_side.spawner import (
    _has_tmux_session,
    build_spawn_command,
    build_tmux_spawn_args,
    discover_backend_binaries,
    kill_tmux_pane,
    spawn_external,
)
from claude_teams.common import messaging, teams
from claude_teams.common.models import COLOR_PALETTE, TeammateMember

TEAM = "test-team"
BINARIES = {"codex": "/usr/local/bin/codex"}


@pytest.fixture(autouse=True)
def _clear_external_registry():
    _external_agents.clear()
    yield
    _external_agents.clear()


@pytest.fixture
def team_dir(tmp_claude_dir: Path) -> Path:
    teams.create_team(TEAM, session_id="test-session-id", base_dir=tmp_claude_dir)
    return tmp_claude_dir


def _make_member(
    name: str,
    team: str = TEAM,
    color: str = "blue",
    agent_type: str = "general-purpose",
    cwd: str = "/tmp",
) -> TeammateMember:
    return TeammateMember(
        agent_id=f"{name}@{team}",
        name=name,
        agent_type=agent_type,
        prompt=f"You are {name}",
        color=color,
        joined_at=0,
        tmux_pane_id="",
        cwd=cwd,
        backend_type="in-process",
    )


class TestNextColor:
    def test_first_teammate_is_blue(self, team_dir: Path) -> None:
        color = _next_color(TEAM, base_dir=team_dir)
        assert color == "blue"

    def test_cycles(self, team_dir: Path) -> None:
        for i in range(len(COLOR_PALETTE)):
            member = _make_member(f"agent-{i}", color=COLOR_PALETTE[i])
            teams.add_member(TEAM, member, base_dir=team_dir)

        color = _next_color(TEAM, base_dir=team_dir)
        assert color == COLOR_PALETTE[0]


class TestBuildSpawnCommand:
    def test_codex_format(self) -> None:
        cmd = build_spawn_command("codex", "/usr/local/bin/codex", "Do research", "/tmp/work")
        assert "/usr/local/bin/codex" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--no-alt-screen" in cmd
        assert "cd /tmp/work" in cmd

    def test_codex_should_not_contain_claude_flags(self) -> None:
        cmd = build_spawn_command("codex", "/usr/local/bin/codex", "Do research", "/tmp")
        assert "CLAUDECODE" not in cmd
        assert "--agent-id" not in cmd
        assert "--team-name" not in cmd


class TestSpawnExternalNameValidation:
    def test_should_reject_empty_name(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            spawn_external(TEAM, "", "prompt", "codex", BINARIES, base_dir=team_dir)

    def test_should_reject_name_with_special_chars(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            spawn_external(TEAM, "agent!@#", "prompt", "codex", BINARIES, base_dir=team_dir)

    def test_should_reject_name_exceeding_64_chars(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="too long"):
            spawn_external(TEAM, "a" * 65, "prompt", "codex", BINARIES, base_dir=team_dir)

    def test_should_reject_reserved_name_team_lead(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="reserved"):
            spawn_external(TEAM, "team-lead", "prompt", "codex", BINARIES, base_dir=team_dir)

    def test_should_reject_when_binary_missing(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="binary not found"):
            spawn_external(TEAM, "worker", "prompt", "codex", {}, base_dir=team_dir)


class TestSpawnExternal:
    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_registers_member_before_spawn(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "researcher" in names

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_does_not_write_prompt_to_inbox(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        """Prompt is passed via CLI args, not inbox. Inbox should be empty after spawn."""
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        msgs = messaging.read_inbox(TEAM, "researcher", base_dir=team_dir)
        assert len(msgs) == 0

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_updates_pane_id(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        member = spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        assert member.tmux_pane_id == "%42"
        config = teams.read_config(TEAM, base_dir=team_dir)
        found = [m for m in config.members if m.name == "researcher"]
        assert found[0].tmux_pane_id == "%42"

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_should_use_new_window_when_enabled(
        self,
        mock_subprocess: MagicMock,
        team_dir: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("USE_TMUX_WINDOWS", "0")
        mock_subprocess.run.return_value.stdout = "@42\n"
        member = spawn_external(
            TEAM,
            "window-worker",
            "Do research",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        assert member.tmux_pane_id == "@42"
        call_args = mock_subprocess.run.call_args[0][0]
        assert call_args[:5] == ["tmux", "new-window", "-dP", "-F", "#{window_id}"]
        assert "-n" in call_args
        assert call_args[call_args.index("-n") + 1] == "@claude-team | window-worker"

    @patch("claude_teams.claude_side.spawner.subprocess.run")
    def test_should_rollback_member_when_tmux_spawn_fails(self, mock_run: MagicMock, team_dir: Path) -> None:
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, ["tmux", "split-window"])
        with pytest.raises(sp.CalledProcessError):
            spawn_external(
                TEAM,
                "broken-worker",
                "Do research",
                "codex",
                BINARIES,
                base_dir=team_dir,
            )

        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "broken-worker" not in names

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_should_kill_orphan_pane_when_config_write_fails(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        """If tmux spawn succeeds but config write-back fails, the pane must be killed."""
        mock_subprocess.run.return_value.stdout = "%99\n"

        killed_panes: list[str] = []

        def track_kill(pane_id: str) -> None:
            killed_panes.append(pane_id)

        # Let registration write_config succeed, then fail on pane ID update (2nd call)
        original_write = teams.write_config
        call_count = 0

        def fail_on_second_write(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise OSError("disk full")
            return original_write(*args, **kwargs)

        with (
            patch("claude_teams.claude_side.spawner.kill_tmux_pane", side_effect=track_kill),
            patch.object(teams, "write_config", side_effect=fail_on_second_write),
        ):
            with pytest.raises(OSError, match="disk full"):
                spawn_external(
                    TEAM,
                    "orphan-worker",
                    "Do stuff",
                    "codex",
                    BINARIES,
                    base_dir=team_dir,
                )

        assert "%99" in killed_panes

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_codex_should_use_prompt_wrapper(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "codex-worker",
            "Analyze code",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        call_args = mock_subprocess.run.call_args[0][0]
        cmd_str = call_args[-1]
        assert "codex-worker" in cmd_str
        assert TEAM in cmd_str
        assert "send_message" in cmd_str

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_member_has_in_process_backend_type(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        member = spawn_external(
            TEAM,
            "worker",
            "Do stuff",
            "codex",
            BINARIES,
            base_dir=team_dir,
        )
        assert member.backend_type == "in-process"


class TestKillTmuxPane:
    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_calls_subprocess(self, mock_subprocess: MagicMock) -> None:
        kill_tmux_pane("%99")
        mock_subprocess.run.assert_called_once_with(["tmux", "kill-pane", "-t", "%99"], check=False)

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_calls_kill_window_for_window_target(self, mock_subprocess: MagicMock) -> None:
        kill_tmux_pane("@99")
        mock_subprocess.run.assert_called_once_with(["tmux", "kill-window", "-t", "@99"], check=False)


class TestBuildTmuxSpawnArgs:
    @patch("claude_teams.claude_side.spawner._has_tmux_session", return_value=True)
    def test_uses_split_window_when_session_exists(self, _mock: MagicMock) -> None:
        args = build_tmux_spawn_args("echo hi", "worker")
        assert args[:2] == ["tmux", "split-window"]

    @patch("claude_teams.claude_side.spawner._has_tmux_session", return_value=False)
    def test_uses_new_session_when_no_session(self, _mock: MagicMock) -> None:
        args = build_tmux_spawn_args("echo hi", "worker")
        assert args[:2] == ["tmux", "new-session"]
        assert "-d" in args
        assert "-s" in args
        session_idx = args.index("-s")
        assert args[session_idx + 1] == "claude-agent-worker"
        assert "echo hi" in args

    @patch("claude_teams.claude_side.spawner._has_tmux_session", return_value=False)
    def test_new_session_sets_reasonable_size(self, _mock: MagicMock) -> None:
        args = build_tmux_spawn_args("echo hi", "w")
        assert "-x" in args and "200" in args
        assert "-y" in args and "50" in args

    def test_uses_new_window_when_env_set(self, monkeypatch) -> None:
        monkeypatch.setenv("USE_TMUX_WINDOWS", "1")
        args = build_tmux_spawn_args("echo hi", "worker")
        assert args[:2] == ["tmux", "new-window"]


class TestHasTmuxSession:
    @patch("claude_teams.claude_side.spawner.subprocess.run")
    def test_returns_true_when_sessions_exist(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="work: 1 windows\n")
        assert _has_tmux_session() is True

    @patch("claude_teams.claude_side.spawner.subprocess.run")
    def test_returns_false_when_no_server(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _has_tmux_session() is False

    @patch("claude_teams.claude_side.spawner.subprocess.run")
    def test_returns_false_when_empty_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert _has_tmux_session() is False


class TestDiscoverBackendBinaries:
    @patch("claude_teams.claude_side.spawner.shutil.which")
    def test_should_find_codex_binary(self, mock_which: MagicMock) -> None:
        mock_which.return_value = "/usr/local/bin/codex"
        result = discover_backend_binaries()
        assert result == {"codex": "/usr/local/bin/codex"}

    @patch("claude_teams.claude_side.spawner.shutil.which")
    def test_should_return_empty_when_not_found(self, mock_which: MagicMock) -> None:
        mock_which.return_value = None
        result = discover_backend_binaries()
        assert result == {}
