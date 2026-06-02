"""Tests for P0.5a: QueryEngine abstraction.

Verifies that QueryEngine encapsulates runtime dependencies
and can be used by main.py, AgentTool, etc.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from cc.core.events import TurnComplete
from cc.core.query_engine import QueryEngine
from cc.models.messages import Usage, UserMessage
from cc.tools.base import ToolRegistry


def _make_mock_client() -> MagicMock:
    """Create a mock Anthropic client."""
    return MagicMock()


class TestQueryEngineInit:
    def test_creates_with_required_args(self) -> None:
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="You are helpful.",
        )
        assert engine.model == "test-model"
        assert engine.system_prompt == "You are helpful."
        assert engine.messages == []
        assert engine.total_input_tokens == 0
        assert engine.total_output_tokens == 0

    def test_permission_ctx_optional(self) -> None:
        """permission_ctx=None should work (pre-P2a behavior)."""
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="test",
            permission_ctx=None,
        )
        assert engine._permission_ctx is None

    def test_model_setter(self) -> None:
        engine = QueryEngine(
            client=_make_mock_client(),
            model="old-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )
        engine.model = "new-model"
        assert engine.model == "new-model"


class TestQueryEngineMakeCallModel:
    def test_make_call_model_returns_callable(self) -> None:
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )
        call_model = engine.make_call_model()
        assert callable(call_model)

    def test_make_call_model_with_override(self) -> None:
        engine = QueryEngine(
            client=_make_mock_client(),
            model="default-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )
        # Should not crash with model override
        call_model = engine.make_call_model(model="override-model")
        assert callable(call_model)


class TestQueryEngineFactory:
    def test_make_call_model_factory(self) -> None:
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )
        factory = engine.make_call_model_factory()
        assert callable(factory)

        # Factory should produce callable
        call_model = factory(model="sub-model")
        assert callable(call_model)


class TestQueryEngineSubmit:
    @pytest.mark.asyncio
    async def test_submit_adds_user_message(self) -> None:
        """submit() should add UserMessage to messages list."""
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )

        # Mock query_loop to yield nothing (just to test message addition)
        import cc.core.query_engine as qe_mod

        original_query_loop = qe_mod.query_loop

        async def mock_query_loop(**kwargs: Any) -> Any:  # type: ignore[misc]
            yield TurnComplete(stop_reason="end_turn", usage=Usage())

        qe_mod.query_loop = mock_query_loop  # type: ignore[assignment]
        try:
            [e async for e in engine.submit("hello")]
            assert len(engine.messages) >= 1
            assert isinstance(engine.messages[0], UserMessage)
            assert engine.messages[0].content == "hello"
        finally:
            qe_mod.query_loop = original_query_loop  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_submit_messages_does_not_wrap(self) -> None:
        """submit_messages() should use messages as-is."""
        engine = QueryEngine(
            client=_make_mock_client(),
            model="test-model",
            registry=ToolRegistry(),
            system_prompt="test",
        )

        import cc.core.query_engine as qe_mod

        original_query_loop = qe_mod.query_loop

        async def mock_query_loop(**kwargs: Any) -> Any:  # type: ignore[misc]
            # Verify messages are passed through
            assert len(kwargs["messages"]) == 2
            yield TurnComplete(stop_reason="end_turn", usage=Usage())

        qe_mod.query_loop = mock_query_loop  # type: ignore[assignment]
        try:
            from cc.models.messages import Message

            msgs: list[Message] = [
                UserMessage(content="first"),
                UserMessage(content="second"),
            ]
            events = [e async for e in engine.submit_messages(msgs)]
            assert len(events) == 1
        finally:
            qe_mod.query_loop = original_query_loop  # type: ignore[assignment]
