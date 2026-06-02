"""Application and query loop state types.

Corresponds to TS: bootstrap/state.ts + query.ts State type.

本模块定义了应用程序和查询循环中使用的核心状态数据结构，包括：
- AutoCompactTracking: 自动压缩的追踪状态
- QueryState: 查询循环每一轮的不可变状态
- ThinkingConfig: 扩展思考模式的配置
- AppConfig: 全局应用配置（API、行为、路径、会话）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    # 仅在类型检查时导入 Message，避免循环依赖
    from .messages import Message


@dataclass
class AutoCompactTracking:
    """Tracking state for auto-compaction.

    Corresponds to TS: query.ts autoCompactTracking in State.

    自动压缩追踪状态：记录上下文窗口自动压缩的运行情况。
    当对话过长时，系统会自动压缩历史消息以节省 token。
    """

    # 连续压缩失败的次数，用于判断是否需要放弃压缩
    consecutive_failures: int = 0
    # 上一次执行压缩的轮次编号，-1 表示尚未执行过压缩
    last_compact_turn: int = -1


@dataclass
class QueryState:
    """Immutable state for each iteration of the query loop.

    Corresponds to TS: query.ts State type (lines 204-217).

    查询循环状态：每一轮对话迭代的快照。
    包含消息列表、轮次计数、恢复计数等运行时状态。
    """

    # 当前对话的完整消息列表
    messages: list[Message] = field(default_factory=list)
    # 当前对话轮次计数
    turn_count: int = 0
    # 因输出 token 超限而触发恢复的次数
    max_output_tokens_recovery_count: int = 0
    # 是否已尝试过被动压缩（响应式压缩）
    has_attempted_reactive_compact: bool = False
    # 自动压缩的追踪状态
    auto_compact_tracking: AutoCompactTracking = field(default_factory=AutoCompactTracking)
    # 当前轮次的唯一标识符（UUID）
    turn_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class ThinkingConfig:
    """Configuration for extended thinking.

    Corresponds to TS: utils/thinking.ts ThinkingConfig.

    扩展思考配置：控制模型是否启用"深度思考"模式及其 token 预算。
    """

    # 思考模式类型，"enabled" 表示启用
    type: str = "enabled"
    # 分配给扩展思考的 token 预算上限
    budget_tokens: int = 10000


@dataclass
class AppConfig:
    """Global application configuration.

    Corresponds to TS: various settings sources.

    全局应用配置：包含 API 连接、模型行为、文件路径和会话管理的所有配置项。
    """

    # ---- API 相关配置 ----
    # Anthropic API 密钥，为 None 时从环境变量读取
    api_key: str | None = None
    # 使用的模型名称
    model: str = "claude-sonnet-4-20250514"
    # 单次响应的最大输出 token 数
    max_tokens: int = 16384

    # ---- 行为配置 ----
    # 扩展思考配置，为 None 时不启用深度思考
    thinking: ThinkingConfig | None = None
    # 单次会话允许的最大对话轮次
    max_turns: int = 100
    # 是否启用详细日志输出
    verbose: bool = False

    # ---- 路径配置 ----
    # 当前工作目录
    cwd: Path = field(default_factory=Path.cwd)
    # Claude 配置目录（存放 CLAUDE.md、会话历史等）
    claude_dir: Path = field(default_factory=lambda: Path.home() / ".claude")

    # ---- 会话配置 ----
    # 当前会话的唯一标识符（UUID），用于会话持久化和恢复
    session_id: str = field(default_factory=lambda: str(uuid4()))
