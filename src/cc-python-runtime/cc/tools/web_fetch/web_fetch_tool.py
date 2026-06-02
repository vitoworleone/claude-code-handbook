"""WebFetchTool — fetch web pages and convert to text.

Corresponds to TS: tools/WebFetchTool/WebFetchTool.ts.
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

WEB_FETCH_TOOL_NAME = "WebFetch"
# 限制抓取内容大小为 100KB，防止超大页面撑爆上下文窗口
MAX_CONTENT_BYTES = 100_000


class WebFetchTool(Tool):
    """Fetch a URL and return its content.

    Corresponds to TS: tools/WebFetchTool/WebFetchTool.ts.
    支持自动将 HTML 转换为 Markdown 格式，使内容更易于模型理解。
    当 markdownify 库不可用时，回退返回原始 HTML。
    """

    def get_name(self) -> str:
        return WEB_FETCH_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=WEB_FETCH_TOOL_NAME,
            description="Fetches a URL and returns its content as text or markdown.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                },
                "required": ["url"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # HTTP 请求不修改本地文件系统，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        url = tool_input.get("url", "")
        if not url:
            return ToolResult(content="Error: url is required", is_error=True)

        # 延迟导入 httpx，避免在不使用该工具时增加启动时间
        import httpx

        try:
            # follow_redirects=True 自动跟随重定向（如 HTTP→HTTPS），
            # 30 秒超时防止慢速服务器阻塞整个对话循环
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                body = response.text

                # 内容截断：与 BashTool 类似，用 1/4 字节数估算字符截断点
                if len(body.encode("utf-8")) > MAX_CONTENT_BYTES:
                    body = body[:MAX_CONTENT_BYTES // 4]
                    body += f"\n\n... (content truncated, exceeded {MAX_CONTENT_BYTES} bytes)"

                # HTML → Markdown 转换：Markdown 格式去除了 HTML 标签噪音，
                # 让模型更容易提取有用信息；同时去掉 script/style 标签
                if "text/html" in content_type:
                    try:
                        from markdownify import markdownify

                        body = markdownify(body, heading_style="ATX", strip=["script", "style"])
                    except ImportError:
                        # markdownify 是可选依赖，未安装时直接返回原始 HTML
                        pass  # markdownify not installed, return raw HTML

                return ToolResult(content=body)

        except httpx.HTTPStatusError as e:
            return ToolResult(content=f"HTTP error {e.response.status_code}: {e}", is_error=True)
        except httpx.ConnectError:
            return ToolResult(content=f"Error: Could not connect to {url}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Error fetching URL: {e}", is_error=True)
