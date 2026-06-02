"""Integration test for query loop with real API.

Verifies T5.1/T5.2: Full query loop with text response and tool_use cycle.

Requires ANTHROPIC_API_KEY to be set AND network access to api.anthropic.com.
W4: Tests now explicitly detect network failures and skip (not fail) when
the environment can't reach the API.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cc.api.claude import stream_response
from cc.core.events import (
    ErrorEvent,
    QueryEvent,
    TextDelta,
    ToolResultReady,
    ToolUseStart,
    TurnComplete,
)
from cc.core.query_loop import query_loop
from cc.models.messages import Message, UserMessage
from cc.prompts.builder import build_system_prompt
from cc.tools.base import ToolRegistry
from cc.tools.bash.bash_tool import BashTool

PROJECT_ROOT = Path(__file__).parent.parent.parent


def get_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


API_KEY = get_api_key()
skip_no_key = pytest.mark.skipif(API_KEY is None, reason="No API key available")


def _check_for_connection_error(events: list[QueryEvent]) -> None:
    """W4: If we got a connection error, skip the test instead of failing.

    This distinguishes 'network unavailable' (environment issue)
    from 'code bug' (logic issue).
    """
    for event in events:
        if isinstance(event, ErrorEvent) and "Connection error" in event.message:
            pytest.skip(
                f"Network unavailable — skipping online test. "
                f"Error: {event.message}"
            )


@skip_no_key
class TestQueryLoopRealAPI:
    """Integration tests that call the real Anthropic API.

    These tests require both a valid API key AND network access.
    If the network is unavailable, they skip (not fail).
    """

    async def test_simple_text_response(self) -> None:
        """Send a simple prompt and get a text response."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=API_KEY)
        system = "You are a helpful assistant. Respond with exactly one word."
        registry = ToolRegistry()

        messages: list[Message] = [UserMessage(content="Say hello")]
        events: list[QueryEvent] = []

        async def mock_call_model(**kwargs: Any) -> Any:
            async for event in stream_response(client, **kwargs):
                yield event

        async for event in query_loop(
            messages=messages,
            system_prompt=system,
            tools=registry,
            call_model=mock_call_model,
            max_turns=1,
        ):
            events.append(event)

        # W4: Check for network failure before asserting
        _check_for_connection_error(events)

        text_events = [e for e in events if isinstance(e, TextDelta)]
        turn_events = [e for e in events if isinstance(e, TurnComplete)]

        assert len(text_events) > 0, (
            f"Expected TextDelta events but got none. "
            f"All events: {[type(e).__name__ for e in events]}"
        )
        full_text = "".join(e.text for e in text_events)
        assert len(full_text) > 0
        assert len(turn_events) == 1
        assert turn_events[0].stop_reason == "end_turn"

    async def test_tool_use_loop(self) -> None:
        """Prompt that triggers a tool call and loops back."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=API_KEY)
        system_prompt = build_system_prompt(
            cwd=str(Path.cwd()),
            model="claude-sonnet-4-20250514",
        )
        system = "\n\n".join(system_prompt)

        registry = ToolRegistry()
        registry.register(BashTool())

        messages: list[Message] = [UserMessage(content="Run `echo 42` and tell me the output. Be very brief.")]
        events: list[QueryEvent] = []

        async def real_call_model(**kwargs: Any) -> Any:
            kw = {k: v for k, v in kwargs.items() if k != "max_tokens"}
            async for event in stream_response(
                client, model="claude-sonnet-4-20250514", max_tokens=1024, **kw
            ):
                yield event

        async for event in query_loop(
            messages=messages,
            system_prompt=system,
            tools=registry,
            call_model=real_call_model,
            max_turns=5,
        ):
            events.append(event)

        # W4: Check for network failure before asserting
        _check_for_connection_error(events)

        tool_starts = [e for e in events if isinstance(e, ToolUseStart)]
        tool_results = [e for e in events if isinstance(e, ToolResultReady)]
        turn_completes = [e for e in events if isinstance(e, TurnComplete)]

        assert len(tool_starts) >= 1, (
            f"Expected at least 1 ToolUseStart but got {len(tool_starts)}. "
            f"All events: {[type(e).__name__ for e in events]}"
        )
        assert tool_starts[0].tool_name == "Bash"
        assert len(tool_results) >= 1
        assert len(turn_completes) >= 2
