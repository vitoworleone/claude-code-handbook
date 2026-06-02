"""Task tools — in-memory task management (Create/Get/List/Update).

Corresponds to TS: tools/TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool.

5 个 task 工具的职责分工：
- TaskCreate: 创建新任务并分配唯一 ID，用于追踪长时间运行的工作项
- TaskGet: 按 ID 查询单个任务的详细信息（标题、描述、状态）
- TaskList: 列出所有任务及其状态概要，便于掌握全局进度
- TaskUpdate: 更新任务的状态或标题等字段，如标记完成
- TaskStop: 停止正在运行的任务，同时支持内存任务和后台智能体任务
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from cc.tools.base import Tool, ToolResult, ToolSchema


@dataclass
class Task:
    """A tracked task.
    使用 dataclass 而非 dict，以获得类型安全和属性访问的便利性。
    """

    id: str
    subject: str
    description: str = ""
    # 任务状态机：pending → in_progress → completed / stopped
    status: str = "pending"  # pending | in_progress | completed


class TaskStore:
    """In-memory task storage shared across all task tools.

    采用内存字典存储，因为任务生命周期与会话绑定。
    所有 task 工具共享同一个 store 实例，确保数据一致性。
    """

    def __init__(self) -> None:
        # 使用 dict 以 O(1) 复杂度按 ID 查找任务
        self._tasks: dict[str, Task] = {}

    def create(self, subject: str, description: str = "") -> Task:
        # 用 UUID 前 8 位作为任务 ID，在单个会话中碰撞概率极低
        task = Task(id=str(uuid4())[:8], subject=subject, description=description)
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_all(self) -> list[Task]:
        return list(self._tasks.values())

    def update(self, task_id: str, **kwargs: Any) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        # 动态属性更新：只更新传入的字段，保留其他字段不变
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        return task

    def stop(self, task_id: str) -> Task | None:
        """Stop a task by setting its status to 'stopped'.
        将任务标记为 stopped 状态——这是一个终态，表示任务被主动中止。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = "stopped"
        return task


# 全局 store 单例——所有 task 工具默认共享此实例，
# 也可以在测试时通过构造参数注入独立的 store
_store = TaskStore()


def get_task_store() -> TaskStore:
    return _store


class TaskCreateTool(Tool):
    """创建新任务。分配唯一 ID 并初始化为 pending 状态。"""

    def __init__(self, store: TaskStore | None = None) -> None:
        # 支持注入自定义 store，便于测试隔离
        self._store = store or _store

    def get_name(self) -> str:
        return "TaskCreate"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TaskCreate",
            description="Create a new task to track progress.",
            input_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task details"},
                },
                "required": ["subject"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        subject = tool_input.get("subject", "")
        description = tool_input.get("description", "")
        if not subject:
            return ToolResult(content="Error: subject is required", is_error=True)
        task = self._store.create(subject, description)
        return ToolResult(content=f"Task #{task.id} created: {task.subject}")


class TaskGetTool(Tool):
    """按 ID 获取任务详情。返回 JSON 格式便于模型结构化解析。"""

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store or _store

    def get_name(self) -> str:
        return "TaskGet"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TaskGet",
            description="Get details of a task by ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "taskId": {"type": "string", "description": "Task ID"},
                },
                "required": ["taskId"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        task_id = tool_input.get("taskId", "")
        task = self._store.get(task_id)
        if task is None:
            return ToolResult(content=f"Error: Task {task_id} not found", is_error=True)
        # 返回 JSON 格式，方便模型提取特定字段
        return ToolResult(content=json.dumps({
            "id": task.id,
            "subject": task.subject,
            "description": task.description,
            "status": task.status,
        }))


class TaskListTool(Tool):
    """列出所有任务。输出紧凑的单行摘要格式，节省上下文空间。"""

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store or _store

    def get_name(self) -> str:
        return "TaskList"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TaskList",
            description="List all tasks.",
            # 无需任何参数，列出全部任务
            input_schema={"type": "object", "properties": {}},
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        tasks = self._store.list_all()
        if not tasks:
            return ToolResult(content="No tasks")
        # 格式：#ID [status] subject — 紧凑的单行格式便于快速浏览
        lines = [f"#{t.id} [{t.status}] {t.subject}" for t in tasks]
        return ToolResult(content="\n".join(lines))


class TaskUpdateTool(Tool):
    """更新任务属性。支持部分更新——只传入需要修改的字段。"""

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store or _store

    def get_name(self) -> str:
        return "TaskUpdate"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TaskUpdate",
            description="Update a task's status or details.",
            input_schema={
                "type": "object",
                "properties": {
                    "taskId": {"type": "string", "description": "Task ID"},
                    "status": {"type": "string", "description": "New status"},
                    "subject": {"type": "string", "description": "New subject"},
                },
                "required": ["taskId"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        task_id = tool_input.get("taskId", "")
        # 提取除 taskId 之外的所有非 None 字段作为更新内容
        updates = {k: v for k, v in tool_input.items() if k != "taskId" and v is not None}
        task = self._store.update(task_id, **updates)
        if task is None:
            return ToolResult(content=f"Error: Task {task_id} not found", is_error=True)
        return ToolResult(content=f"Task #{task.id} updated: [{task.status}] {task.subject}")


class TaskStopTool(Tool):
    """Stop a running task.

    Corresponds to TS: tools/TaskStopTool.
    Checks both in-memory TaskStore (for TaskCreate'd tasks) and
    TaskRegistry (for background agents/teammates) — wired by main.py.

    该工具支持两种任务来源的停止操作：
    1. 内存中的任务（通过 TaskCreate 创建）——直接修改状态
    2. 后台智能体任务（通过 TaskRegistry 管理）——取消异步协程
    两种来源的查找采用"瀑布式"策略：先查内存，再查注册表。
    """

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store or _store
        # _task_registry 由 main.py 的 _build_engine() 在运行时注入，
        # 类型为 TaskRegistry，用于管理后台智能体/队友的生命周期
        self._task_registry: Any = None  # Set by main.py _build_engine()

    def get_name(self) -> str:
        return "TaskStop"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TaskStop",
            description="Stop a running task by ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to stop"},
                },
                "required": ["task_id"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        task_id = tool_input.get("task_id", "")
        if not task_id:
            return ToolResult(content="Error: task_id is required", is_error=True)

        # 瀑布式查找策略：先查内存任务（成本低），再查后台注册表
        task = self._store.stop(task_id)
        if task is not None:
            return ToolResult(content=f"Task #{task.id} stopped successfully")

        # 内存中未找到时，尝试在后台任务注册表中查找并停止
        if self._task_registry is not None:
            stopped = self._task_registry.stop(task_id)
            if stopped:
                return ToolResult(content=f"Background task {task_id} stopped successfully")

        return ToolResult(content=f"Error: Task {task_id} not found", is_error=True)
