"""Teammate spawn logic — creates and starts in-process teammates.

Spawns an in-process teammate by creating its identity, registering it
in the team file and TaskRegistry, and starting the execution loop.

Corresponds to TS: utils/swarm/spawnInProcess.ts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

    from cc.session.task_registry import TaskRegistry
    from cc.tools.base import ToolRegistry

from cc.swarm.identity import format_agent_id
from cc.swarm.in_process_runner import InProcessTeammate
from cc.swarm.team_file import TeamMember, add_member

logger = logging.getLogger(__name__)

# 模块级别的运行中任务注册表
# 以 task_id 为 key 保存正在执行的 asyncio.Task
# 用于在 spawn 后追踪和查询 teammate 的运行状态
_running_tasks: dict[str, asyncio.Task[str]] = {}


async def spawn_teammate(
    team_name: str,
    agent_name: str,
    prompt: str,
    call_model_factory: Any,
    parent_registry: ToolRegistry,
    claude_dir: Path | None = None,
    task_registry: TaskRegistry | None = None,
) -> str:
    """Spawn an in-process teammate with real execution loop.

    Creates identity, registers in team file + TaskRegistry,
    starts InProcessTeammate as background asyncio.Task.

    整个 spawn 流程分为三步：
    1. 注册：将 teammate 信息写入 team file（持久化的团队成员清单）
    2. 启动：创建 InProcessTeammate 实例并以 asyncio.Task 形式在后台运行
    3. 回调：注册 done_callback，在任务完成/失败时自动清理状态

    Returns task_id for tracking.
    """
    # 构造标准格式的 agent_id（"name@team"）
    agent_id = format_agent_id(agent_name, team_name)
    # 生成唯一的 task_id，使用 uuid 前 8 位作为标识符，足够防止实际使用中的碰撞
    task_id = f"teammate-{uuid4().hex[:8]}"

    logger.info("Spawning teammate: %s (task_id=%s)", agent_id, task_id)

    # --- 第 1 步：注册到 team file ---
    # 将 teammate 信息持久化到 ~/.claude/teams/{team}/config.json
    # 这使得 team-lead 可以随时查看当前团队的成员列表
    member = TeamMember(
        agent_id=agent_id,
        name=agent_name,
        joined_at=time.time(),
        is_active=True,
    )
    try:
        add_member(team_name, member, claude_dir)
    except ValueError as e:
        # 团队不存在时仅记录警告而不中断 spawn 流程
        # 因为 team file 注册是"最佳努力"的，不影响 teammate 实际执行
        logger.warning("Could not add member to team file: %s", e)

    # --- 第 2 步：创建 teammate 实例并启动后台任务 ---
    teammate = InProcessTeammate(
        agent_id=agent_id,
        team_name=team_name,
        agent_name=agent_name,
        call_model_factory=call_model_factory,
        parent_registry=parent_registry,
        claude_dir=claude_dir,
    )

    # 使用 asyncio.create_task 在事件循环中创建后台协程
    # teammate 的执行与主 agent 并发运行，不阻塞主循环
    task = asyncio.create_task(teammate.run(prompt))
    _running_tasks[task_id] = task

    # 同时注册到 TaskRegistry（如果可用），提供统一的任务管理能力
    # TaskRegistry 是更高层的抽象，支持状态查询、任务列表等功能
    if task_registry is not None:

        reg_id = task_registry.register(
            "in_process_teammate",
            metadata={"agent_id": agent_id, "team_name": team_name, "agent_name": agent_name},
            asyncio_task=task,
        )
        logger.debug("Teammate registered in TaskRegistry: %s → %s", task_id, reg_id)

    # --- 第 3 步：注册完成回调 ---
    def _on_done(t: asyncio.Task[str]) -> None:
        # 从运行中任务表中移除（pop 而非 del，避免 KeyError）
        _running_tasks.pop(task_id, None)
        # 更新 TaskRegistry 中对应记录的状态
        if task_registry is not None:
            from cc.session.task_registry import TaskState

            # 通过检查 task 是否有异常来判断最终状态
            state = TaskState.FAILED if t.exception() else TaskState.COMPLETED
            # 在 TaskRegistry 中找到对应的记录并更新
            for rec in task_registry.list_all():
                if rec.metadata.get("agent_id") == agent_id:
                    task_registry.update_state(rec.task_id, state)
                    break
        if t.exception():
            logger.error("Teammate %s failed: %s", agent_id, t.exception())

    # add_done_callback 确保任务结束后（无论成功或失败）自动执行清理逻辑
    task.add_done_callback(_on_done)
    return task_id


def get_running_tasks() -> dict[str, asyncio.Task[str]]:
    # 返回当前所有运行中的 teammate 任务的快照（浅拷贝，防止外部修改原始 dict）
    return dict(_running_tasks)
