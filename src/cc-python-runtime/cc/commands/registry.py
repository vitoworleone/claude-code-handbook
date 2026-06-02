"""Slash command registry and built-in commands.

Corresponds to TS: commands.ts + commands/.

Slash 命令注册表：管理用户在 REPL 中可用的 /command 命令。
采用注册式设计，支持内置命令和扩展命令的统一管理。
命令处理函数通过 **kwargs 接收上下文参数，不同命令使用不同的参数子集。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SlashCommand:
    """A registered slash command.

    name: 命令名称（不含斜杠），如 "help"
    description: 命令的简短描述，用于 /help 列表展示
    handler: 命令处理函数，接收 **kwargs 返回字符串结果
    """

    name: str
    description: str
    handler: Any  # Callable — typed loosely to avoid complex generics


# 全局命令注册表，所有命令注册后存储在此字典中
# 使用模块级变量而非类实例，因为命令注册是全局唯一的
_commands: dict[str, SlashCommand] = {}


def register_command(name: str, description: str, handler: Any) -> None:
    """Register a slash command.

    允许同名命令覆盖注册，后注册的优先。
    这使得扩展命令可以覆盖内置命令的行为。
    """
    _commands[name] = SlashCommand(name=name, description=description, handler=handler)


def get_command(name: str) -> SlashCommand | None:
    """Look up a slash command by name."""
    return _commands.get(name)


def list_commands() -> list[SlashCommand]:
    """List all registered commands."""
    return list(_commands.values())


def is_slash_command(text: str) -> bool:
    """Check if input is a slash command.

    仅通过前缀 "/" 判断，不验证命令是否实际注册。
    这样可以在解析阶段先识别意图，再在执行阶段报告未知命令。
    """
    return text.strip().startswith("/")


def parse_slash_command(text: str) -> tuple[str, str]:
    """Parse a slash command into (name, args).

    将 "/command arg1 arg2" 解析为 ("command", "arg1 arg2")。
    使用 split(None, 1) 只分割第一个空白符，保留参数中的空格。
    如果输入不是斜杠命令，返回 ("", 原文本)。
    """
    text = text.strip()
    if not text.startswith("/"):
        return "", text

    # 去掉前导斜杠后按空白符分割，最多分两部分
    parts = text[1:].split(None, 1)
    name = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return name, args


# ---- 内置命令处理函数 ----
# 每个处理函数接收 **kwargs 并返回字符串。
# 特殊返回值（以 __ 包裹的）被 REPL 主循环拦截并执行对应的内部操作，
# 而非直接展示给用户。

def _help_handler(**_kwargs: Any) -> str:
    """Show available commands.

    遍历所有已注册命令，按名称排序后格式化输出。
    """
    lines = ["Available commands:"]
    for cmd in sorted(_commands.values(), key=lambda c: c.name):
        lines.append(f"  /{cmd.name} — {cmd.description}")
    return "\n".join(lines)


def _clear_handler(**_kwargs: Any) -> str:
    """返回特殊标记 __CLEAR__，REPL 主循环识别后会清空对话历史。"""
    return "__CLEAR__"


def _compact_handler(**_kwargs: Any) -> str:
    """返回特殊标记 __COMPACT__，REPL 主循环识别后会触发上下文压缩。"""
    return "__COMPACT__"


def _cost_handler(**kwargs: Any) -> str:
    """展示当前会话的累计 token 使用量。

    从 kwargs 中读取 REPL 传入的 token 统计数据。
    """
    total_in = kwargs.get("total_input_tokens", 0)
    total_out = kwargs.get("total_output_tokens", 0)
    return f"Session usage: {total_in} input tokens, {total_out} output tokens"


# 百炼兼容模型前缀——这些模型走阿里云百炼 Anthropic 兼容接口
DASHSCOPE_MODELS = {"qwen3-max", "glm-5", "kimi-k2.5"}

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"


def is_dashscope_model(model: str) -> bool:
    """判断模型是否走阿里云百炼接口。"""
    return model in DASHSCOPE_MODELS


# 可用模型列表（Anthropic 原生 + 阿里云百炼兼容）
AVAILABLE_MODELS = [
    # Anthropic
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-haiku-4-5-20251001",
    # 阿里云百炼（需配置 ANTHROPIC_BASE_URL）
    "qwen3-max",
    "glm-5",
    "kimi-k2.5",
]


def _model_handler(**kwargs: Any) -> str:
    """Show or change the current model.

    支持三种用法：
      /model          → 显示当前模型 + 可用列表（带序号）
      /model 2        → 按序号切换
      /model qwen3-max → 按名称切换
    """
    new_model = kwargs.get("args", "").strip()
    if not new_model:
        current = kwargs.get("current_model", "unknown")
        lines = [f"Current model: {current}", "", "Available models (use /model <number> to switch):"]
        for i, m in enumerate(AVAILABLE_MODELS, 1):
            marker = "*" if m == current else " "
            lines.append(f"  {marker} {i}. {m}")
        return "\n".join(lines)

    # 支持数字选择
    if new_model.isdigit():
        idx = int(new_model)
        if 1 <= idx <= len(AVAILABLE_MODELS):
            return f"__MODEL__{AVAILABLE_MODELS[idx - 1]}"
        return f"Invalid model number: {new_model}. Use 1-{len(AVAILABLE_MODELS)}."

    return f"__MODEL__{new_model}"


# ---- 注册内置命令 ----
# 模块导入时自动注册，确保这些命令始终可用
register_command("help", "Show available commands", _help_handler)
register_command("clear", "Clear conversation history", _clear_handler)
register_command("compact", "Compact conversation context", _compact_handler)
register_command("cost", "Show token usage for this session", _cost_handler)
register_command("model", "Show or change the current model", _model_handler)
