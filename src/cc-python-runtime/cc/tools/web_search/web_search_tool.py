"""WebSearchTool — web search stub.

Corresponds to TS: tools/WebSearchTool.

该工具是网络搜索功能的占位实现。需要配置搜索 API 密钥后才能使用。
支持的搜索后端：Brave Search API（BRAVE_API_KEY）或
SerpAPI（SERPAPI_KEY），优先使用已配置的那个。
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

WEB_SEARCH_TOOL_NAME = "WebSearch"


class WebSearchTool(Tool):
    """Search the web for information.

    Corresponds to TS: tools/WebSearchTool.
    Currently a stub that requires API key configuration.
    后续实现将调用搜索 API 并返回结构化的搜索结果列表。
    """

    def get_name(self) -> str:
        return WEB_SEARCH_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=WEB_SEARCH_TOOL_NAME,
            description="Search the web for information using a search query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
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
        # 搜索请求为只读外部 API 调用，不修改本地状态，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        query = tool_input.get("query", "")
        if not query:
            return ToolResult(content="Error: query is required", is_error=True)

        # 当前为 stub 实现——返回配置提示，引导用户设置搜索 API 密钥
        return ToolResult(
            content=(
                "WebSearch requires API key configuration. "
                "Set BRAVE_API_KEY or SERPAPI_KEY environment variable to enable web search."
            ),
            is_error=True,
        )
