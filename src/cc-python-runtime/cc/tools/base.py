"""Tool base class and types.

Corresponds to TS: Tool.ts (ToolDef, buildTool) + tools.ts (assembleToolPool).
"""

# 本模块定义了工具系统的四大基石：注册和发现
# 1. ToolSchema —— 工具的 JSON Schema 描述，用于向 API 注册工具能力
# 2. ToolResult —— 工具执行后的统一返回格式，支持纯文本和富内容（如图片）
# 3. Tool —— 所有工具的抽象基类，定义了名称、schema、执行、并发安全四个接口契约
# 4. ToolRegistry —— 工具注册表，负责工具的注册、查找和批量 schema 导出
#
# 设计哲学：通过抽象基类 + 注册表模式，将工具的定义与发现解耦，
# 使得 query_loop 不需要知道具体有哪些工具，只需通过 registry 动态查找。

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSchema:
    """Tool schema for API registration."""
    # 对应 Anthropic API 中 tool 定义的三个必要字段：
    # - name: 工具的唯一标识符，API 返回 tool_use 时通过此名称匹配
    # - description: 工具功能描述，影响模型是否选择调用此工具
    # - input_schema: JSON Schema 格式的参数定义，模型据此生成合法的调用参数

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution.

    FIX (check.md #5): content can be str or list of content block dicts
    to support images, structured MCP results, etc.
    """
    # content 支持两种类型：
    # - str: 普通文本结果，绝大多数工具返回此类型
    # - list[dict]: 富内容块列表，用于返回图片（base64）、MCP 结构化结果等
    #   例如 FileReadTool 读取图片时返回 [{"type": "image", "source": {...}}]
    # 这种联合类型设计是为了兼容 Anthropic API 的 content block 格式，
    # 使得工具结果可以直接嵌入 API 的 tool_result 消息中。

    content: str | list[dict[str, Any]]
    # is_error 标记此结果是否为错误。关键设计：工具错误不抛异常，而是返回
    # is_error=True 的 ToolResult，这样 query_loop 不会中断，模型可以看到
    # 错误信息并决定如何处理（重试、换参数、或向用户解释）。
    is_error: bool = False

    @property
    def text(self) -> str:
        """Extract text content regardless of content type."""
        # 提供统一的文本提取接口，无论 content 是 str 还是 list[dict]。
        # 当 content 是富内容块列表时，尝试从每个 dict 中提取 "text" 字段，
        # 用换行连接。这在 hooks 的 PostToolUse 回调中特别有用，
        # 因为 hooks 只需要文本摘要而不关心图片等富内容。
        if isinstance(self.content, str):
            return self.content
        return "\n".join(
            block.get("text", str(block)) for block in self.content if isinstance(block, dict)
        )


class Tool(ABC):
    """Base class for all tools.

    Corresponds to TS: Tool.ts ToolDef interface.
    """
    # 所有工具必须实现以下四个方法（其中 is_concurrency_safe 有默认实现）。
    # 这套接口契约保证了工具系统的可扩展性：新增工具只需继承 Tool 并实现这些方法，
    # 无需修改 orchestration、streaming_executor 等编排层代码。

    @abstractmethod
    def get_name(self) -> str:
        """Return the tool name as registered with the API."""
        ...

    @abstractmethod
    def get_schema(self) -> ToolSchema:
        """Return the tool's JSON schema for API registration."""
        ...

    @abstractmethod
    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given input.

        Args:
            tool_input: Validated input parameters.

        Returns:
            ToolResult with content and error status.
        """
        ...

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        """Whether this tool can run concurrently with others.

        Corresponds to TS: Tool.ts isConcurrencySafe.
        Override in subclasses. Default: False (serial).
        """
        # 默认返回 False，意味着此工具会独占执行——
        # orchestration 在调度时会等待所有并发工具完成后，才单独执行此工具。
        # 只读工具（如 FileReadTool）应覆写为 True，允许多个读操作并行，
        # 而写操作（如 FileEditTool、BashTool）保持 False 以避免竞态条件。
        # 参数 tool_input 允许根据具体输入动态判断，例如 BashTool 可以对只读命令返回 True。
        return False


@dataclass
class ToolRegistry:
    """Registry of available tools.

    Corresponds to TS: tools.ts assembleToolPool().
    """
    # 工具注册表是工具发现机制的核心。query_loop 通过 registry 查找工具，
    # 而不是直接持有工具实例，这使得工具集可以在运行时动态组装。
    # 例如：子 agent 的 registry 会排除 AgentTool（防止无限递归），
    # 后台 agent 的 registry 会排除交互式工具（如 AskUserQuestion）。

    # 使用 dict 存储，key 为工具名称，保证 O(1) 查找性能
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        # 不允许重复注册同名工具，因为工具名是 API 层面的唯一标识，
        # 重复会导致 tool_use 响应无法正确路由。
        name = tool.get_name()
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        # 返回 None 而非抛异常，因为 API 可能返回未注册的工具名（如 MCP 工具被移除），
        # 调用方（orchestration）会将 None 转化为 is_error=True 的 ToolResult。
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        # 用于 AgentTool 构建子 registry 时遍历父 registry 的所有工具
        return list(self._tools.values())

    def get_api_schemas(self) -> list[dict[str, Any]]:
        """Return all tool schemas in API format."""
        # 将所有工具的 schema 转为 API 请求所需的 dict 格式列表。
        # 这个列表会作为 API 请求中 "tools" 参数的值，告诉模型可用的工具集。
        schemas = []
        for tool in self._tools.values():
            schema = tool.get_schema()
            schemas.append({
                "name": schema.name,
                "description": schema.description,
                "input_schema": schema.input_schema,
            })
        return schemas
