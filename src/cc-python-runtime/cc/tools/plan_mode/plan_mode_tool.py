"""EnterPlanModeTool / ExitPlanModeTool — toggle plan mode state.

Corresponds to TS: tools/PlanMode (P5-7).

计划模式（Plan Mode）是一种"只思考不执行"的运行状态。
进入计划模式后，模型应只生成计划和分析，不调用任何会产生
副作用的工具（如 BashTool、FileEditTool 等）。
这在用户希望先审查方案再执行时非常有用。
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

# 模块级状态标记——当前使用全局变量实现，
# 后续 P2b 阶段将迁移到正式的会话状态管理中
_plan_mode_active: bool = False

ENTER_PLAN_MODE_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_NAME = "ExitPlanMode"


def is_plan_mode_active() -> bool:
    """Check whether plan mode is currently active.
    供引擎查询当前是否处于计划模式，以决定是否允许执行写操作。
    """
    return _plan_mode_active


def reset_plan_mode() -> None:
    """Reset plan mode state. Useful for testing.
    测试时重置状态，避免测试用例之间的状态泄漏。
    """
    global _plan_mode_active
    _plan_mode_active = False


class EnterPlanModeTool(Tool):
    """Enter plan mode — sets a state flag and returns confirmation.

    In plan mode the model should only plan and not execute actions.
    """

    def get_name(self) -> str:
        return ENTER_PLAN_MODE_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=ENTER_PLAN_MODE_NAME,
            description="Enter plan mode. In plan mode, only planning is performed without executing actions.",
            input_schema={
                "type": "object",
                "properties": {},
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 切换模式是轻量级操作且具有幂等性，不影响其他工具的并发执行
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        global _plan_mode_active
        _plan_mode_active = True
        logger.debug("Plan mode activated")
        return ToolResult(content="Plan mode activated. Only planning actions will be performed.")


class ExitPlanModeTool(Tool):
    """Exit plan mode — clears the state flag and returns confirmation."""

    def get_name(self) -> str:
        return EXIT_PLAN_MODE_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=EXIT_PLAN_MODE_NAME,
            description="Exit plan mode. Resume normal execution of actions.",
            input_schema={
                "type": "object",
                "properties": {},
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        global _plan_mode_active
        _plan_mode_active = False
        logger.debug("Plan mode deactivated")
        return ToolResult(content="Plan mode deactivated. Normal execution resumed.")
