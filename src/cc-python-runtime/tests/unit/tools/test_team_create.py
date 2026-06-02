"""Tests for cc.tools.team.team_create_tool — TeamCreateTool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cc.tools.team.team_create_tool import TeamCreateTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tool() -> TeamCreateTool:
    return TeamCreateTool()


@pytest.mark.asyncio
async def test_create_team(tool: TeamCreateTool, tmp_path: Path) -> None:
    """TeamCreate creates a team file and returns team info."""
    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", tmp_path):
        result = await tool.execute({"team_name": "alpha", "description": "test team"})

    assert not result.is_error
    data = json.loads(result.text)
    assert data["team_name"] == "alpha"
    assert data["lead_agent_id"] == "team-lead@alpha"
    assert "team_file_path" in data


@pytest.mark.asyncio
async def test_create_duplicate_team(tool: TeamCreateTool, tmp_path: Path) -> None:
    """TeamCreate fails if team already exists."""
    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", tmp_path):
        await tool.execute({"team_name": "dup"})
        result = await tool.execute({"team_name": "dup"})

    assert result.is_error
    assert "already exists" in result.text


@pytest.mark.asyncio
async def test_create_empty_name(tool: TeamCreateTool) -> None:
    """TeamCreate fails with empty team name."""
    result = await tool.execute({"team_name": ""})
    assert result.is_error
    assert "required" in result.text


@pytest.mark.asyncio
async def test_schema(tool: TeamCreateTool) -> None:
    schema = tool.get_schema()
    assert schema.name == "TeamCreate"
    assert "team_name" in schema.input_schema["properties"]
