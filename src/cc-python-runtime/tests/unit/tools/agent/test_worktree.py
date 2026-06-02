"""Tests for P4c: AgentTool worktree isolation.

Mocks subprocess calls to verify worktree creation and cleanup logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cc.tools.agent.worktree import cleanup_agent_worktree, create_agent_worktree


class TestCreateAgentWorktree:
    @pytest.mark.asyncio
    async def test_creates_worktree_successfully(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await create_agent_worktree("/repo", "test-123")

        assert "cc-agent-worktree-test-123" in result
        mock_exec.assert_called_once()
        args = mock_exec.call_args
        assert args[0][0] == "git"
        assert args[0][1] == "worktree"
        assert args[0][2] == "add"
        assert args[0][3] == "--detach"
        assert args[1]["cwd"] == "/repo"

    @pytest.mark.asyncio
    async def test_raises_on_git_failure(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"not a git repo"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(RuntimeError, match="not a git repo"),
        ):
            await create_agent_worktree("/not-a-repo", "agent-1")


class TestCleanupAgentWorktree:
    @pytest.mark.asyncio
    async def test_removes_clean_worktree(self) -> None:
        # status --porcelain returns empty (no changes)
        status_proc = AsyncMock()
        status_proc.returncode = 0
        status_proc.communicate = AsyncMock(return_value=(b"", b""))

        # worktree remove succeeds
        remove_proc = AsyncMock()
        remove_proc.returncode = 0
        remove_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[status_proc, remove_proc],
        ) as mock_exec:
            await cleanup_agent_worktree("/tmp/worktree", "/repo")

        assert mock_exec.call_count == 2
        # First call: git status --porcelain
        first_call = mock_exec.call_args_list[0]
        assert first_call[0] == ("git", "status", "--porcelain")
        assert first_call[1]["cwd"] == "/tmp/worktree"
        # Second call: git worktree remove
        second_call = mock_exec.call_args_list[1]
        assert second_call[0] == ("git", "worktree", "remove", "/tmp/worktree")
        assert second_call[1]["cwd"] == "/repo"

    @pytest.mark.asyncio
    async def test_keeps_dirty_worktree(self) -> None:
        # status --porcelain returns changes
        status_proc = AsyncMock()
        status_proc.returncode = 0
        status_proc.communicate = AsyncMock(return_value=(b" M file.py\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=status_proc,
        ) as mock_exec:
            await cleanup_agent_worktree("/tmp/worktree", "/repo")

        # Should only call status, not remove
        assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_remove_failure_gracefully(self) -> None:
        status_proc = AsyncMock()
        status_proc.returncode = 0
        status_proc.communicate = AsyncMock(return_value=(b"", b""))

        remove_proc = AsyncMock()
        remove_proc.returncode = 1
        remove_proc.communicate = AsyncMock(return_value=(b"", b"locked"))

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[status_proc, remove_proc],
        ):
            # Should not raise — just logs warning
            await cleanup_agent_worktree("/tmp/worktree", "/repo")


class TestAgentToolWorktreeSchema:
    def test_schema_has_isolation_param(self) -> None:
        from cc.tools.agent.agent_tool import AgentTool
        from cc.tools.base import ToolRegistry

        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        schema = tool.get_schema()
        props = schema.input_schema["properties"]
        assert "isolation" in props
        assert props["isolation"]["enum"] == ["worktree"]
