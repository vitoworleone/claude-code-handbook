"""Tests for cc.tools.send_message.send_message_tool — SendMessageTool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cc.swarm.identity import format_agent_id
from cc.swarm.mailbox import TeammateMailbox
from cc.swarm.team_file import TeamFile, TeamMember, save_team_file
from cc.tools.send_message.send_message_tool import SendMessageTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def team_dir(tmp_path: Path) -> Path:
    """Set up a team and return the claude dir."""
    team = TeamFile(
        name="test-team",
        lead_agent_id=format_agent_id("team-lead", "test-team"),
        members=[
            TeamMember(agent_id="team-lead@test-team", name="team-lead"),
            TeamMember(agent_id="worker@test-team", name="worker"),
            TeamMember(agent_id="researcher@test-team", name="researcher"),
        ],
    )
    save_team_file(team, tmp_path)
    return tmp_path


@pytest.fixture
def tool(team_dir: Path) -> SendMessageTool:
    return SendMessageTool(team_name="test-team", sender_name="team-lead")


@pytest.mark.asyncio
async def test_send_message(tool: SendMessageTool, team_dir: Path) -> None:
    """Send a point-to-point message."""
    with patch("cc.swarm.mailbox._DEFAULT_CLAUDE_DIR", team_dir):
        result = await tool.execute(
            {"to": "worker", "text": "do research", "summary": "research task"}
        )

    assert not result.is_error
    data = json.loads(result.text)
    assert data["success"] is True
    assert "worker" in data["message"]

    # Verify mailbox
    with patch("cc.swarm.mailbox._DEFAULT_CLAUDE_DIR", team_dir):
        mb = TeammateMailbox("test-team", claude_dir=team_dir)
        msgs = mb.receive("worker")
        assert len(msgs) == 1
        assert msgs[0].text == "do research"
        assert msgs[0].from_name == "team-lead"


@pytest.mark.asyncio
async def test_broadcast(tool: SendMessageTool, team_dir: Path) -> None:
    """Broadcast sends to all non-sender members."""
    with (
        patch("cc.swarm.mailbox._DEFAULT_CLAUDE_DIR", team_dir),
        patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", team_dir),
    ):
        result = await tool.execute(
            {"to": "*", "text": "team update", "summary": "update"}
        )

    assert not result.is_error
    data = json.loads(result.text)
    assert data["success"] is True
    assert len(data["recipients"]) == 2
    assert "worker" in data["recipients"]
    assert "researcher" in data["recipients"]


@pytest.mark.asyncio
async def test_send_empty_to() -> None:
    tool = SendMessageTool(team_name="t")
    result = await tool.execute({"to": "", "text": "hi"})
    assert result.is_error
    assert "empty" in result.text


@pytest.mark.asyncio
async def test_send_no_team() -> None:
    tool = SendMessageTool(team_name="", sender_name="x")
    result = await tool.execute({"to": "y", "text": "hi"})
    assert result.is_error
    assert "team" in result.text.lower()


@pytest.mark.asyncio
async def test_schema() -> None:
    tool = SendMessageTool()
    schema = tool.get_schema()
    assert schema.name == "SendMessage"
    assert "to" in schema.input_schema["properties"]
    assert "text" in schema.input_schema["properties"]
