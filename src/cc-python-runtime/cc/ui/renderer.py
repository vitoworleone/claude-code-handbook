"""Terminal UI renderer using Rich.

本模块负责将 query_loop 产生的事件流（QueryEvent）渲染为终端可视化输出。
采用事件驱动的设计模式：核心逻辑产出事件 -> UI 层消费事件并渲染。
这种解耦使得核心逻辑不依赖任何具体的 UI 实现（可替换为 Web UI、测试 mock 等）。

Corresponds to TS: components/ + screens/ (reimplemented with Rich).
Consumes QueryEvent stream and renders to terminal.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from cc.core.events import QueryEvent

# 全局 Console 实例，所有渲染通过它输出到终端
console = Console()

# 当前工作目录前缀，用于将绝对路径转为相对路径显示
# 末尾加 os.sep 确保只匹配完整目录前缀（不误截 /Users/foo/bar-baz 中的 /Users/foo/bar）
_cwd_prefix = os.getcwd() + os.sep
# 用户 home 目录前缀，将 /Users/xxx/ 替换为 ~/，避免暴露用户名
_home_prefix = os.path.expanduser("~") + os.sep


def _shorten_paths(text: str) -> str:
    """将文本中的绝对路径缩短，仅用于显示。

    替换顺序：先 cwd（更长更具体），再 home（更短更通用）。
    """
    text = text.replace(_cwd_prefix, "")
    text = text.replace(_home_prefix, "~/")
    return text


def render_event(event: QueryEvent) -> None:
    """Render a single query event to the terminal.

    通过 isinstance 分派不同类型的事件到对应的渲染逻辑。
    每种事件类型对应 query_loop 中的一个阶段：
    - TextDelta：模型正在生成文本（流式输出）
    - ThinkingDelta：模型的思维链输出（以灰色显示，与正文区分）
    - ToolUseStart：模型发起了工具调用
    - ToolResultReady：工具执行完毕返回结果
    - CompactOccurred：上下文超长触发了自动压缩
    - TurnComplete：一轮对话结束（模型停止生成）
    - ErrorEvent：运行时错误
    """
    from cc.core.events import (
        CompactOccurred,
        ErrorEvent,
        TextDelta,
        ThinkingDelta,
        ToolResultReady,
        ToolUseStart,
        TurnComplete,
    )

    if isinstance(event, TextDelta):
        # 流式文本输出：end="" 确保不换行，因为文本是逐 token 到达的
        # highlight=False 禁止 Rich 自动语法高亮，保持原始文本样式
        console.print(event.text, end="", highlight=False)

    elif isinstance(event, ThinkingDelta):
        # 思维链以 dim（暗淡）样式显示，让用户知道这是模型的"思考过程"而非最终输出
        console.print(Text(event.text, style="dim"), end="")

    elif isinstance(event, ToolUseStart):
        # 工具调用开始：先换行与前面的文本输出分隔，然后显示工具名和输入预览
        console.print()
        console.print(
            Text(f"  [{event.tool_name}] ", style="bold cyan"),
            end="",
        )
        # 截断过长的输入参数预览，避免刷屏
        # 将绝对路径转为相对路径，让显示更简洁
        input_preview = _shorten_paths(str(event.input))
        if len(input_preview) > 120:
            input_preview = input_preview[:120] + "..."
        console.print(Text(input_preview, style="dim"))

    elif isinstance(event, ToolResultReady):
        # 工具执行结果：错误用红色显示，正常结果用暗绿色显示
        if event.is_error:
            console.print(Text(f"  Error: {_shorten_paths(event.content[:200])}", style="red"))
        else:
            # 同样截断过长的输出，用户如需完整内容可在对话中查看
            preview = event.content[:200]
            if len(event.content) > 200:
                preview += "..."
            console.print(Text(f"  {_shorten_paths(preview)}", style="dim green"))

    elif isinstance(event, CompactOccurred):
        # 上下文压缩通知：以黄色醒目显示，提醒用户对话历史已被压缩
        console.print()
        console.print(Text("  [Context compacted]", style="bold yellow"))

    elif isinstance(event, TurnComplete):
        # 一轮对话结束
        if event.stop_reason == "end_turn":
            # end_turn 表示模型主动结束（非工具调用中断），添加换行确保格式整洁
            console.print()  # Final newline after text
        # 显示本轮的 token 消耗统计，帮助用户了解资源使用情况
        usage = event.usage
        if usage.input_tokens > 0 or usage.output_tokens > 0:
            console.print(
                Text(
                    f"  ({usage.input_tokens} in / {usage.output_tokens} out tokens)",
                    style="dim",
                )
            )

    elif isinstance(event, ErrorEvent):
        # 错误事件以醒目的红色粗体显示
        console.print()
        console.print(Text(f"Error: {_shorten_paths(event.message)}", style="bold red"))


def print_welcome() -> None:
    """Print the welcome banner."""
    # 启动时显示版本信息和基本操作提示
    console.print()
    console.print(Text("cc-python-claude v0.1.0", style="bold blue"))
    console.print(Text("Type your message, or /help for commands. Ctrl+C to interrupt, Ctrl+D to exit.", style="dim"))
    console.print()


def print_prompt() -> str:
    """Display the input prompt and read user input."""
    try:
        # 使用 Rich 的 markup 语法 [bold blue] 渲染蓝色粗体的提示符 ">"
        return console.input("[bold blue]> [/]")
    except EOFError:
        # Ctrl+D 触发 EOFError，向上传播由调用方处理退出逻辑
        raise
    except KeyboardInterrupt:
        # Ctrl+C 中断当前输入，返回空字符串（不退出程序）
        console.print()
        return ""
