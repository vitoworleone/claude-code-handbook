"""Tests for P2a: Permission gate + non-interactive semantics."""

from __future__ import annotations

import pytest

from cc.permissions.gate import (
    PermissionContext,
    PermissionDecision,
    PermissionMode,
    check_permission,
)


class TestCheckPermission:
    def test_bypass_allows_everything(self) -> None:
        assert check_permission(PermissionMode.BYPASS, "Bash") == PermissionDecision.ALLOW
        assert check_permission(PermissionMode.BYPASS, "anything") == PermissionDecision.ALLOW

    def test_read_only_always_allowed(self) -> None:
        for tool in ("Read", "Glob", "Grep", "TaskGet", "TaskList"):
            assert check_permission(PermissionMode.DEFAULT, tool) == PermissionDecision.ALLOW

    def test_edit_tools_allowed_in_accept_edits(self) -> None:
        assert check_permission(PermissionMode.ACCEPT_EDITS, "Edit") == PermissionDecision.ALLOW
        assert check_permission(PermissionMode.ACCEPT_EDITS, "Write") == PermissionDecision.ALLOW

    def test_edit_tools_ask_in_default(self) -> None:
        assert check_permission(PermissionMode.DEFAULT, "Edit") == PermissionDecision.ASK
        assert check_permission(PermissionMode.DEFAULT, "Write") == PermissionDecision.ASK

    def test_bash_always_asks(self) -> None:
        assert check_permission(PermissionMode.ACCEPT_EDITS, "Bash") == PermissionDecision.ASK
        assert check_permission(PermissionMode.DEFAULT, "Bash") == PermissionDecision.ASK

    def test_unknown_tool_defaults_to_ask(self) -> None:
        """New/unknown tools should default to ASK, not ALLOW."""
        assert check_permission(PermissionMode.ACCEPT_EDITS, "FutureTool") == PermissionDecision.ASK
        assert check_permission(PermissionMode.DEFAULT, "WebSearch") == PermissionDecision.ASK
        assert check_permission(PermissionMode.ACCEPT_EDITS, "TeamCreate") == PermissionDecision.ASK
        assert check_permission(PermissionMode.ACCEPT_EDITS, "SendMessage") == PermissionDecision.ASK

    def test_agent_asks(self) -> None:
        assert check_permission(PermissionMode.ACCEPT_EDITS, "Agent") == PermissionDecision.ASK


class TestPermissionContextNonInteractive:
    @pytest.mark.asyncio
    async def test_non_interactive_denies_ask_tools(self) -> None:
        """Non-interactive context should deny tools that need ASK."""
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Bash", {"command": "ls"}) is False

    @pytest.mark.asyncio
    async def test_non_interactive_allows_read_tools(self) -> None:
        """Non-interactive context should still allow read-only tools."""
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Read", {"file_path": "/tmp/x"}) is True

    @pytest.mark.asyncio
    async def test_non_interactive_allows_edit_in_accept_mode(self) -> None:
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Edit", {}) is True

    @pytest.mark.asyncio
    async def test_bypass_mode_allows_all_non_interactive(self) -> None:
        ctx = PermissionContext(mode=PermissionMode.BYPASS, is_interactive=False)
        assert await ctx.check("Bash", {"command": "rm -rf /"}) is True

    @pytest.mark.asyncio
    async def test_always_allow_cached(self) -> None:
        """After 'always allow', subsequent checks should pass."""
        ctx = PermissionContext(mode=PermissionMode.BYPASS, is_interactive=False)
        # In bypass mode, everything is allowed, so _always_allow is not tested
        # Test the cache mechanism directly
        ctx._always_allow.add("Bash")
        ctx_strict = PermissionContext(mode=PermissionMode.DEFAULT, is_interactive=False)
        ctx_strict._always_allow.add("Bash")
        assert await ctx_strict.check("Bash", {}) is True
