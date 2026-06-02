"""Tests for cc.swarm.coordinator — coordinator mode detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc.swarm.coordinator import get_coordinator_config, is_coordinator_mode

if TYPE_CHECKING:
    import pytest


class TestIsCoordinatorMode:
    def test_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
        assert is_coordinator_mode() is False

    def test_set_to_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
        assert is_coordinator_mode() is True

    def test_set_to_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "true")
        assert is_coordinator_mode() is True

    def test_set_to_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "yes")
        assert is_coordinator_mode() is True

    def test_set_to_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "0")
        assert is_coordinator_mode() is False

    def test_set_to_random(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "nah")
        assert is_coordinator_mode() is False


class TestGetCoordinatorConfig:
    def test_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
        config = get_coordinator_config()
        assert config["enabled"] is False
        assert config["env_var"] == "CLAUDE_CODE_COORDINATOR_MODE"

    def test_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
        config = get_coordinator_config()
        assert config["enabled"] is True
