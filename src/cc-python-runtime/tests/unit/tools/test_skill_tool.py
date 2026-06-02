"""Tests for SkillTool (P5-6).

Verifies skill loading by name, error on not found, args appending.
"""

from __future__ import annotations

import pytest

from cc.skills.loader import Skill
from cc.tools.skill.skill_tool import SkillTool


@pytest.fixture
def sample_skills() -> list[Skill]:
    return [
        Skill(name="commit", description="Create a commit", prompt="Analyze changes and commit."),
        Skill(name="review", description="Code review", prompt="Review code for bugs."),
    ]


class TestSkillTool:
    def test_name(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        assert tool.get_name() == "Skill"

    def test_schema(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        schema = tool.get_schema()
        assert schema.name == "Skill"
        assert "skill" in schema.input_schema["properties"]
        assert "args" in schema.input_schema["properties"]
        assert schema.input_schema["required"] == ["skill"]

    def test_concurrency_safe(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        assert tool.is_concurrency_safe({}) is True

    @pytest.mark.asyncio
    async def test_execute_found(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        result = await tool.execute({"skill": "commit"})
        assert not result.is_error
        assert "Analyze changes and commit." in result.content

    @pytest.mark.asyncio
    async def test_execute_case_insensitive(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        result = await tool.execute({"skill": "Commit"})
        assert not result.is_error
        assert "Analyze changes and commit." in result.content

    @pytest.mark.asyncio
    async def test_execute_not_found(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        result = await tool.execute({"skill": "nonexistent"})
        assert result.is_error
        assert "not found" in result.content
        assert "commit" in result.content  # lists available skills

    @pytest.mark.asyncio
    async def test_execute_with_args(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        result = await tool.execute({"skill": "commit", "args": "-m 'fix bug'"})
        assert not result.is_error
        assert "Analyze changes and commit." in result.content
        assert "Arguments: -m 'fix bug'" in result.content

    @pytest.mark.asyncio
    async def test_execute_empty_skill_name(self, sample_skills: list[Skill]) -> None:
        tool = SkillTool(skills=sample_skills)
        result = await tool.execute({"skill": ""})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_execute_no_skills(self) -> None:
        tool = SkillTool(skills=[])
        result = await tool.execute({"skill": "anything"})
        assert result.is_error
        assert "(none)" in result.content
