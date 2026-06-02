"""Coordinator mode — orchestration via system prompt.

The coordinator is not a separate runtime process; it's the same QueryEngine
with a specialized system prompt that instructs the model to use AgentTool
for worker dispatching and SendMessage for communication.

Corresponds to TS: coordinator/coordinatorMode.ts.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def is_coordinator_mode() -> bool:
    """Check if CLAUDE_CODE_COORDINATOR_MODE env var is set."""
    # 通过环境变量判断是否启用协调器模式
    # 协调器模式下，主 agent 会作为"组长"角色分配任务给子 agent
    val = os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "")
    return val.lower() in ("1", "true", "yes")


def get_coordinator_config() -> dict[str, object]:
    """Get coordinator configuration from environment."""
    # 返回协调器的配置信息，主要包含是否启用及对应的环境变量名
    # 供上层模块查询协调器状态时使用
    return {
        "enabled": is_coordinator_mode(),
        "env_var": "CLAUDE_CODE_COORDINATOR_MODE",
    }


def maybe_inject_coordinator_prompt(base_prompt: str) -> str:
    """If coordinator mode is active, prepend coordinator instructions.

    The coordinator prompt tells the model to:
    1. Decompose tasks into parallel work units
    2. Spawn workers via AgentTool with run_in_background=true
    3. Collect results via <task-notification> messages
    4. Synthesize findings before directing implementation
    """
    # 如果未启用协调器模式，直接返回原始 prompt，不做任何修改
    if not is_coordinator_mode():
        return base_prompt

    # 延迟导入：避免在非协调器模式下加载不必要的模块，同时也防止循环导入
    from cc.prompts.coordinator_prompt import get_coordinator_prompt

    coordinator_prompt = get_coordinator_prompt()
    logger.info("Coordinator mode active — injecting orchestration prompt")
    # 将协调器指令前置于基础 prompt 之前
    # 这样模型会首先看到协调器行为指令，再看到通用 agent 指令
    return f"{coordinator_prompt}\n\n{base_prompt}"
