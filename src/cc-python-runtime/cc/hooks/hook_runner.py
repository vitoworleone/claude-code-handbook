"""Hook system — execute shell commands before/after tool use.

Corresponds to TS: hooks/ system.
Hooks are configured in settings.json and run as shell commands.

Hook 系统：允许用户在工具执行前后运行自定义 shell 命令。
典型用途：
- PreToolUse: 在执行 Bash 命令前检查是否安全（如禁止 rm -rf）
- PostToolUse: 在文件修改后自动运行 lint/format
- 通过退出码 2 可以阻止工具执行（仅 PreToolUse 生效）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Hook 执行的超时时间（秒）
# 超时后 hook 进程会被强制杀死，防止挂起的 hook 阻塞整个工具执行流程
HOOK_TIMEOUT_S = 10.0


@dataclass
class HookConfig:
    """A configured hook.

    event: hook 触发时机，"PreToolUse"（工具执行前）或 "PostToolUse"（工具执行后）
    command: 要执行的 shell 命令
    tool_name: 限定 hook 只对特定工具生效，None 表示对所有工具生效
    """

    event: str  # "PreToolUse" | "PostToolUse"
    command: str
    tool_name: str | None = None  # None = all tools


@dataclass
class HookResult:
    """Result of running a hook.

    blocked: 是否阻止了工具执行（仅 PreToolUse hook 通过退出码 2 触发）
    message: hook 的标准输出内容，用于向用户展示阻止原因或其他信息
    """

    blocked: bool = False
    message: str = ""


def load_hooks(claude_dir: Path | None = None) -> list[HookConfig]:
    """Load hook configurations from settings.json.

    Corresponds to TS: hooks loading from settings.

    从 settings.json 的 "hooks" 字段加载配置。
    支持两种格式：
    1. 字典格式：{"command": "...", "tool_name": "Bash"} —— 精确控制
    2. 字符串格式："echo hello" —— 简写，对所有工具生效
    """
    settings_path = (claude_dir or Path.home() / ".claude") / "settings.json"
    if not settings_path.is_file():
        return []

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    hooks_config = settings.get("hooks", {})
    hooks: list[HookConfig] = []

    # hooks_config 结构: {"PreToolUse": [...], "PostToolUse": [...]}
    for event_name, event_hooks in hooks_config.items():
        if not isinstance(event_hooks, list):
            continue
        for hook_entry in event_hooks:
            if isinstance(hook_entry, dict):
                # 字典格式：支持指定 tool_name 做精确匹配
                hooks.append(HookConfig(
                    event=event_name,
                    command=hook_entry.get("command", ""),
                    tool_name=hook_entry.get("tool_name"),
                ))
            elif isinstance(hook_entry, str):
                # 字符串格式：简写，对所有工具生效
                hooks.append(HookConfig(event=event_name, command=hook_entry))

    return hooks


async def run_hook(
    hook: HookConfig,
    context: dict[str, Any],
) -> HookResult:
    """Execute a hook command.

    Corresponds to TS: hooks execution.

    执行模型：
    1. 通过 stdin 将工具上下文信息以 JSON 格式传递给 hook 脚本
    2. hook 脚本通过退出码表达意图：
       - 0: 允许工具执行（正常通过）
       - 2: 阻止工具执行（blocked=True），stdout 内容作为阻止原因
       - 其他: 发出警告日志但不阻止工具执行
    3. 超时后强制杀死进程，返回空结果（不阻止工具执行）
    """
    if not hook.command:
        return HookResult()

    try:
        # 启动子进程，通过 stdin/stdout/stderr 管道通信
        proc = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 将工具调用的上下文信息序列化为 JSON，通过 stdin 传给 hook 脚本
        context_json = json.dumps(context).encode("utf-8")

        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(input=context_json),
                timeout=HOOK_TIMEOUT_S,
            )
        except TimeoutError:
            # 超时处理：强制杀死进程，suppress ProcessLookupError 以防进程已自行退出
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            logger.warning("Hook timed out: %s", hook.command)
            # 超时不阻止工具执行，避免 hook 脚本的问题影响正常工作流
            return HookResult()

        output = stdout.decode("utf-8", errors="replace").strip()

        # 退出码 2 是特殊约定：表示 hook 主动阻止工具执行
        if proc.returncode == 2:
            return HookResult(blocked=True, message=output or "Blocked by hook")
        # 非 0 非 2 的退出码视为 hook 自身出错，记录警告但不阻止
        if proc.returncode != 0:
            logger.warning("Hook exited with code %d: %s", proc.returncode, hook.command)

        return HookResult(message=output)

    except Exception as e:
        # 完全捕获异常，确保 hook 系统的任何故障都不会影响工具执行
        logger.warning("Hook failed: %s — %s", hook.command, e)
        return HookResult()


async def run_pre_tool_hooks(
    hooks: list[HookConfig],
    tool_name: str,
    tool_input: dict[str, Any],
) -> HookResult:
    """Run all PreToolUse hooks for a tool. Returns blocked if any hook blocks.

    遍历所有 PreToolUse hook，按注册顺序依次执行。
    只要有一个 hook 返回 blocked=True 就立即停止后续 hook 的执行并返回阻止结果。
    这是"短路"语义：第一个拒绝就足以阻止工具执行。
    """
    for hook in hooks:
        # 只执行 PreToolUse 类型的 hook
        if hook.event != "PreToolUse":
            continue
        # 如果 hook 指定了 tool_name 且不匹配当前工具，跳过
        if hook.tool_name is not None and hook.tool_name != tool_name:
            continue

        result = await run_hook(hook, {"tool_name": tool_name, "input": tool_input})
        if result.blocked:
            # 短路返回：一个 hook 阻止就够了
            return result

    # 所有 hook 都通过，返回空结果（blocked=False）
    return HookResult()


async def run_post_tool_hooks(
    hooks: list[HookConfig],
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: str,
) -> None:
    """Run all PostToolUse hooks for a tool.

    PostToolUse hook 在工具执行完成后运行，无法阻止工具执行（结果已产生）。
    工具输出截断到 1000 字符传递给 hook，避免大量输出撑爆 hook 进程的内存。
    返回值为 None 因为 post hook 的结果不影响工具执行流程。
    """
    for hook in hooks:
        if hook.event != "PostToolUse":
            continue
        if hook.tool_name is not None and hook.tool_name != tool_name:
            continue

        await run_hook(hook, {
            "tool_name": tool_name,
            "input": tool_input,
            # 截断工具输出，防止传递过大的数据给 hook 脚本
            "output": tool_output[:1000],
        })
