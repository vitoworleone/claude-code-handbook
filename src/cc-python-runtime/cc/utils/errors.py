"""Custom error types.

定义项目专用的异常层次结构。
所有自定义异常都继承自 CCError 基类，这样调用方可以通过
捕获 CCError 来统一处理所有项目内部错误，同时也可以
捕获具体子类来进行细粒度的错误处理。

Corresponds to TS: various error handling patterns across the codebase.
"""

from __future__ import annotations


class CCError(Exception):
    """Base exception for all cc errors.

    作为所有自定义异常的基类，使得上层代码可以用
    except CCError 统一捕获所有业务逻辑异常，
    而不会误捕获系统级异常（如 MemoryError、SystemExit）。
    """


class ConfigError(CCError):
    """Configuration error (missing API key, invalid config, etc.).

    在 API key 缺失、配置文件格式错误、必填参数未设置等情况下抛出。
    通常发生在程序启动阶段的配置加载环节。
    """


class APIError(CCError):
    """Anthropic API error.

    封装了来自 Anthropic API 的错误信息，附带 HTTP 状态码和错误类型。
    常见场景：速率限制 (429)、认证失败 (401)、服务不可用 (503)。
    """

    def __init__(self, message: str, status_code: int = 0, error_type: str = "") -> None:
        super().__init__(message)
        # HTTP 状态码，0 表示未知或非 HTTP 错误（如网络超时）
        self.status_code = status_code
        # API 返回的错误类型字符串（如 "overloaded_error"、"invalid_request_error"）
        self.error_type = error_type


class ToolExecutionError(CCError):
    """Tool execution error.

    工具执行过程中发生的错误。
    注意：在 query_loop 中，工具错误通常不会抛出此异常，
    而是返回 is_error=True 的 ToolResult（确保循环不被中断）。
    此异常主要用于工具注册、初始化等非执行阶段的错误。
    """

    def __init__(self, message: str, tool_name: str = "") -> None:
        super().__init__(message)
        # 出错的工具名称，便于日志和调试时快速定位问题工具
        self.tool_name = tool_name


class CompactError(CCError):
    """Context compaction failed.

    上下文压缩失败时抛出。通常意味着对话历史无法被有效缩减，
    可能需要用户手动清理对话或调整压缩策略。
    """


class AbortError(CCError):
    """User interrupted the operation.

    用户主动中断操作（如按 Ctrl+C），与 KeyboardInterrupt 不同的是，
    AbortError 可以被业务逻辑层捕获并做优雅的清理处理。
    """
