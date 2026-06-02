"""Tests for W1: Agent permission wiring.

Verifies that sub-agents (foreground + background) get permission checkers.
"""

from __future__ import annotations

import pytest

from cc.tools.agent.agent_tool import AgentTool
from cc.tools.base import ToolRegistry


class TestAgentPermissionWiring:
    def test_foreground_agent_gets_permission_checker(self) -> None:
        """_build_sub_permission_checker returns a callable for foreground."""
        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        checker = tool._build_sub_permission_checker(is_background=False)
        assert callable(checker)

    def test_background_agent_gets_non_interactive_checker(self) -> None:
        """Background agent's checker must be non-interactive (fail-fast on ASK)."""
        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        checker = tool._build_sub_permission_checker(is_background=True)
        assert callable(checker)

    @pytest.mark.asyncio
    async def test_background_checker_denies_bash(self) -> None:
        """Background agent's permission checker must deny Bash (ASK → fail-fast)."""
        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        checker = tool._build_sub_permission_checker(is_background=True)
        # Bash requires ASK, background is non-interactive → should deny
        allowed = await checker("Bash", {"command": "echo hi"})
        assert allowed is False

    @pytest.mark.asyncio
    async def test_background_checker_allows_read(self) -> None:
        """Background agent can still read files (ALLOW in all modes)."""
        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        checker = tool._build_sub_permission_checker(is_background=True)
        allowed = await checker("Read", {"file_path": "/tmp/x"})
        assert allowed is True

    @pytest.mark.asyncio
    async def test_foreground_checker_allows_edit(self) -> None:
        """Foreground agent in ACCEPT_EDITS allows Edit."""
        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        checker = tool._build_sub_permission_checker(is_background=False)
        allowed = await checker("Edit", {})
        assert allowed is True
