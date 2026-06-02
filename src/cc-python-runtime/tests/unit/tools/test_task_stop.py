"""Tests for TaskStopTool.

Verifies stopping a task sets its status to 'stopped'.
"""

import pytest

from cc.tools.task_tools.task_tools import (
    TaskCreateTool,
    TaskStopTool,
    TaskStore,
)


class TestTaskStopTool:
    def _make_store(self) -> TaskStore:
        return TaskStore()

    @pytest.mark.asyncio
    async def test_stop_existing_task(self) -> None:
        store = self._make_store()
        create = TaskCreateTool(store)
        stop = TaskStopTool(store)

        result = await create.execute({"subject": "Running task"})
        assert not result.is_error
        task_id = result.content.split("#")[1].split(" ")[0]

        stop_result = await stop.execute({"task_id": task_id})
        assert not stop_result.is_error
        assert "stopped successfully" in stop_result.content

        # Verify task status changed
        task = store.get(task_id)
        assert task is not None
        assert task.status == "stopped"

    @pytest.mark.asyncio
    async def test_stop_nonexistent_task(self) -> None:
        store = self._make_store()
        stop = TaskStopTool(store)

        result = await stop.execute({"task_id": "nonexistent"})
        assert result.is_error
        assert "not found" in result.content

    @pytest.mark.asyncio
    async def test_stop_empty_task_id(self) -> None:
        store = self._make_store()
        stop = TaskStopTool(store)

        result = await stop.execute({"task_id": ""})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        store = self._make_store()
        stop = TaskStopTool(store)

        assert stop.get_name() == "TaskStop"
        schema = stop.get_schema()
        assert schema.name == "TaskStop"
        assert "task_id" in schema.input_schema["properties"]
        assert "task_id" in schema.input_schema["required"]
