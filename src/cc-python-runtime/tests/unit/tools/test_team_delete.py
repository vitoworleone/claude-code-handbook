"""Tests for cc.tools.team.team_delete_tool — TeamDeleteTool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cc.swarm.identity import format_agent_id
from cc.swarm.team_file import TeamFile, TeamMember, save_team_file
from cc.tools.team.team_delete_tool import TeamDeleteTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tool() -> TeamDeleteTool:
    return TeamDeleteTool()


@pytest.mark.asyncio
async def test_delete_team(tool: TeamDeleteTool, tmp_path: Path) -> None:
    """TeamDelete removes team directory."""
    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", tmp_path):
        team = TeamFile(
            name="rm-team",
            lead_agent_id=format_agent_id("team-lead", "rm-team"),
            members=[
                TeamMember(
                    agent_id=format_agent_id("team-lead", "rm-team"),
                    name="team-lead",
                )
            ],
        )
        path = save_team_file(team, tmp_path)
        assert path.exists()

        # Also patch the _get_team_dir used by delete
        with patch("cc.tools.team.team_delete_tool._get_team_dir") as mock_dir:
            mock_dir.return_value = path.parent
            result = await tool.execute({"team_name": "rm-team"})

    assert not result.is_error
    data = json.loads(result.text)
    assert data["success"] is True
    assert "rm-team" in data["message"]


@pytest.mark.asyncio
async def test_delete_nonexistent(tool: TeamDeleteTool, tmp_path: Path) -> None:
    """TeamDelete fails for nonexistent team."""
    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", tmp_path):
        result = await tool.execute({"team_name": "ghost"})

    assert result.is_error
    data = json.loads(result.text)
    assert data["success"] is False


@pytest.mark.asyncio
async def test_delete_with_active_members(tool: TeamDeleteTool, tmp_path: Path) -> None:
    """TeamDelete fails if non-lead members are active."""
    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", tmp_path):
        team = TeamFile(
            name="busy",
            lead_agent_id="team-lead@busy",
            members=[
                TeamMember(agent_id="team-lead@busy", name="team-lead", is_active=True),
                TeamMember(agent_id="worker@busy", name="worker", is_active=True),
            ],
        )
        save_team_file(team, tmp_path)
        result = await tool.execute({"team_name": "busy"})

    assert result.is_error
    data = json.loads(result.text)
    assert "active member" in data["message"]


@pytest.mark.asyncio
async def test_delete_empty_name(tool: TeamDeleteTool) -> None:
    result = await tool.execute({"team_name": ""})
    assert result.is_error


@pytest.mark.asyncio
async def test_schema(tool: TeamDeleteTool) -> None:
    schema = tool.get_schema()
    assert schema.name == "TeamDelete"
