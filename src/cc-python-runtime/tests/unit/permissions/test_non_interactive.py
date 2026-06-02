"""Tests for P2a: Non-interactive permission semantics.

Verifies that --print mode, background agents, and teammates
cannot block on user input.
"""

from __future__ import annotations

import pytest

from cc.permissions.gate import PermissionContext, PermissionMode


class TestNonInteractiveSemantics:
    """All non-interactive scenarios must fail-fast on ASK decisions."""

    @pytest.mark.asyncio
    async def test_print_mode_denies_bash(self) -> None:
        """--print mode should deny Bash (cannot prompt user)."""
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Bash", {"command": "echo hi"}) is False

    @pytest.mark.asyncio
    async def test_print_mode_allows_read(self) -> None:
        """--print mode should allow Read (always safe)."""
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Read", {"file_path": "/tmp/x"}) is True

    @pytest.mark.asyncio
    async def test_background_agent_denies_interactive_tools(self) -> None:
        """Background agents cannot prompt — ASK tools denied."""
        ctx = PermissionContext(mode=PermissionMode.DEFAULT, is_interactive=False)
        # All these would need ASK in DEFAULT mode
        assert await ctx.check("Bash", {}) is False
        assert await ctx.check("Edit", {}) is False
        assert await ctx.check("Agent", {}) is False

    @pytest.mark.asyncio
    async def test_teammate_denies_interactive_tools(self) -> None:
        """Teammates cannot prompt — same as background."""
        ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
        assert await ctx.check("Bash", {}) is False
        assert await ctx.check("WebFetch", {}) is False

    @pytest.mark.asyncio
    async def test_bypass_mode_overrides_non_interactive(self) -> None:
        """Bypass mode should allow everything regardless of interactivity."""
        ctx = PermissionContext(mode=PermissionMode.BYPASS, is_interactive=False)
        assert await ctx.check("Bash", {"command": "rm -rf /"}) is True
        assert await ctx.check("Agent", {}) is True
