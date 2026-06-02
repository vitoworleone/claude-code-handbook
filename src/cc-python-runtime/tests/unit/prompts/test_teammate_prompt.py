"""Tests for cc.prompts.teammate_prompt — teammate prompt addendum."""

from __future__ import annotations

from cc.prompts.teammate_prompt import (
    TEAMMATE_SYSTEM_PROMPT_ADDENDUM,
    build_teammate_prompt_addendum,
)


class TestTeammateSystemPromptAddendum:
    def test_contains_send_message(self) -> None:
        """Addendum must mention SendMessage tool."""
        assert "SendMessage" in TEAMMATE_SYSTEM_PROMPT_ADDENDUM

    def test_contains_broadcast(self) -> None:
        """Addendum must mention broadcast."""
        assert '"*"' in TEAMMATE_SYSTEM_PROMPT_ADDENDUM

    def test_contains_team_lead(self) -> None:
        assert "team lead" in TEAMMATE_SYSTEM_PROMPT_ADDENDUM.lower()


class TestBuildTeammatePromptAddendum:
    def test_includes_identity(self) -> None:
        prompt = build_teammate_prompt_addendum("alpha", "researcher")
        assert "researcher" in prompt
        assert "alpha" in prompt
        assert "researcher@alpha" in prompt

    def test_includes_team_config_path(self) -> None:
        prompt = build_teammate_prompt_addendum("my-proj", "worker")
        assert "~/.claude/teams/my-proj/config.json" in prompt

    def test_includes_base_addendum(self) -> None:
        prompt = build_teammate_prompt_addendum("t", "a")
        assert "SendMessage" in prompt
        assert "IMPORTANT" in prompt

    def test_includes_task_lifecycle(self) -> None:
        prompt = build_teammate_prompt_addendum("t", "a")
        assert "Task Lifecycle" in prompt

    def test_includes_idle_note(self) -> None:
        prompt = build_teammate_prompt_addendum("t", "a")
        assert "idle" in prompt.lower()
