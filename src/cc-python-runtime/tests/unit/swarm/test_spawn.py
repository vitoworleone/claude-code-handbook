"""Tests for cc.swarm.spawn — teammate spawning with real execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from cc.core.events import TextDelta, TurnComplete
from cc.models.messages import Usage
from cc.swarm.identity import format_agent_id
from cc.swarm.spawn import get_running_tasks, spawn_teammate
from cc.swarm.team_file import TeamFile, TeamMember, load_team_file, save_team_file
from cc.tools.base import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path


def _mock_factory(model: str | None = None, max_tokens: int = 16384) -> Any:
    """Mock call_model factory for testing."""
    async def call_model(**kwargs: Any) -> Any:
        yield TextDelta(text="Done.")
        yield TurnComplete(stop_reason="end_turn", usage=Usage())
    return call_model


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    team = TeamFile(
        name="spawn-test",
        lead_agent_id=format_agent_id("team-lead", "spawn-test"),
        members=[
            TeamMember(
                agent_id=format_agent_id("team-lead", "spawn-test"),
                name="team-lead",
            )
        ],
    )
    save_team_file(team, tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_spawn_returns_task_id(claude_dir: Path) -> None:
    from unittest.mock import patch

    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", claude_dir):
        task_id = await spawn_teammate(
            team_name="spawn-test",
            agent_name="worker",
            prompt="research something",
            call_model_factory=_mock_factory,
            parent_registry=ToolRegistry(),
            claude_dir=claude_dir,
        )

    assert task_id.startswith("teammate-")
    tasks = get_running_tasks()
    if task_id in tasks:
        await tasks[task_id]


@pytest.mark.asyncio
async def test_spawn_registers_member(claude_dir: Path) -> None:
    from unittest.mock import patch

    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", claude_dir):
        task_id = await spawn_teammate(
            team_name="spawn-test",
            agent_name="researcher",
            prompt="find bugs",
            call_model_factory=_mock_factory,
            parent_registry=ToolRegistry(),
            claude_dir=claude_dir,
        )

    tasks = get_running_tasks()
    if task_id in tasks:
        await tasks[task_id]

    team = load_team_file("spawn-test", claude_dir)
    assert team is not None
    names = [m.name for m in team.members]
    assert "researcher" in names


@pytest.mark.asyncio
async def test_spawn_teammate_sends_message(claude_dir: Path) -> None:
    from unittest.mock import patch

    from cc.swarm.mailbox import TeammateMailbox

    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", claude_dir):
        task_id = await spawn_teammate(
            team_name="spawn-test",
            agent_name="notifier",
            prompt="check stuff",
            call_model_factory=_mock_factory,
            parent_registry=ToolRegistry(),
            claude_dir=claude_dir,
        )

    tasks = get_running_tasks()
    if task_id in tasks:
        await tasks[task_id]

    mb = TeammateMailbox("spawn-test", claude_dir=claude_dir)
    msgs = mb.receive("team-lead")
    assert len(msgs) >= 1
    assert any("notifier" in m.from_name for m in msgs)


@pytest.mark.asyncio
async def test_spawn_with_task_registry(claude_dir: Path) -> None:
    """Spawn should register in TaskRegistry when provided."""
    from unittest.mock import patch

    from cc.session.task_registry import TaskRegistry

    reg = TaskRegistry()

    with patch("cc.swarm.team_file._DEFAULT_CLAUDE_DIR", claude_dir):
        task_id = await spawn_teammate(
            team_name="spawn-test",
            agent_name="tracked",
            prompt="tracked task",
            call_model_factory=_mock_factory,
            parent_registry=ToolRegistry(),
            claude_dir=claude_dir,
            task_registry=reg,
        )

    # Wait for completion
    tasks = get_running_tasks()
    if task_id in tasks:
        await tasks[task_id]
    await asyncio.sleep(0.05)  # Let callback fire

    # Should be registered
    all_tasks = reg.list_all()
    assert len(all_tasks) >= 1
    teammate_tasks = [t for t in all_tasks if t.task_type == "in_process_teammate"]
    assert len(teammate_tasks) >= 1
