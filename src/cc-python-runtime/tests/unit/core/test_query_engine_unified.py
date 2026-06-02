"""Tests for W2: QueryEngine as sole runtime owner.

Verifies all code paths (submit, submit_messages, run_turn)
consistently pass permission_checker.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from cc.core.events import TurnComplete
from cc.core.query_engine import QueryEngine
from cc.models.messages import Usage, UserMessage
from cc.permissions.gate import PermissionContext, PermissionMode
from cc.tools.base import ToolRegistry


def _mock_query_loop_capturing_kwargs() -> tuple[list[dict[str, Any]], Any]:
    """Create a mock query_loop that captures kwargs for inspection."""
    captured: list[dict[str, Any]] = []

    async def mock_ql(**kwargs: Any) -> Any:  # type: ignore[misc]
        captured.append(kwargs)
        yield TurnComplete(stop_reason="end_turn", usage=Usage())

    return captured, mock_ql


class TestQueryEngineUnifiedPermissions:
    @pytest.mark.asyncio
    async def test_submit_passes_permission_checker(self) -> None:
        """submit() must pass permission_checker to query_loop."""
        captured, mock_ql = _mock_query_loop_capturing_kwargs()

        import cc.core.query_engine as mod
        orig = mod.query_loop
        mod.query_loop = mock_ql  # type: ignore[assignment]
        try:
            ctx = PermissionContext(mode=PermissionMode.DEFAULT, is_interactive=True)
            engine = QueryEngine(
                client=MagicMock(),
                model="test",
                registry=ToolRegistry(),
                system_prompt="test",
                permission_ctx=ctx,
            )
            _ = [e async for e in engine.submit("hello")]
            assert len(captured) == 1
            assert captured[0]["permission_checker"] is not None
        finally:
            mod.query_loop = orig  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_submit_messages_passes_permission_checker(self) -> None:
        """submit_messages() must ALSO pass permission_checker (W2 fix)."""
        captured, mock_ql = _mock_query_loop_capturing_kwargs()

        import cc.core.query_engine as mod
        orig = mod.query_loop
        mod.query_loop = mock_ql  # type: ignore[assignment]
        try:
            ctx = PermissionContext(mode=PermissionMode.DEFAULT, is_interactive=True)
            engine = QueryEngine(
                client=MagicMock(),
                model="test",
                registry=ToolRegistry(),
                system_prompt="test",
                permission_ctx=ctx,
            )
            msgs = [UserMessage(content="test")]
            _ = [e async for e in engine.submit_messages(msgs)]
            assert len(captured) == 1
            assert captured[0]["permission_checker"] is not None
        finally:
            mod.query_loop = orig  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_run_turn_passes_permission_checker(self) -> None:
        """run_turn() must pass permission_checker (W2 fix)."""
        captured, mock_ql = _mock_query_loop_capturing_kwargs()

        import cc.core.query_engine as mod
        orig = mod.query_loop
        mod.query_loop = mock_ql  # type: ignore[assignment]
        try:
            ctx = PermissionContext(mode=PermissionMode.DEFAULT, is_interactive=True)
            engine = QueryEngine(
                client=MagicMock(),
                model="test",
                registry=ToolRegistry(),
                system_prompt="test",
                permission_ctx=ctx,
            )
            engine.messages.append(UserMessage(content="hello"))
            _ = [e async for e in engine.run_turn()]
            assert len(captured) == 1
            assert captured[0]["permission_checker"] is not None
        finally:
            mod.query_loop = orig  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_no_permission_ctx_passes_none(self) -> None:
        """Without permission_ctx, all paths should pass None."""
        captured, mock_ql = _mock_query_loop_capturing_kwargs()

        import cc.core.query_engine as mod
        orig = mod.query_loop
        mod.query_loop = mock_ql  # type: ignore[assignment]
        try:
            engine = QueryEngine(
                client=MagicMock(),
                model="test",
                registry=ToolRegistry(),
                system_prompt="test",
                permission_ctx=None,
            )
            _ = [e async for e in engine.submit("hello")]
            assert captured[0]["permission_checker"] is None
        finally:
            mod.query_loop = orig  # type: ignore[assignment]
