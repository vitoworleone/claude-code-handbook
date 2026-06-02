"""System prompt assembly.

Corresponds to TS: utils/systemPrompt.ts + constants/prompts.ts getSystemPrompt().

本模块负责将各个 prompt 段落拼装成完整的 system prompt。
拼装顺序很重要——前面的静态段落可以利用 API 的 prompt caching 机制缓存，
后面的动态段落（环境信息、memory、CLAUDE.md）每次请求可能不同。

主要函数：
  - compute_env_info(): 生成环境信息段落（平台、shell、工作目录等）
  - build_system_prompt(): 按固定顺序拼装所有段落，返回段落列表
"""

from __future__ import annotations

import os
import platform
from datetime import UTC, datetime
from pathlib import Path

from .sections import (
    SUMMARIZE_TOOL_RESULTS,
    build_memory_prompt,
    get_actions_section,
    get_doing_tasks_section,
    get_intro_section,
    get_output_efficiency_section,
    get_system_section,
    get_tone_style_section,
    get_using_tools_section,
)


def compute_env_info(
    cwd: str,
    model: str,
    is_git: bool | None = None,
) -> str:
    """Compute environment information section.

    Corresponds to TS: constants/prompts.ts computeSimpleEnvInfo().

    生成运行环境描述段落，让模型了解自身的运行上下文。
    包含工作目录、是否为 git 仓库、平台、shell 类型、日期等信息。
    这些信息帮助模型生成适合当前环境的命令和建议。
    """
    # 如果调用者未指定 is_git，通过检查 .git 目录是否存在来自动检测
    if is_git is None:
        is_git = Path(cwd, ".git").exists()

    # Python create_subprocess_shell() uses COMSPEC/cmd.exe on Windows even when
    # the parent process was launched from PowerShell. Tell the model the shell
    # that BashTool actually executes to avoid mixed POSIX/Windows commands.
    if platform.system().lower() == "windows":
        shell_name = f"{os.environ.get('COMSPEC', 'cmd.exe')} (Windows cmd.exe syntax)"
    else:
        shell = os.environ.get("SHELL", "unknown")
        shell_name = "zsh" if "zsh" in shell else ("bash" if "bash" in shell else shell)

    try:
        uname_sr = f"{platform.system()} {platform.release()}"
    except Exception:
        uname_sr = "Unknown"

    # 使用 UTC 时间避免时区问题
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    return f"""# Environment
You have been invoked in the following environment:
 - Primary working directory: {cwd}
  - Is a git repository: {is_git}
 - Platform: {platform.system().lower()}
 - Shell: {shell_name}
 - OS Version: {uname_sr}
 - You are powered by the model {model}.
 - The current date is {today}.
 - Shell command guidance: generate commands for the listed shell. On Windows, avoid POSIX-only utilities like head, tail, pwd, grep, sed, awk, rm, and sleep unless you explicitly invoke an environment that provides them."""


def build_system_prompt(
    cwd: str,
    model: str,
    claude_md_content: str | None = None,
    memory_dir: str | None = None,
    memory_index_content: str | None = None,
) -> list[str]:
    """Build the complete system prompt.

    Corresponds to TS: constants/prompts.ts getSystemPrompt().

    段落拼装顺序（从上到下）：
      1. 静态段落（intro → system → doing_tasks → actions → using_tools → tone → efficiency）
         这些段落内容固定不变，API 层可利用 prompt caching 避免重复计算 token
      2. 动态段落（env_info → summarize_tool_results）
         环境信息每次可能不同（如 cwd 变化），放在静态段落之后
      3. 条件段落（memory → CLAUDE.md）
         仅在启用 memory 系统或存在 CLAUDE.md 文件时才注入

    返回字符串列表（而非拼接后的单个字符串），因为 API 层需要将它们
    作为独立的 cache_control 段传入，以最大化 prompt caching 命中率。

    Args:
        cwd: Current working directory.
        model: Model identifier string.
        claude_md_content: Loaded CLAUDE.md text (if any).
        memory_dir: Absolute path to the memory directory (enables memory prompt).
        memory_index_content: Content of MEMORY.md index file (if exists).

    Returns a list of prompt sections that are joined by the API layer.
    """
    sections: list[str | None] = [
        # --- 静态段落（可缓存）---
        # 按照 TS 原版 getSystemPrompt() 中的顺序排列
        get_intro_section(),          # 角色定义和基本安全指令
        get_system_section(),         # 系统行为规则（权限、标签处理等）
        get_doing_tasks_section(),    # 任务执行原则（先读再改、不过度工程等）
        get_actions_section(),        # 操作风险评估和确认机制
        get_using_tools_section(),    # 工具使用偏好（专用工具优先于 Bash）
        get_tone_style_section(),     # 输出风格（简洁、无 emoji）
        get_output_efficiency_section(),  # 输出效率要求
        # --- 动态段落 ---
        compute_env_info(cwd, model),     # 运行环境信息
        SUMMARIZE_TOOL_RESULTS,           # 提醒模型记录工具结果中的关键信息
    ]

    # Memory 系统 prompt — 包含完整的记忆行为指令 + MEMORY.md 索引内容
    # 仅在配置了 memory_dir 时才注入
    # 对应 TS: memdir/memdir.ts loadMemoryPrompt() → buildMemoryPrompt()
    if memory_dir:
        sections.append(build_memory_prompt(memory_dir, memory_index_content))

    # CLAUDE.md 内容注入 — 项目/用户级的自定义指令
    # 这是用户控制模型行为的主要方式，放在最后确保其指令优先级最高
    if claude_md_content:
        sections.append(f"""# CLAUDE.md
Codebase and user instructions are shown below. Be sure to adhere to these instructions.

{claude_md_content}""")

    # 过滤掉 None 值（某些段落可能因条件未满足而为 None）
    return [s for s in sections if s is not None]
