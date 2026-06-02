"""Tests for cc.swarm.identity — agent ID parsing and formatting."""

from __future__ import annotations

import pytest

from cc.swarm.identity import (
    TEAM_LEAD_NAME,
    AgentRoute,
    format_agent_id,
    parse_agent_id,
    sanitize_name,
)


class TestFormatAgentId:
    def test_basic(self) -> None:
        assert format_agent_id("researcher", "my-team") == "researcher@my-team"

    def test_team_lead(self) -> None:
        assert format_agent_id(TEAM_LEAD_NAME, "alpha") == "team-lead@alpha"


class TestParseAgentId:
    def test_basic(self) -> None:
        route = parse_agent_id("researcher@my-team")
        assert route == AgentRoute(agent_name="researcher", team_name="my-team")

    def test_team_lead(self) -> None:
        route = parse_agent_id("team-lead@alpha")
        assert route.agent_name == "team-lead"
        assert route.team_name == "alpha"

    def test_no_at_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid agent_id"):
            parse_agent_id("no-at-sign")

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            parse_agent_id("@team")

    def test_empty_team_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            parse_agent_id("name@")

    def test_roundtrip(self) -> None:
        original = format_agent_id("worker", "proj")
        route = parse_agent_id(original)
        assert route.agent_name == "worker"
        assert route.team_name == "proj"


class TestSanitizeName:
    def test_basic(self) -> None:
        assert sanitize_name("My Team") == "my-team"

    def test_special_chars(self) -> None:
        assert sanitize_name("hello@world!") == "hello-world-"

    def test_already_clean(self) -> None:
        assert sanitize_name("clean-name") == "clean-name"
