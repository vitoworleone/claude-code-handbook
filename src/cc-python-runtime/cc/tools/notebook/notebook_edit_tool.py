"""NotebookEditTool — edit Jupyter notebook (.ipynb) files.

Corresponds to TS: tools/NotebookEditTool.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"

# 支持的三种操作类型
VALID_COMMANDS = ("insert_cell", "replace_cell", "delete_cell")
# Jupyter notebook 标准 cell 类型：代码和 Markdown
VALID_CELL_TYPES = ("code", "markdown")


def _make_cell(cell_type: str, source: str) -> dict[str, Any]:
    """Create a new notebook cell dict.

    构造符合 nbformat v4 规范的 cell 字典。
    source 按行分割并保留行尾换行符，这是 .ipynb JSON 格式的要求。
    """
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        # splitlines(keepends=True) 保留每行的换行符，
        # 这是 Jupyter notebook JSON 格式的规范要求
        "source": source.splitlines(keepends=True),
    }
    if cell_type == "code":
        # 代码 cell 需要额外的执行状态字段；
        # None 表示尚未执行，空列表表示无输出
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


class NotebookEditTool(Tool):
    """Edit Jupyter notebook (.ipynb) files.

    Corresponds to TS: tools/NotebookEditTool.
    Supports insert, replace, and delete cell operations.

    该工具直接操作 .ipynb 的 JSON 结构，无需启动 Jupyter 内核。
    三种操作覆盖了 notebook 编辑的核心场景：
    - insert_cell: 在指定位置插入新 cell（索引自动 clamp 到有效范围）
    - replace_cell: 替换指定位置的 cell 内容和类型
    - delete_cell: 删除指定位置的 cell
    """

    def get_name(self) -> str:
        return NOTEBOOK_EDIT_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=NOTEBOOK_EDIT_TOOL_NAME,
            description="Edit Jupyter notebook (.ipynb) files. Supports inserting, replacing, and deleting cells.",
            input_schema={
                "type": "object",
                "properties": {
                    "notebook_path": {
                        "type": "string",
                        "description": "Path to the .ipynb notebook file",
                    },
                    "command": {
                        "type": "string",
                        "enum": list(VALID_COMMANDS),
                        "description": "The edit operation: insert_cell, replace_cell, or delete_cell",
                    },
                    "cell_index": {
                        "type": "integer",
                        "description": "The index of the cell to operate on (0-based)",
                    },
                    "cell_type": {
                        "type": "string",
                        "enum": list(VALID_CELL_TYPES),
                        "description": "Cell type for insert/replace operations",
                    },
                    "source": {
                        "type": "string",
                        "description": "Cell source content for insert/replace operations",
                    },
                },
                # cell_type 和 source 不是全局必填——delete_cell 不需要它们
                "required": ["notebook_path", "command", "cell_index"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 该工具会写文件，并发操作同一个 notebook 可能导致数据丢失
        return False  # Writes files

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        notebook_path = tool_input.get("notebook_path", "")
        command = tool_input.get("command", "")
        cell_index = tool_input.get("cell_index")
        # cell_type 默认为 code，因为代码 cell 是最常见的操作对象
        cell_type = tool_input.get("cell_type", "code")
        source = tool_input.get("source", "")

        # --- 参数校验 ---
        if not notebook_path:
            return ToolResult(content="Error: notebook_path is required", is_error=True)
        if command not in VALID_COMMANDS:
            return ToolResult(
                content=f"Error: command must be one of {VALID_COMMANDS}",
                is_error=True,
            )
        if cell_index is None:
            return ToolResult(content="Error: cell_index is required", is_error=True)

        path = Path(notebook_path)

        # --- 加载或创建 notebook ---
        if not path.exists() and command == "insert_cell":
            # 文件不存在且是插入操作时，自动创建一个符合 nbformat v4 规范的空 notebook，
            # 避免用户需要手动先创建文件
            notebook = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python 3",
                        "language": "python",
                        "name": "python3",
                    },
                    "language_info": {"name": "python", "version": "3.12.0"},
                },
                "cells": [],
            }
        elif not path.exists():
            # replace/delete 操作要求文件必须已存在
            return ToolResult(
                content=f"Error: Notebook does not exist: {notebook_path}",
                is_error=True,
            )
        else:
            try:
                raw = path.read_text(encoding="utf-8")
                notebook = json.loads(raw)
            except json.JSONDecodeError as e:
                return ToolResult(
                    content=f"Error: Invalid notebook JSON: {e}",
                    is_error=True,
                )
            except Exception as e:
                return ToolResult(
                    content=f"Error reading notebook: {e}",
                    is_error=True,
                )

        cells: list[dict[str, Any]] = notebook.get("cells", [])  # type: ignore[assignment]

        # --- 执行 cell 操作 ---
        if command == "insert_cell":
            if cell_type not in VALID_CELL_TYPES:
                return ToolResult(
                    content=f"Error: cell_type must be one of {VALID_CELL_TYPES}",
                    is_error=True,
                )
            new_cell = _make_cell(cell_type, source)
            # 将索引 clamp 到 [0, len(cells)]，使越界索引不会报错而是插在头/尾
            idx = max(0, min(cell_index, len(cells)))
            cells.insert(idx, new_cell)

        elif command == "replace_cell":
            # replace 和 delete 操作要求索引严格在有效范围内
            if cell_index < 0 or cell_index >= len(cells):
                return ToolResult(
                    content=f"Error: cell_index {cell_index} out of range (notebook has {len(cells)} cells)",
                    is_error=True,
                )
            if cell_type not in VALID_CELL_TYPES:
                return ToolResult(
                    content=f"Error: cell_type must be one of {VALID_CELL_TYPES}",
                    is_error=True,
                )
            # 整个 cell 替换（包括 metadata），以确保 cell 结构干净
            cells[cell_index] = _make_cell(cell_type, source)

        elif command == "delete_cell":
            if cell_index < 0 or cell_index >= len(cells):
                return ToolResult(
                    content=f"Error: cell_index {cell_index} out of range (notebook has {len(cells)} cells)",
                    is_error=True,
                )
            cells.pop(cell_index)

        notebook["cells"] = cells

        # --- 写回文件 ---
        try:
            # 确保父目录存在，支持在新路径下创建 notebook
            path.parent.mkdir(parents=True, exist_ok=True)
            # indent=1 减少文件体积，ensure_ascii=False 保留 Unicode 字符
            path.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error writing notebook: {e}", is_error=True)

        return ToolResult(
            content=f"Successfully executed {command} at index {cell_index}. "
            f"Notebook now has {len(cells)} cell(s)."
        )
