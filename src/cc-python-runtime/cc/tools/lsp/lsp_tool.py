"""LSPTool — Language Server Protocol integration stub.

P5-9: Provides LSP actions (diagnostics, hover, definition, references).
Currently a stub that requires a running language server configuration.

该工具是 LSP 集成的占位实现，预留了完整的接口定义。
后续阶段将接入实际的语言服务器（如 pyright、clangd 等），
届时可通过 .claude/settings.json 配置语言服务器的启动命令和参数。
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

LSP_TOOL_NAME = "LSP"


class LSPTool(Tool):
    """Language Server Protocol integration.

    Provides IDE-like features: diagnostics, hover info, go-to-definition,
    and find-references. Currently a stub requiring language server config.

    四种 LSP 操作对应的典型使用场景：
    - diagnostics: 获取文件的编译错误和警告，用于自动修复
    - hover: 查看符号的类型信息和文档，帮助理解代码
    - definition: 跳转到符号定义处，追踪代码引用链
    - references: 查找符号的所有使用处，评估修改影响范围
    """

    def get_name(self) -> str:
        return LSP_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=LSP_TOOL_NAME,
            description=(
                "Query a Language Server for code intelligence: "
                "diagnostics, hover information, go-to-definition, and find-references."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["diagnostics", "hover", "definition", "references"],
                        "description": "The LSP action to perform",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number (0-based)",
                    },
                    "character": {
                        "type": "integer",
                        "description": "Character offset in the line (0-based)",
                    },
                },
                # character 非必填，因为 diagnostics 操作只需要文件路径和行号
                "required": ["action", "file_path", "line"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # LSP 查询为只读操作，不修改文件，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        action = tool_input.get("action", "")
        if action not in ("diagnostics", "hover", "definition", "references"):
            return ToolResult(
                content=f"Error: invalid action '{action}'. "
                "Must be one of: diagnostics, hover, definition, references",
                is_error=True,
            )

        file_path = tool_input.get("file_path", "")
        if not file_path:
            return ToolResult(content="Error: file_path is required", is_error=True)

        # 当前为 stub 实现——返回配置提示，引导用户设置语言服务器
        return ToolResult(
            content=(
                "LSP requires a running language server. "
                "Configure in .claude/settings.json"
            ),
            is_error=True,
        )
