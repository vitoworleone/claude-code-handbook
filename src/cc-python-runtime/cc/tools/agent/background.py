"""Background agent manager — tracks and manages background agent tasks.

P4a: Manages async agent tasks, integrates with TaskRegistry.
"""

# 本模块实现了后台 agent 的生命周期管理。
# 当 AgentTool 以 run_in_background=True 模式被调用时，
# 子 agent 的协程会交给 BackgroundAgentManager 管理。
#
# 管理器的职责：
# 1. 将 agent 协程包装为 asyncio.Task 并启动
# 2. 在 TaskRegistry 中注册任务，实现全局可追踪
# 3. 任务完成/失败时更新状态并将结果放入通知队列
# 4. 提供非阻塞的 poll_completed() 接口，让主循环可以检查完成通知
#
# 设计理念：主 agent 不需要主动等待后台 agent，而是在每次对话轮次中
# 通过 poll_completed() 检查是否有后台任务完成，完成的结果会被
# 注入到对话上下文中，让模型知道后台任务的结果。

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cc.session.task_registry import TaskRegistry

logger = logging.getLogger(__name__)


class BackgroundAgentManager:
    """Manages background agent tasks with TaskRegistry integration.

    - Spawns agents as asyncio tasks
    - Registers them in TaskRegistry for lifecycle tracking
    - Collects completion notifications
    """

    def __init__(self, task_registry: TaskRegistry | None = None) -> None:
        # task_registry: 全局任务注册表，可选。如果提供，后台任务会被注册其中，
        # 支持通过 /tasks 命令查看所有运行中的任务。
        self._task_registry = task_registry
        # _results: 完成通知队列，存储 (agent_id, result_text) 对。
        # 使用 asyncio.Queue 保证线程安全，且支持非阻塞读取。
        self._results: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        # _tasks: 保持对 asyncio.Task 的引用，防止被垃圾回收。
        # asyncio 不持有对 Task 的强引用，如果没有其他地方引用，
        # Task 可能在完成前被 GC 回收。
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def spawn(
        self,
        agent_id: str,
        description: str,
        coro: Any,
    ) -> str:
        """Spawn a background agent task.

        Returns the task_id from TaskRegistry (or agent_id if no registry).
        """
        # 将 agent 协程包装为 asyncio.Task，通过 _run_and_notify 添加
        # 完成回调逻辑（结果入队 + 状态更新）
        task = asyncio.create_task(self._run_and_notify(agent_id, coro))
        # 持有 task 的强引用，防止 GC 回收未完成的任务
        self._tasks[agent_id] = task

        task_id = agent_id
        if self._task_registry:
            # 在全局 TaskRegistry 中注册，使任务可通过 /tasks 命令查看
            task_id = self._task_registry.register(
                "background_agent",
                metadata={"agent_id": agent_id, "description": description},
                asyncio_task=task,
            )

        return task_id

    async def _run_and_notify(self, agent_id: str, coro: Any) -> None:
        """Run the agent coroutine and queue the result."""
        try:
            result = await coro
            result_text = result if isinstance(result, str) else str(result)
            # 将结果放入通知队列，主循环的 poll_completed() 会消费它
            await self._results.put((agent_id, result_text))

            # 更新 TaskRegistry 中的任务状态为已完成
            if self._task_registry:
                from cc.session.task_registry import TaskState

                # 通过 agent_id 在 metadata 中查找对应的 task record
                # 因为 TaskRegistry 的主键是 task_id（可能与 agent_id 不同），
                # 需要遍历所有记录来匹配
                for record in self._task_registry.list_all():
                    if record.metadata.get("agent_id") == agent_id:
                        self._task_registry.update_state(record.task_id, TaskState.COMPLETED)
                        break

            logger.info("Background agent completed: %s", agent_id)
        except asyncio.CancelledError:
            # 任务被外部取消（例如用户中断），不算错误
            logger.info("Background agent cancelled: %s", agent_id)
        except Exception as e:
            # agent 执行失败：将错误信息放入结果队列，让主 agent 知道失败原因
            logger.warning("Background agent failed: %s — %s", agent_id, e)
            await self._results.put((agent_id, f"Agent error: {e}"))

            # 更新 TaskRegistry 中的任务状态为失败
            if self._task_registry:
                from cc.session.task_registry import TaskState

                for record in self._task_registry.list_all():
                    if record.metadata.get("agent_id") == agent_id:
                        self._task_registry.update_state(record.task_id, TaskState.FAILED)
                        break

    async def poll_completed(self) -> list[tuple[str, str]]:
        """Non-blocking poll for completed agent results."""
        # 非阻塞地从结果队列中取出所有已完成的 agent 结果。
        # 主循环在每次对话轮次开始时调用此方法，
        # 将后台 agent 的完成结果注入到对话上下文中。
        # 使用 get_nowait() 而非 get()，因为 get() 在队列为空时会阻塞。
        results: list[tuple[str, str]] = []
        while not self._results.empty():
            try:
                results.append(self._results.get_nowait())
            except asyncio.QueueEmpty:
                # 并发环境下 empty() 和 get_nowait() 之间可能有竞态，
                # 捕获 QueueEmpty 确保不会异常退出
                break
        return results
