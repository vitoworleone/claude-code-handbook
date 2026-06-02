"""MCP client — connect to MCP servers and register tools.

MCP (Model Context Protocol) 是一种标准协议，允许 AI 模型连接外部工具服务器。
本模块实现 MCP 客户端功能：连接 MCP 服务器、获取其提供的工具列表、
将每个远程工具包装为本地 Tool 代理对象并注册到 ToolRegistry 中。

这样 agent 就可以像调用本地工具一样透明地调用远程 MCP 服务器上的工具。

Corresponds to TS: services/mcp/client.ts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from cc.tools.base import Tool, ToolRegistry, ToolResult, ToolSchema

if TYPE_CHECKING:
    from .config import McpServerConfig

logger = logging.getLogger(__name__)

# MCP 工具名称前缀，用于在 ToolRegistry 中区分本地工具和 MCP 远程工具
# 格式：mcp__{server_name}__{tool_name}（双下划线分隔，避免与工具名中的单下划线冲突）
MCP_TOOL_NAME_PREFIX = "mcp__"


class McpToolProxy(Tool):
    """Proxy tool that delegates to an MCP server via RPC.

    代理模式：本地创建一个 Tool 实例，其 execute() 方法内部通过
    MCP session 发送 RPC 调用到远程服务器，再将结果转换为本地 ToolResult。

    Corresponds to TS: services/mcp/client.ts MCP tool execution.
    """

    def __init__(
        self, server_name: str, tool_name: str, description: str, input_schema: dict[str, Any], session: Any,
    ) -> None:
        self._server_name = server_name
        self._tool_name = tool_name
        self._description = description
        self._input_schema = input_schema
        # MCP ClientSession 实例，用于发送 RPC 请求
        self._session = session

    def get_name(self) -> str:
        # 使用 "mcp__{server}__{tool}" 格式生成全局唯一的工具名称
        # 这确保不同 MCP 服务器上的同名工具不会冲突
        return f"{MCP_TOOL_NAME_PREFIX}{self._server_name}__{self._tool_name}"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.get_name(),
            description=self._description,
            input_schema=self._input_schema,
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 假设 MCP 工具是并发安全的，因为每次调用都是独立的 RPC 请求
        # 远程服务器自行处理并发控制
        return True  # MCP tools are assumed safe

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        """Execute MCP tool, preserving structured content when possible."""
        try:
            # 通过 MCP session 向远程服务器发起工具调用
            result = await self._session.call_tool(self._tool_name, arguments=tool_input)
            if not hasattr(result, "content") or not result.content:
                return ToolResult(content="(no output)")

            # --- 处理返回内容：尽可能保留结构化信息 ---
            # MCP 工具可能返回多种内容类型（文本、图片等），需要逐一解析
            rich_blocks: list[dict[str, Any]] = []
            text_parts: list[str] = []

            for block in result.content:
                if hasattr(block, "type"):
                    if block.type == "text" and hasattr(block, "text"):
                        # 文本块：同时加入 rich_blocks 和 text_parts
                        rich_blocks.append({"type": "text", "text": block.text})
                        text_parts.append(block.text)
                    elif block.type == "image" and hasattr(block, "data"):
                        # 图片块：转换为 Claude API 的 base64 图片格式
                        rich_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": getattr(block, "mimeType", "image/png"),
                                "data": block.data,
                            },
                        })
                    else:
                        # 未知类型的内容块，降级为文本表示
                        text_parts.append(str(block))
                elif hasattr(block, "text"):
                    text_parts.append(block.text)

            # 如果包含非文本内容（如图片），返回结构化的 rich content
            # 否则将所有文本拼接为纯字符串返回，减少不必要的复杂度
            if any(b.get("type") != "text" for b in rich_blocks):
                return ToolResult(content=rich_blocks)
            return ToolResult(content="\n".join(text_parts) if text_parts else "(no output)")

        except Exception as e:
            # MCP 工具调用失败时返回错误结果（而非抛异常），确保不中断 query_loop
            return ToolResult(content=f"MCP tool error: {e}", is_error=True)


async def connect_mcp_server(
    config: McpServerConfig,
    registry: ToolRegistry,
) -> Any:
    """Connect to an MCP server and register its tools.

    连接流程：
    1. 导入 MCP SDK（不可用则跳过）
    2. 建立 stdio 传输通道（启动子进程）
    3. 初始化 MCP session（握手协商）
    4. 获取服务器的工具列表
    5. 为每个工具创建 McpToolProxy 并注册到 ToolRegistry

    Corresponds to TS: services/mcp/client.ts connectToServer() + fetchToolsForClient().

    Returns the MCP session (or None on failure).
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        # MCP SDK 是可选依赖，未安装时优雅降级
        logger.warning("MCP SDK not installed. Run: pip install mcp")
        return None

    # 当前仅支持 stdio 传输方式（通过子进程的 stdin/stdout 通信）
    # SSE 和 HTTP 传输方式暂未实现
    if config.transport != "stdio":
        logger.warning("Only stdio transport is currently supported, got: %s", config.transport)
        return None

    try:
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env if config.env else None,
        )

        # 启动 MCP 服务器子进程并建立 stdio 通信通道
        # 设置 30 秒超时，防止服务器启动卡住导致主进程阻塞
        read_stream, write_stream = await asyncio.wait_for(
            stdio_client(params).__aenter__(),
            timeout=30.0,
        )

        # 在传输通道上建立 MCP 会话
        # 10 秒超时用于 session 初始化阶段的握手
        session = await asyncio.wait_for(
            ClientSession(read_stream, write_stream).__aenter__(),
            timeout=10.0,
        )

        # 发送 initialize 请求完成协议握手
        await session.initialize()

        # 获取服务器提供的所有工具定义，并逐一注册为本地代理工具
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            proxy = McpToolProxy(
                server_name=config.name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {"type": "object"},
                session=session,
            )
            try:
                registry.register(proxy)
                logger.info("Registered MCP tool: %s", proxy.get_name())
            except ValueError:
                # 工具名重复时跳过注册，可能是配置文件中重复定义了同一服务器
                logger.warning("MCP tool already registered: %s", proxy.get_name())

        return session

    except ImportError:
        logger.warning("MCP SDK not installed")
        return None
    except TimeoutError:
        # 连接超时通常意味着 MCP 服务器未能正常启动
        logger.warning("MCP server connection timed out: %s", config.name)
        return None
    except Exception as e:
        # 捕获所有其他异常，确保单个 MCP 服务器的连接失败不会影响整体启动流程
        logger.warning("Failed to connect MCP server '%s': %s", config.name, e)
        return None
