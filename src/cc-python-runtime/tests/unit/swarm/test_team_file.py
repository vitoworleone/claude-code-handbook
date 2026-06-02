"""Tests for cc.swarm.team_file — team configuration management."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from cc.swarm.identity import format_agent_id
from cc.swarm.team_file import (
    TeamFile,
    TeamMember,
    add_member,
    load_team_file,
    remove_member,
    save_team_file,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    return tmp_path


class TestTeamMember:
    def test_roundtrip(self) -> None:
        m = TeamMember(
            agent_id="worker@team",
            name="worker",
            agent_type="researcher",
            model="claude-sonnet-4-20250514",
            joined_at=1000.0,
            cwd="/tmp",
            is_active=True,
        )
        d = m.to_dict()
        restored = TeamMember.from_dict(d)
        assert restored.agent_id == "worker@team"
        assert restored.name == "worker"
        assert restored.agent_type == "researcher"
        assert restored.model == "claude-sonnet-4-20250514"
        assert restored.joined_at == 1000.0
        assert restored.cwd == "/tmp"
        assert restored.is_active is True


class TestTeamFile:
    def test_roundtrip(self) -> None:
        tf = TeamFile(
            name="alpha",
            description="test team",
            created_at=1000.0,
            lead_agent_id="team-lead@alpha",
            members=[
                TeamMember(
                    agent_id="team-lead@alpha",
                    name="team-lead",
                    joined_at=1000.0,
                )
            ],
        )
        d = tf.to_dict()
        restored = TeamFile.from_dict(d)
        assert restored.name == "alpha"
        assert restored.description == "test team"
        assert restored.lead_agent_id == "team-lead@alpha"
        assert len(restored.members) == 1
        assert restored.members[0].name == "team-lead"


class TestSaveAndLoad:
    def test_save_and_load(self, claude_dir: Path) -> None:
        team = TeamFile(
            name="my-team",
            description="test",
            created_at=time.time(),
            lead_agent_id=format_agent_id("team-lead", "my-team"),
            members=[
                TeamMember(
                    agent_id=format_agent_id("team-lead", "my-team"),
                    name="team-lead",
                    joined_at=time.time(),
                )
            ],
        )
        path = save_team_file(team, claude_dir)
        assert path.exists()

        loaded = load_team_file("my-team", claude_dir)
        assert loaded is not None
        assert loaded.name == "my-team"
        assert loaded.description == "test"
        assert len(loaded.members) == 1

    def test_load_nonexistent(self, claude_dir: Path) -> None:
        assert load_team_file("nope", claude_dir) is None


class TestAddMember:
    def test_add_member(self, claude_dir: Path) -> None:
        team = TeamFile(
            name="proj",
            created_at=time.time(),
            lead_agent_id=format_agent_id("team-lead", "proj"),
            members=[
                TeamMember(
                    agent_id=format_agent_id("team-lead", "proj"),
                    name="team-lead",
                    joined_at=time.time(),
                )
            ],
        )
        save_team_file(team, claude_dir)

        new_member = TeamMember(
            agent_id=format_agent_id("worker", "proj"),
            name="worker",
            agent_type="researcher",
            joined_at=time.time(),
        )
        add_member("proj", new_member, claude_dir)

        loaded = load_team_file("proj", claude_dir)
        assert loaded is not None
        assert len(loaded.members) == 2
        assert loaded.members[1].name == "worker"

    def test_add_member_no_team_raises(self, claude_dir: Path) -> None:
        m = TeamMember(agent_id="x@y", name="x")
        with pytest.raises(ValueError, match="does not exist"):
            add_member("nonexistent", m, claude_dir)

    def test_add_duplicate_is_noop(self, claude_dir: Path) -> None:
        team = TeamFile(
            name="dup",
            members=[TeamMember(agent_id="a@dup", name="a")],
        )
        save_team_file(team, claude_dir)
        add_member("dup", TeamMember(agent_id="a@dup", name="a"), claude_dir)
        loaded = load_team_file("dup", claude_dir)
        assert loaded is not None
        assert len(loaded.members) == 1


class TestRemoveMember:
    def test_remove_member(self, claude_dir: Path) -> None:
        team = TeamFile(
            name="rm",
            members=[
                TeamMember(agent_id="lead@rm", name="team-lead"),
                TeamMember(agent_id="worker@rm", name="worker"),
            ],
        )
        save_team_file(team, claude_dir)
        remove_member("rm", "worker", claude_dir)

        loaded = load_team_file("rm", claude_dir)
        assert loaded is not None
        assert len(loaded.members) == 1
        assert loaded.members[0].name == "team-lead"

    def test_remove_nonexistent_member(self, claude_dir: Path) -> None:
        team = TeamFile(name="rm2", members=[TeamMember(agent_id="a@rm2", name="a")])
        save_team_file(team, claude_dir)
        # Should not raise, just log warning
        remove_member("rm2", "nonexistent", claude_dir)
        loaded = load_team_file("rm2", claude_dir)
        assert loaded is not None
        assert len(loaded.members) == 1

    def test_remove_no_team_raises(self, claude_dir: Path) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            remove_member("ghost", "x", claude_dir)
