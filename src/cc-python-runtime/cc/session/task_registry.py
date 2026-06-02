"""Task registry — session-level tracking for background tasks.

Corresponds to TS: agents/task_runtime.py (cc-python-codex reference).

All background tasks (background agents, teammates, extraction) register here.
TaskStop queries this. Session save snapshots it. Session restore marks
non-terminal tasks as KILLED.

任务注册表是会话级别的后台任务管理中心，解决以下问题：
1. 统一追踪所有类型的后台任务（代理、队友、记忆提取等）
2. 提供任务取消机制（TaskStop 命令通过此处找到并取消任务）
3. 支持会话持久化和恢复（snapshot/restore）
4. 保证进程退出后不会留下"幽灵"任务（恢复时将非终态任务标记为 KILLED）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    import asyncio

logger = logging.getLogger(__name__)

# 任务 ID 前缀映射，通过前缀字母可以快速判断任务类型
# 例如 "a-1f3c2b4e" 一看就知道是 background_agent
_TYPE_PREFIXES = {
    "background_agent": "a",
    "in_process_teammate": "t",
    "extraction": "x",
    "local_bash": "b",
}


class TaskState(Enum):
    """Task lifecycle states.

    任务状态机：
    PENDING → RUNNING → COMPLETED
                     → FAILED
                     → KILLED（外部取消或进程退出）

    注意没有 PENDING → RUNNING 的显式转换，
    因为 register() 直接将任务设为 RUNNING 状态。
    PENDING 保留用于未来可能的队列化场景。
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


# 终态集合 —— 处于这些状态的任务不会再发生变化
# 使用 frozenset 确保不可变，且支持 O(1) 成员检查
TERMINAL_STATES = frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.KILLED})


@dataclass
class TaskRecord:
    """A registered background task.

    包含任务的元数据和运行时引用。
    _asyncio_task 是对底层 asyncio.Task 的引用，用于取消操作，
    但不会被序列化到快照中（进程重启后原有的 asyncio.Task 已失效）。
    """

    task_id: str
    task_type: str  # "background_agent" | "in_process_teammate" | "extraction"
    state: TaskState
    created_at: float
    updated_at: float = 0.0
    # metadata 存储任务特有的附加信息，如任务描述、目标等
    metadata: dict[str, Any] = field(default_factory=dict)
    # 运行时字段 —— 不参与持久化，repr=False 避免在日志中泄露
    _asyncio_task: asyncio.Task[Any] | None = field(default=None, repr=False)

    @property
    def is_terminal(self) -> bool:
        """任务是否已进入终态（完成/失败/被杀），终态任务不可再变更。"""
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        """Serialize for snapshot persistence.

        只序列化可持久化的字段，排除 _asyncio_task 运行时引用。
        state 使用 .value 转为字符串，确保 JSON 可序列化。
        """
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        """Deserialize from snapshot.

        从字典恢复 TaskRecord，state 字符串通过 TaskState() 还原为枚举值。
        _asyncio_task 在恢复时始终为 None（原进程已退出）。
        """
        return cls(
            task_id=data["task_id"],
            task_type=data["task_type"],
            state=TaskState(data["state"]),
            created_at=data["created_at"],
            updated_at=data.get("updated_at", 0.0),
            metadata=data.get("metadata", {}),
        )


class TaskRegistry:
    """Session-level task registry.

    - All background tasks register here with type + metadata
    - TaskStop looks up and cancels tasks via stop()
    - Session persistence snapshots/restores via snapshot()/restore()
    - On restore, non-terminal tasks are marked KILLED (process died)

    使用字典（task_id → TaskRecord）存储所有任务，支持 O(1) 的 ID 查找。
    整个生命周期内只增不删 —— 已完成/失败/取消的任务保留在注册表中，
    以便用户查看历史和调试。
    """

    def __init__(self) -> None:
        # task_id → TaskRecord 的映射，包含所有注册过的任务（含已结束的）
        self._tasks: dict[str, TaskRecord] = {}

    def register(
        self,
        task_type: str,
        metadata: dict[str, Any] | None = None,
        asyncio_task: asyncio.Task[Any] | None = None,
    ) -> str:
        """Register a new task. Returns the generated task_id.

        生成格式为 "<类型前缀>-<8位随机hex>" 的任务 ID，
        直接将任务设为 RUNNING 状态（跳过 PENDING），
        因为调用方通常在 asyncio.Task 已创建后才注册。
        """
        # 根据任务类型选择 ID 前缀，未知类型用 "u"（unknown）
        prefix = _TYPE_PREFIXES.get(task_type, "u")
        # 使用 UUID 前 8 位 hex 保证唯一性（32 bit 随机空间）
        task_id = f"{prefix}-{uuid4().hex[:8]}"
        now = time.time()

        record = TaskRecord(
            task_id=task_id,
            task_type=task_type,
            state=TaskState.RUNNING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            _asyncio_task=asyncio_task,
        )
        self._tasks[task_id] = record
        logger.debug("Task registered: %s (%s)", task_id, task_type)
        return task_id

    def update_state(self, task_id: str, state: TaskState) -> None:
        """Update a task's state.

        同时更新 updated_at 时间戳，用于追踪状态变更时间。
        对未知 task_id 发出警告但不抛异常，保持系统鲁棒性。
        """
        record = self._tasks.get(task_id)
        if record is None:
            logger.warning("update_state: unknown task %s", task_id)
            return
        record.state = state
        record.updated_at = time.time()
        logger.debug("Task %s → %s", task_id, state.value)

    def get(self, task_id: str) -> TaskRecord | None:
        """Look up a task by ID."""
        return self._tasks.get(task_id)

    def list_all(self) -> list[TaskRecord]:
        """Return all tasks.

        返回所有任务（含已结束的），用于 TaskList 命令展示完整历史。
        """
        return list(self._tasks.values())

    def list_active(self) -> list[TaskRecord]:
        """Return non-terminal tasks.

        只返回仍在运行中的任务，用于判断是否有未完成工作。
        """
        return [r for r in self._tasks.values() if not r.is_terminal]

    def stop(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if actually cancelled.

        取消流程：
        1. 查找任务记录
        2. 检查是否已在终态（已结束的任务不可重复取消）
        3. 如果有关联的 asyncio.Task 且未完成，调用 cancel() 发送取消信号
        4. 将状态更新为 KILLED
        """
        record = self._tasks.get(task_id)
        if record is None:
            return False
        if record.is_terminal:
            return False

        # 向底层 asyncio.Task 发送取消信号（CancelledError）
        if record._asyncio_task is not None and not record._asyncio_task.done():
            record._asyncio_task.cancel()

        record.state = TaskState.KILLED
        record.updated_at = time.time()
        logger.info("Task stopped: %s", task_id)
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        """Serialize all tasks for session persistence.

        将所有任务记录导出为字典列表，用于 save_session 持久化。
        """
        return [r.to_dict() for r in self._tasks.values()]

    def restore(self, records: list[dict[str, Any]]) -> None:
        """Restore tasks from a snapshot.

        Non-terminal tasks are marked KILLED (the process that ran them is gone).

        恢复策略的核心考虑：进程重启后，之前运行中的 asyncio.Task 已不存在，
        这些任务实际上已经"死亡"。将它们标记为 KILLED 可以：
        1. 正确反映实际状态（任务已无法继续执行）
        2. 避免 UI 错误地显示"运行中"的幽灵任务
        3. 允许用户看到哪些任务因崩溃而中断
        """
        now = time.time()
        for data in records:
            record = TaskRecord.from_dict(data)
            # 非终态任务说明它在上次进程退出时还在运行，现在必须标记为 KILLED
            if not record.is_terminal:
                record.state = TaskState.KILLED
                record.updated_at = now
                logger.info("Restored task %s marked KILLED (process exited)", record.task_id)
            self._tasks[record.task_id] = record
