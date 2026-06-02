"""ToolSearchTool — search registered tools by name and description.

Corresponds to TS: tools/ToolSearchTool.

该工具实现了工具的"自省"能力：当模型不确定应该使用哪个工具时，
可以通过关键词搜索来发现可用工具。这在延迟加载（deferred tools）
场景下尤其重要——模型只知道工具名称，需要搜索才能获取完整定义。
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolRegistry, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

TOOL_SEARCH_TOOL_NAME = "ToolSearch"


class ToolSearchTool(Tool):
    """Search registered tools by name and description.

    Corresponds to TS: tools/ToolSearchTool.
    Accepts a ToolRegistry in the constructor to search across all registered tools.
    使用基于关键词匹配的简单评分机制，名称匹配权重高于描述匹配，
    精确名称匹配给予额外加分，最终按分数降序排列。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        # 注入工具注册表，搜索范围覆盖所有已注册的工具
        self._registry = registry

    def get_name(self) -> str:
        return TOOL_SEARCH_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=TOOL_SEARCH_TOOL_NAME,
            description="Search registered tools by name and description.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to match against tool names and descriptions",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 只读取注册表信息，不修改任何状态
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        query = tool_input.get("query", "")
        max_results = int(tool_input.get("max_results", 5))

        if not query:
            return ToolResult(content="Error: query is required", is_error=True)

        # 将查询拆分为独立的关键词，支持多词搜索（如 "web fetch"）
        query_lower = query.lower()
        query_terms = query_lower.split()

        # 使用 (score, name, description) 三元组存储搜索结果
        results: list[tuple[int, str, str]] = []
        for tool in self._registry.list_tools():
            schema = tool.get_schema()
            name = schema.name
            description = schema.description
            name_lower = name.lower()
            desc_lower = description.lower()

            # 评分策略：名称匹配权重（10）高于描述匹配（5），
            # 因为用户通常记得工具名称的关键字
            score = 0
            for term in query_terms:
                if term in name_lower:
                    score += 10  # Name match is weighted higher
                if term in desc_lower:
                    score += 5

            # 完全匹配工具名时给予大额加分（50），确保精确搜索排在最前
            if query_lower == name_lower:
                score += 50

            if score > 0:
                results.append((score, name, description))

        # 按分数降序排列，高相关性的工具排在前面
        results.sort(key=lambda x: x[0], reverse=True)
        results = results[:max_results]

        if not results:
            return ToolResult(content=f"No tools found matching '{query}'")

        lines = []
        for _score, name, description in results:
            lines.append(f"- {name}: {description}")

        return ToolResult(content=f"Found {len(results)} tool(s):\n" + "\n".join(lines))
