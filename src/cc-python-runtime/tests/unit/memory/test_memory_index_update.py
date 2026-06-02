"""Tests for P3a: Memory MEMORY.md index auto-update."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc.memory.extractor import _extract_description
from cc.memory.session_memory import (
    get_memory_dir,
    load_memory_index,
    update_memory_index,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestUpdateMemoryIndex:
    def test_creates_index_if_missing(self, tmp_path: Path) -> None:
        update_memory_index("/test", "user_role", "User is a data scientist", claude_dir=tmp_path)
        mem_dir = get_memory_dir("/test", tmp_path)
        index = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "[user_role]" in index
        assert "data scientist" in index

    def test_appends_to_existing_index(self, tmp_path: Path) -> None:
        update_memory_index("/test", "first", "First memory", claude_dir=tmp_path)
        update_memory_index("/test", "second", "Second memory", claude_dir=tmp_path)
        index = load_memory_index("/test", tmp_path)
        assert index is not None
        assert "[first]" in index
        assert "[second]" in index

    def test_updates_existing_entry(self, tmp_path: Path) -> None:
        update_memory_index("/test", "role", "Old description", claude_dir=tmp_path)
        update_memory_index("/test", "role", "New description", claude_dir=tmp_path)
        index = load_memory_index("/test", tmp_path)
        assert index is not None
        assert "New description" in index
        assert "Old description" not in index
        # Should not duplicate
        assert index.count("[role]") == 1

    def test_no_duplicate_on_repeat(self, tmp_path: Path) -> None:
        for _ in range(3):
            update_memory_index("/test", "dup", "Same", claude_dir=tmp_path)
        index = load_memory_index("/test", tmp_path)
        assert index is not None
        assert index.count("[dup]") == 1

    def test_index_loaded_in_prompt(self, tmp_path: Path) -> None:
        """After update, load_memory_index returns the content."""
        update_memory_index("/test", "mem1", "Description one", claude_dir=tmp_path)
        content = load_memory_index("/test", tmp_path)
        assert content is not None
        assert "mem1" in content


class TestExtractDescription:
    def test_extracts_from_frontmatter(self) -> None:
        content = """---
name: user_role
description: User is a senior engineer
type: user
---

User works on backend systems."""
        assert _extract_description(content) == "User is a senior engineer"

    def test_returns_none_without_frontmatter(self) -> None:
        assert _extract_description("Just plain text") is None

    def test_returns_none_without_description_field(self) -> None:
        content = """---
name: test
type: project
---
Content here."""
        assert _extract_description(content) is None
