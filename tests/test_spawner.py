from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_teams.claude_side.spawner import (
    assign_color,
    build_codex_spawn_command,
    discover_harness_binary,
    kill_tmux_pane,
    spawn_external,
)
from claude_teams.common import messaging, teams
from claude_teams.common.models import COLOR_PALETTE, TeammateMember

TEAM = "test-team"


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
        backend_type="external",
    )


class TestAssignColor:
    def test_first_teammate_is_blue(self, team_dir: Path) -> None:
        color = assign_color(TEAM, base_dir=team_dir)
        assert color == "blue"

    def test_cycles(self, team_dir: Path) -> None:
        for i in range(len(COLOR_PALETTE)):
            member = _make_member(f"agent-{i}", color=COLOR_PALETTE[i])
            teams.add_member(TEAM, member, base_dir=team_dir)

        color = assign_color(TEAM, base_dir=team_dir)
        assert color == COLOR_PALETTE[0]


class TestBuildCodexSpawnCommand:
    def test_format(self) -> None:
        cmd = build_codex_spawn_command("/usr/local/bin/codex", "Do research", "/tmp/work")
        assert "/usr/local/bin/codex" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--no-alt-screen" in cmd
        assert "cd /tmp/work" in cmd

    def test_should_not_contain_claude_flags(self) -> None:
        cmd = build_codex_spawn_command("/usr/local/bin/codex", "Do research", "/tmp")
        assert "CLAUDECODE" not in cmd
        assert "--agent-id" not in cmd
        assert "--team-name" not in cmd


class TestSpawnExternalNameValidation:
    def test_should_reject_empty_name(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            spawn_external(TEAM, "", "prompt", "/bin/codex", base_dir=team_dir)

    def test_should_reject_name_with_special_chars(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            spawn_external(TEAM, "agent!@#", "prompt", "/bin/codex", base_dir=team_dir)

    def test_should_reject_name_exceeding_64_chars(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="too long"):
            spawn_external(TEAM, "a" * 65, "prompt", "/bin/codex", base_dir=team_dir)

    def test_should_reject_reserved_name_team_lead(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="reserved"):
            spawn_external(TEAM, "team-lead", "prompt", "/bin/codex", base_dir=team_dir)

    def test_should_reject_when_codex_binary_missing(self, team_dir: Path) -> None:
        with pytest.raises(ValueError, match="codex"):
            spawn_external(TEAM, "worker", "prompt", None, base_dir=team_dir)


class TestSpawnExternal:
    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_registers_member_before_spawn(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "/usr/local/bin/codex",
            base_dir=team_dir,
        )
        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "researcher" in names

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_writes_prompt_to_inbox(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "/usr/local/bin/codex",
            base_dir=team_dir,
        )
        msgs = messaging.read_inbox(TEAM, "researcher", base_dir=team_dir)
        assert len(msgs) == 1
        assert msgs[0].from_ == "team-lead"
        assert msgs[0].text == "Do research"

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_updates_pane_id(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        member = spawn_external(
            TEAM,
            "researcher",
            "Do research",
            "/usr/local/bin/codex",
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
            "/usr/local/bin/codex",
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
                "/usr/local/bin/codex",
                base_dir=team_dir,
            )

        config = teams.read_config(TEAM, base_dir=team_dir)
        names = [m.name for m in config.members]
        assert "broken-worker" not in names

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_codex_should_use_prompt_wrapper(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        spawn_external(
            TEAM,
            "codex-worker",
            "Analyze code",
            "/usr/local/bin/codex",
            base_dir=team_dir,
        )
        call_args = mock_subprocess.run.call_args[0][0]
        cmd_str = call_args[-1]
        assert "codex-worker" in cmd_str
        assert TEAM in cmd_str
        assert "send_message" in cmd_str

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_member_has_external_backend_type(self, mock_subprocess: MagicMock, team_dir: Path) -> None:
        mock_subprocess.run.return_value.stdout = "%42\n"
        member = spawn_external(
            TEAM,
            "worker",
            "Do stuff",
            "/usr/local/bin/codex",
            base_dir=team_dir,
        )
        assert member.backend_type == "external"


class TestKillTmuxPane:
    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_calls_subprocess(self, mock_subprocess: MagicMock) -> None:
        kill_tmux_pane("%99")
        mock_subprocess.run.assert_called_once_with(["tmux", "kill-pane", "-t", "%99"], check=False)

    @patch("claude_teams.claude_side.spawner.subprocess")
    def test_calls_kill_window_for_window_target(self, mock_subprocess: MagicMock) -> None:
        kill_tmux_pane("@99")
        mock_subprocess.run.assert_called_once_with(["tmux", "kill-window", "-t", "@99"], check=False)


class TestDiscoverHarnessBinary:
    @patch("claude_teams.claude_side.spawner.shutil.which")
    def test_should_find_codex_binary(self, mock_which: MagicMock) -> None:
        mock_which.return_value = "/usr/local/bin/codex"
        assert discover_harness_binary("codex") == "/usr/local/bin/codex"
        mock_which.assert_called_once_with("codex")

    @patch("claude_teams.claude_side.spawner.shutil.which")
    def test_should_return_none_when_not_found(self, mock_which: MagicMock) -> None:
        mock_which.return_value = None
        assert discover_harness_binary("codex") is None
