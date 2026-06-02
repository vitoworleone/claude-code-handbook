"""Tests for TodoWriteTool.

Verifies todo CRUD operations and persistence.
Uses tmp_path for hermetic tests — no writes to real ~/.claude/.
"""

import json
from pathlib import Path

import pytest

from cc.tools.todo.todo_write_tool import TodoWriteTool, _get_project_hash, _get_todos_dir


class TestTodoWriteTool:
    @pytest.mark.asyncio
    async def test_write_todos(self, tmp_path: Path) -> None:
        project_dir = "/test/project"
        tool = TodoWriteTool(project_dir=project_dir, claude_dir=tmp_path)
        todos = [
            {"id": "1", "content": "Fix bug", "status": "pending"},
            {"id": "2", "content": "Write tests", "status": "in_progress"},
            {"id": "3", "content": "Deploy", "status": "completed"},
        ]

        result = await tool.execute({"todos": todos})
        assert not result.is_error
        assert "3 todo(s)" in result.content
        assert "1 pending" in result.content
        assert "1 in progress" in result.content
        assert "1 completed" in result.content

        # Verify the file was written in tmp_path
        todos_dir = _get_todos_dir(tmp_path)
        project_hash = _get_project_hash(project_dir)
        file_path = todos_dir / f"{project_hash}.json"
        assert file_path.exists()

        written = json.loads(file_path.read_text())
        assert len(written) == 3
        assert written[0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_write_empty_todos(self, tmp_path: Path) -> None:
        tool = TodoWriteTool(project_dir="/test", claude_dir=tmp_path)
        result = await tool.execute({"todos": []})
        assert not result.is_error
        assert "0 todo(s)" in result.content

    @pytest.mark.asyncio
    async def test_missing_todos_field(self, tmp_path: Path) -> None:
        tool = TodoWriteTool(claude_dir=tmp_path)
        result = await tool.execute({})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_invalid_todos_type(self, tmp_path: Path) -> None:
        tool = TodoWriteTool(claude_dir=tmp_path)
        result = await tool.execute({"todos": "not a list"})
        assert result.is_error
        assert "array" in result.content

    @pytest.mark.asyncio
    async def test_invalid_status(self, tmp_path: Path) -> None:
        tool = TodoWriteTool(claude_dir=tmp_path)
        result = await tool.execute({
            "todos": [{"id": "1", "content": "Bad", "status": "invalid"}],
        })
        assert result.is_error
        assert "invalid status" in result.content

    @pytest.mark.asyncio
    async def test_missing_required_field(self, tmp_path: Path) -> None:
        tool = TodoWriteTool(claude_dir=tmp_path)
        result = await tool.execute({
            "todos": [{"id": "1", "content": "No status"}],
        })
        assert result.is_error
        assert "missing required field" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        tool = TodoWriteTool()
        assert tool.get_name() == "TodoWrite"
        schema = tool.get_schema()
        assert schema.name == "TodoWrite"
        assert "todos" in schema.input_schema["properties"]
        assert "todos" in schema.input_schema["required"]
