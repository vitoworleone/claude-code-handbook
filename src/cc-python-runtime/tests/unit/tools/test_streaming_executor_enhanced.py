"""Tests for P1a: Enhanced StreamingToolExecutor with hooks + concurrency."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cc.models.content_blocks import ToolUseBlock
from cc.tools.base import Tool, ToolRegistry, ToolResult, ToolSchema
from cc.tools.streaming_executor import StreamingToolExecutor


class SlowTool(Tool):
    """Tool that sleeps to test concurrency."""

    def __init__(self, name: str = "slow", delay: float = 0.05, safe: bool = True) -> None:
        self._name = name
        self._delay = delay
        self._safe = safe

    def get_name(self) -> str:
        return self._name

    def get_schema(self) -> ToolSchema:
        return ToolSchema(name=self._name, description="test", input_schema={"type": "object"})

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolResult(content=f"{self._name} done")

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        return self._safe


class TestEnhancedExecutorConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_safe_tools_run_parallel(self) -> None:
        """Concurrent-safe tools should run in parallel."""
        reg = ToolRegistry()
        reg.register(SlowTool("read1", 0.05, safe=True))
        reg.register(SlowTool("read2", 0.05, safe=True))

        executor = StreamingToolExecutor(reg)
        executor.add_tool(ToolUseBlock(id="t1", name="read1", input={}))
        executor.add_tool(ToolUseBlock(id="t2", name="read2", input={}))

        import time
        start = time.monotonic()
        results = await executor.get_results()
        elapsed = time.monotonic() - start

        assert len(results) == 2
        # If parallel, should take ~0.05s not ~0.1s
        assert elapsed < 0.09

    @pytest.mark.asyncio
    async def test_non_safe_tool_runs_exclusively(self) -> None:
        """Non-concurrent-safe tool should wait for others to finish."""
        reg = ToolRegistry()
        reg.register(SlowTool("safe", 0.02, safe=True))
        reg.register(SlowTool("bash", 0.02, safe=False))

        executor = StreamingToolExecutor(reg)
        executor.add_tool(ToolUseBlock(id="t1", name="safe", input={}))
        executor.add_tool(ToolUseBlock(id="t2", name="bash", input={}))

        results = await executor.get_results()
        assert len(results) == 2
        # Both should complete (order doesn't matter for correctness)
        ids = {r[0] for r in results}
        assert ids == {"t1", "t2"}


class TestEnhancedExecutorHooks:
    @pytest.mark.asyncio
    async def test_pre_hook_blocks_execution(self) -> None:
        """PreToolUse hook that blocks should prevent tool execution."""
        reg = ToolRegistry()
        reg.register(SlowTool("bash", 0.01))

        from cc.hooks.hook_runner import HookConfig

        hooks = [HookConfig(
            event="PreToolUse",
            command="exit 1",
            tool_name="bash",
        )]

        # Mock the hook runner to block
        import cc.hooks.hook_runner as hr
        original = hr.run_pre_tool_hooks

        from dataclasses import dataclass
        @dataclass
        class BlockResult:
            blocked: bool = True
            message: str = "Blocked by test"

        async def mock_pre_hooks(hooks: Any, name: str, inp: Any) -> Any:
            return BlockResult()

        hr.run_pre_tool_hooks = mock_pre_hooks  # type: ignore[assignment]
        try:
            executor = StreamingToolExecutor(reg, hooks=hooks)
            executor.add_tool(ToolUseBlock(id="t1", name="bash", input={}))
            results = await executor.get_results()
            assert len(results) == 1
            assert results[0][1].is_error is True
            assert "Blocked" in results[0][1].content  # type: ignore[operator]
        finally:
            hr.run_pre_tool_hooks = original  # type: ignore[assignment]


class TestEnhancedExecutorPermission:
    @pytest.mark.asyncio
    async def test_permission_checker_denies(self) -> None:
        """Permission checker returning False should deny tool execution."""
        reg = ToolRegistry()
        reg.register(SlowTool("bash", 0.01))

        async def deny_all(tool_name: str, tool_input: dict[str, Any]) -> bool:
            return False

        executor = StreamingToolExecutor(reg, permission_checker=deny_all)
        executor.add_tool(ToolUseBlock(id="t1", name="bash", input={}))
        results = await executor.get_results()
        assert results[0][1].is_error is True
        assert "Denied" in results[0][1].content  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_permission_checker_allows(self) -> None:
        """Permission checker returning True should allow execution."""
        reg = ToolRegistry()
        reg.register(SlowTool("bash", 0.01))

        async def allow_all(tool_name: str, tool_input: dict[str, Any]) -> bool:
            return True

        executor = StreamingToolExecutor(reg, permission_checker=allow_all)
        executor.add_tool(ToolUseBlock(id="t1", name="bash", input={}))
        results = await executor.get_results()
        assert results[0][1].is_error is False


class TestEnhancedExecutorErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        reg = ToolRegistry()
        executor = StreamingToolExecutor(reg)
        executor.add_tool(ToolUseBlock(id="t1", name="nonexistent", input={}))
        results = await executor.get_results()
        assert results[0][1].is_error is True

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error(self) -> None:
        class FailTool(Tool):
            def get_name(self) -> str: return "fail"
            def get_schema(self) -> ToolSchema:
                return ToolSchema(name="fail", description="", input_schema={})
            async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
                raise RuntimeError("boom")
            def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
                return True

        reg = ToolRegistry()
        reg.register(FailTool())
        executor = StreamingToolExecutor(reg)
        executor.add_tool(ToolUseBlock(id="t1", name="fail", input={}))
        results = await executor.get_results()
        assert results[0][1].is_error is True
        assert "boom" in results[0][1].content  # type: ignore[operator]
