"""Tests for P2b: Permission rules system."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from cc.permissions.gate import PermissionContext, PermissionDecision, PermissionMode
from cc.permissions.rules import (
    PermissionRules,
    apply_rules,
    load_permission_rules,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadPermissionRules:
    def test_load_from_settings(self, tmp_path: Path) -> None:
        settings = {
            "permissions": {
                "allow": ["Edit", "Bash:git*"],
                "deny": ["Agent"],
            }
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings))
        rules = load_permission_rules(tmp_path)
        assert rules.allow == ["Edit", "Bash:git*"]
        assert rules.deny == ["Agent"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        rules = load_permission_rules(tmp_path / "nonexistent")
        assert rules.allow == []
        assert rules.deny == []

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text("{bad json")
        rules = load_permission_rules(tmp_path)
        assert rules.allow == []
        assert rules.deny == []

    def test_no_permissions_key(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text(json.dumps({"other": "stuff"}))
        rules = load_permission_rules(tmp_path)
        assert rules.allow == []
        assert rules.deny == []

    def test_invalid_permissions_type(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text(json.dumps({"permissions": "invalid"}))
        rules = load_permission_rules(tmp_path)
        assert rules.allow == []
        assert rules.deny == []

    def test_invalid_allow_type(self, tmp_path: Path) -> None:
        settings = {"permissions": {"allow": "not-a-list", "deny": ["Agent"]}}
        (tmp_path / "settings.json").write_text(json.dumps(settings))
        rules = load_permission_rules(tmp_path)
        assert rules.allow == []
        assert rules.deny == ["Agent"]


class TestApplyRules:
    def test_allow_rule_exact_match(self) -> None:
        rules = PermissionRules(allow=["Edit"], deny=[])
        result = apply_rules(rules, "Edit")
        assert result == PermissionDecision.ALLOW

    def test_deny_rule_exact_match(self) -> None:
        rules = PermissionRules(allow=[], deny=["Agent"])
        result = apply_rules(rules, "Agent")
        assert result == PermissionDecision.DENY

    def test_no_match_returns_none(self) -> None:
        rules = PermissionRules(allow=["Edit"], deny=["Agent"])
        result = apply_rules(rules, "Bash", {"command": "ls"})
        assert result is None

    def test_deny_takes_precedence_over_allow(self) -> None:
        rules = PermissionRules(allow=["Agent"], deny=["Agent"])
        result = apply_rules(rules, "Agent")
        assert result == PermissionDecision.DENY

    def test_bash_glob_allow(self) -> None:
        rules = PermissionRules(allow=["Bash:git*"], deny=[])
        result = apply_rules(rules, "Bash", {"command": "git status"})
        assert result == PermissionDecision.ALLOW

    def test_bash_glob_no_match(self) -> None:
        rules = PermissionRules(allow=["Bash:git*"], deny=[])
        result = apply_rules(rules, "Bash", {"command": "rm -rf /"})
        assert result is None

    def test_bash_glob_deny(self) -> None:
        rules = PermissionRules(allow=[], deny=["Bash:rm*"])
        result = apply_rules(rules, "Bash", {"command": "rm -rf /"})
        assert result == PermissionDecision.DENY

    def test_bash_glob_with_no_input(self) -> None:
        rules = PermissionRules(allow=["Bash:git*"], deny=[])
        result = apply_rules(rules, "Bash", None)
        assert result is None

    def test_empty_rules_returns_none(self) -> None:
        rules = PermissionRules()
        result = apply_rules(rules, "Bash", {"command": "ls"})
        assert result is None


class TestPermissionContextWithRules:
    @pytest.mark.asyncio
    async def test_rules_allow_overrides_mode_ask(self) -> None:
        """A rule that allows Bash should bypass mode-based ASK."""
        rules = PermissionRules(allow=["Bash:git*"], deny=[])
        ctx = PermissionContext(
            mode=PermissionMode.DEFAULT,
            is_interactive=False,
            rules=rules,
        )
        # Without rules, Bash would be ASK -> denied (non-interactive)
        assert await ctx.check("Bash", {"command": "git status"}) is True

    @pytest.mark.asyncio
    async def test_rules_deny_overrides_mode_allow(self) -> None:
        """A deny rule should block even normally allowed tools."""
        rules = PermissionRules(allow=[], deny=["Read"])
        ctx = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            is_interactive=True,
            rules=rules,
        )
        # Read is normally auto-allowed, but deny rule blocks it
        assert await ctx.check("Read", {"file_path": "/tmp/x"}) is False

    @pytest.mark.asyncio
    async def test_rules_none_falls_through(self) -> None:
        """When rules is None, behavior matches mode-only check."""
        ctx = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            is_interactive=False,
            rules=None,
        )
        assert await ctx.check("Read", {"file_path": "/tmp/x"}) is True
        assert await ctx.check("Bash", {"command": "ls"}) is False

    @pytest.mark.asyncio
    async def test_rules_no_match_falls_through_to_mode(self) -> None:
        """When no rule matches, mode-based check is used."""
        rules = PermissionRules(allow=["Bash:git*"], deny=[])
        ctx = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            is_interactive=False,
            rules=rules,
        )
        # "ls" doesn't match "git*", so falls through to mode check -> ASK -> denied
        assert await ctx.check("Bash", {"command": "ls"}) is False
