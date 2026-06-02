"""Tests for P4a: AgentTool background mode + BackgroundAgentManager."""

from __future__ import annotations

import asyncio

import pytest

from cc.session.task_registry import TaskRegistry, TaskState
from cc.tools.agent.background import BackgroundAgentManager


class TestBackgroundAgentManager:
    @pytest.mark.asyncio
    async def test_spawn_and_complete(self) -> None:
        reg = TaskRegistry()
        mgr = BackgroundAgentManager(task_registry=reg)

        async def simple_agent() -> str:
            return "done"

        task_id = await mgr.spawn("a1", "test agent", simple_agent())
        assert task_id.startswith("a-")

        # Wait for completion
        await asyncio.sleep(0.05)

        results = await mgr.poll_completed()
        assert len(results) == 1
        assert results[0] == ("a1", "done")

        # TaskRegistry should show completed
        record = reg.get(task_id)
        assert record is not None
        assert record.state == TaskState.COMPLETED

    @pytest.mark.asyncio
    async def test_spawn_failure(self) -> None:
        reg = TaskRegistry()
        mgr = BackgroundAgentManager(task_registry=reg)

        async def failing_agent() -> str:
            raise RuntimeError("boom")

        task_id = await mgr.spawn("a2", "failing", failing_agent())
        await asyncio.sleep(0.05)

        results = await mgr.poll_completed()
        assert len(results) == 1
        assert "error" in results[0][1].lower()

        record = reg.get(task_id)
        assert record is not None
        assert record.state == TaskState.FAILED

    @pytest.mark.asyncio
    async def test_without_registry(self) -> None:
        mgr = BackgroundAgentManager()  # No registry

        async def simple() -> str:
            return "ok"

        task_id = await mgr.spawn("a3", "no reg", simple())
        assert task_id == "a3"  # Falls back to agent_id
        await asyncio.sleep(0.05)
        results = await mgr.poll_completed()
        assert len(results) == 1


class TestAgentToolBackgroundSchema:
    def test_schema_has_background_params(self) -> None:
        from cc.tools.agent.agent_tool import AgentTool
        from cc.tools.base import ToolRegistry

        tool = AgentTool(
            parent_registry=ToolRegistry(),
            call_model_factory=lambda **k: None,
        )
        schema = tool.get_schema()
        props = schema.input_schema["properties"]
        assert "run_in_background" in props
        assert "subagent_type" in props
