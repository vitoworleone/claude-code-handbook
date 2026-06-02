"""Tests for EnterPlanModeTool / ExitPlanModeTool (P5-7).

Verifies plan mode state toggling and confirmation messages.
"""

from __future__ import annotations

import pytest

from cc.tools.plan_mode.plan_mode_tool import (
    EnterPlanModeTool,
    ExitPlanModeTool,
    is_plan_mode_active,
    reset_plan_mode,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Reset plan mode state before each test."""
    reset_plan_mode()


class TestEnterPlanModeTool:
    def test_name(self) -> None:
        tool = EnterPlanModeTool()
        assert tool.get_name() == "EnterPlanMode"

    def test_schema(self) -> None:
        tool = EnterPlanModeTool()
        schema = tool.get_schema()
        assert schema.name == "EnterPlanMode"

    def test_concurrency_safe(self) -> None:
        tool = EnterPlanModeTool()
        assert tool.is_concurrency_safe({}) is True

    @pytest.mark.asyncio
    async def test_execute_activates_plan_mode(self) -> None:
        tool = EnterPlanModeTool()
        assert not is_plan_mode_active()
        result = await tool.execute({})
        assert not result.is_error
        assert "activated" in result.content.lower()
        assert is_plan_mode_active()


class TestExitPlanModeTool:
    def test_name(self) -> None:
        tool = ExitPlanModeTool()
        assert tool.get_name() == "ExitPlanMode"

    def test_schema(self) -> None:
        tool = ExitPlanModeTool()
        schema = tool.get_schema()
        assert schema.name == "ExitPlanMode"

    def test_concurrency_safe(self) -> None:
        tool = ExitPlanModeTool()
        assert tool.is_concurrency_safe({}) is True

    @pytest.mark.asyncio
    async def test_execute_deactivates_plan_mode(self) -> None:
        # First enter plan mode
        enter_tool = EnterPlanModeTool()
        await enter_tool.execute({})
        assert is_plan_mode_active()

        # Now exit
        exit_tool = ExitPlanModeTool()
        result = await exit_tool.execute({})
        assert not result.is_error
        assert "deactivated" in result.content.lower()
        assert not is_plan_mode_active()

    @pytest.mark.asyncio
    async def test_exit_when_not_active(self) -> None:
        """Exiting when not active should still succeed."""
        tool = ExitPlanModeTool()
        assert not is_plan_mode_active()
        result = await tool.execute({})
        assert not result.is_error
        assert not is_plan_mode_active()


class TestPlanModeToggle:
    @pytest.mark.asyncio
    async def test_toggle_cycle(self) -> None:
        """Enter -> Exit -> Enter should work correctly."""
        enter_tool = EnterPlanModeTool()
        exit_tool = ExitPlanModeTool()

        assert not is_plan_mode_active()

        await enter_tool.execute({})
        assert is_plan_mode_active()

        await exit_tool.execute({})
        assert not is_plan_mode_active()

        await enter_tool.execute({})
        assert is_plan_mode_active()

    def test_reset_helper(self) -> None:
        """reset_plan_mode helper should clear state."""
        import cc.tools.plan_mode.plan_mode_tool as mod

        mod._plan_mode_active = True
        assert is_plan_mode_active()
        reset_plan_mode()
        assert not is_plan_mode_active()
