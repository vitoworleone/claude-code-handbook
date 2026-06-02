"""Tests for NotebookEditTool.

Verifies insert, replace, and delete cell operations.
"""

import json
from pathlib import Path

import pytest

from cc.tools.notebook.notebook_edit_tool import NotebookEditTool


def _make_notebook(path: Path, cells: list[dict] | None = None) -> None:
    """Create a minimal notebook file for testing."""
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": cells or [],
    }
    path.write_text(json.dumps(notebook), encoding="utf-8")


def _read_notebook(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestNotebookEditTool:
    @pytest.mark.asyncio
    async def test_insert_cell_into_new_notebook(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = str(tmp_path / "test.ipynb")

        result = await tool.execute({
            "notebook_path": nb_path,
            "command": "insert_cell",
            "cell_index": 0,
            "cell_type": "code",
            "source": "print('hello')",
        })
        assert not result.is_error
        assert "1 cell(s)" in result.content

        nb = _read_notebook(Path(nb_path))
        assert len(nb["cells"]) == 1
        assert nb["cells"][0]["cell_type"] == "code"
        assert "print('hello')" in "".join(nb["cells"][0]["source"])

    @pytest.mark.asyncio
    async def test_insert_cell_into_existing_notebook(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path, cells=[
            {"cell_type": "code", "metadata": {}, "source": ["x = 1\n"], "execution_count": None, "outputs": []},
        ])

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "insert_cell",
            "cell_index": 1,
            "cell_type": "markdown",
            "source": "# Title",
        })
        assert not result.is_error
        assert "2 cell(s)" in result.content

        nb = _read_notebook(nb_path)
        assert nb["cells"][1]["cell_type"] == "markdown"

    @pytest.mark.asyncio
    async def test_replace_cell(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path, cells=[
            {"cell_type": "code", "metadata": {}, "source": ["old code\n"], "execution_count": None, "outputs": []},
        ])

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "replace_cell",
            "cell_index": 0,
            "cell_type": "code",
            "source": "new code",
        })
        assert not result.is_error

        nb = _read_notebook(nb_path)
        assert "new code" in "".join(nb["cells"][0]["source"])

    @pytest.mark.asyncio
    async def test_delete_cell(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path, cells=[
            {"cell_type": "code", "metadata": {}, "source": ["a\n"], "execution_count": None, "outputs": []},
            {"cell_type": "code", "metadata": {}, "source": ["b\n"], "execution_count": None, "outputs": []},
        ])

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "delete_cell",
            "cell_index": 0,
        })
        assert not result.is_error
        assert "1 cell(s)" in result.content

        nb = _read_notebook(nb_path)
        assert len(nb["cells"]) == 1
        assert "b" in "".join(nb["cells"][0]["source"])

    @pytest.mark.asyncio
    async def test_replace_out_of_range(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path, cells=[])

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "replace_cell",
            "cell_index": 0,
            "cell_type": "code",
            "source": "x",
        })
        assert result.is_error
        assert "out of range" in result.content

    @pytest.mark.asyncio
    async def test_delete_out_of_range(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path, cells=[])

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "delete_cell",
            "cell_index": 0,
        })
        assert result.is_error
        assert "out of range" in result.content

    @pytest.mark.asyncio
    async def test_invalid_command(self, tmp_path: Path) -> None:
        tool = NotebookEditTool()
        nb_path = tmp_path / "test.ipynb"
        _make_notebook(nb_path)

        result = await tool.execute({
            "notebook_path": str(nb_path),
            "command": "bad_command",
            "cell_index": 0,
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_missing_notebook_path(self) -> None:
        tool = NotebookEditTool()
        result = await tool.execute({
            "notebook_path": "",
            "command": "insert_cell",
            "cell_index": 0,
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_nonexistent_notebook_for_replace(self) -> None:
        tool = NotebookEditTool()
        result = await tool.execute({
            "notebook_path": "/tmp/nonexistent_notebook.ipynb",
            "command": "replace_cell",
            "cell_index": 0,
        })
        assert result.is_error
        assert "does not exist" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        tool = NotebookEditTool()
        assert tool.get_name() == "NotebookEdit"
        schema = tool.get_schema()
        assert schema.name == "NotebookEdit"
        assert "notebook_path" in schema.input_schema["properties"]
        assert "command" in schema.input_schema["properties"]
        assert "cell_index" in schema.input_schema["properties"]

    @pytest.mark.asyncio
    async def test_not_concurrency_safe(self) -> None:
        tool = NotebookEditTool()
        assert tool.is_concurrency_safe({}) is False
