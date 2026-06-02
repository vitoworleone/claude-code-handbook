"""CLI entry point for cc-python-claude.

Corresponds to TS: main.tsx + entrypoints/cli.tsx.

All modules are wired into the runtime here — no dead code.

=== 整体架构角色 ===

main.py 是整个系统的「控制面」——它不执行对话逻辑，
而是负责把所有运行时依赖装配好，然后交给内核（query_loop 状态机）去运转。

核心职责：
  1. 装配（Wiring）: 工具注册、prompt 构建、权限/hooks/MCP/team 连接
  2. 封装（Packaging）: 把装配结果打包成 QueryEngine 实例
  3. 驱动（Driving）: 用 engine.submit()（一次性）或 engine.run_turn()（REPL 循环）驱动内核
  4. 后处理: 每轮结束后做 session 持久化 + 后台 memory extraction

调用链:
  main() → _run_print_mode() 或 _run_repl()
    → _build_engine() [装配所有依赖]
      → _build_registry() [注册所有工具]
      → _build_system()  [构建 system prompt]
    → engine.submit() 或 engine.run_turn()
      → 内核 query_loop() [状态机运转]

关键设计决策:
  - 所有工具在 _build_registry() 中一次性注册，但部分工具需要在
    _build_engine() 中做「二次布线」（注入运行时依赖如 task_registry、team_context）
  - call_model_factory 通过闭包 + engine_ref 实现延迟绑定，
    使子 agent 能复用父 agent 的 QueryEngine.make_call_model()
  - REPL 主循环是一个 while True 事件循环，每轮结束做 save + extract
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator  # noqa: TC003
from pathlib import Path

import click

# --- 内核层 ---
from cc.api.claude import stream_response          # provider adapter: 把 Anthropic SDK 的流式事件转为内部 QueryEvent
from cc.api.client import create_client             # Anthropic SDK 客户端工厂
from cc.core.events import QueryEvent, TurnComplete # 内核事件类型（query_loop 的产出物）
from cc.core.query_engine import QueryEngine        # 运行时依赖容器，封装 main → query_loop 的中间层

# --- 装配层 ---
from cc.hooks.hook_runner import load_hooks         # 从 settings.json 加载 hook 配置
from cc.models.messages import Message, UserMessage  # transcript 的基本元素
from cc.prompts.builder import build_system_prompt   # system prompt 拼装器（多段合并）
from cc.prompts.claudemd import load_claude_md       # 从 cwd 向上搜索 CLAUDE.md 文件
from cc.session.history import HistoryEntry, add_to_history  # 用户输入历史（~/.claude/history.jsonl）
from cc.session.storage import save_session          # transcript 持久化到 ~/.claude/sessions/
from cc.skills.loader import load_skills             # 从 ~/.claude/skills/ 加载 skill markdown

# --- 工具层（顶层直接 import 的是 6 个核心文件操作工具）---
from cc.tools.base import ToolRegistry               # 工具注册表：name → Tool 实例的映射 + schema 生成
from cc.tools.bash.bash_tool import BashTool
from cc.tools.file_edit.file_edit_tool import FileEditTool
from cc.tools.file_read.file_read_tool import FileReadTool
from cc.tools.file_write.file_write_tool import FileWriteTool
from cc.tools.glob_tool.glob_tool import GlobTool
from cc.tools.grep_tool.grep_tool import GrepTool

# --- UI 层（与内核解耦，只消费事件流）---
from cc.ui.renderer import console, render_event

logger = logging.getLogger(__name__)


def _build_registry(cwd: str, call_model_factory: object | None = None, model: str = "") -> ToolRegistry:
    """Build the default tool registry with all tools.

    === 工具注册策略 ===

    这个函数注册所有工具的「第一遍」。部分工具（AgentTool、TeamCreate、SendMessage、
    TaskStop）需要运行时依赖（如 task_registry、team_context），在这里只做基础注册，
    后续在 _build_engine() 中做「二次布线」覆盖注册。

    工具分层：
      Tier 1 - 文件操作（顶层 import，随 main.py 启动就绑定）
      Tier 2 - 任务/搜索/notebook 等（lazy import，按需加载）
      Tier 3 - 协作工具 Agent/Team/SendMessage（需要 call_model_factory + 运行时注入）

    lazy import 的原因：避免循环依赖 + 减少启动时间。每个工具可能依赖不同的
    第三方库（如 notebook 依赖 nbformat），延迟到实际注册时才导入。
    """
    registry = ToolRegistry()
    # --- Tier 1: 核心文件操作工具（与 cwd 绑定）---
    registry.register(BashTool(cwd=cwd))
    registry.register(FileReadTool())
    registry.register(FileEditTool())
    registry.register(FileWriteTool())
    registry.register(GlobTool())
    registry.register(GrepTool())

    # --- Tier 2: 扩展工具（lazy import）---
    from cc.tools.task_tools.task_tools import (
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskStopTool,
        TaskUpdateTool,
    )

    registry.register(TaskCreateTool())
    registry.register(TaskGetTool())
    registry.register(TaskListTool())
    registry.register(TaskUpdateTool())
    registry.register(TaskStopTool())

    from cc.tools.web_fetch.web_fetch_tool import WebFetchTool

    registry.register(WebFetchTool())

    from cc.tools.ask_user.ask_user_tool import AskUserQuestionTool

    registry.register(AskUserQuestionTool(input_fn=True))

    from cc.tools.todo.todo_write_tool import TodoWriteTool

    registry.register(TodoWriteTool(project_dir=cwd))

    from cc.tools.web_search.web_search_tool import WebSearchTool

    registry.register(WebSearchTool())

    from cc.tools.notebook.notebook_edit_tool import NotebookEditTool

    registry.register(NotebookEditTool())

    from cc.tools.tool_search.tool_search_tool import ToolSearchTool

    registry.register(ToolSearchTool(registry=registry))

    # SkillTool — 让模型可以通过工具调用加载 skill prompt
    # 注意：skills 还有另一条触发路径——在 _run_repl() 中注册为 slash command
    # 两条路径最终效果相同：把 skill 的 markdown prompt 注入 transcript
    from cc.skills.loader import load_skills as _load_skills
    from cc.tools.skill.skill_tool import SkillTool

    skills = _load_skills(cwd)
    registry.register(SkillTool(skills=skills))

    # PlanMode tools — enter/exit plan mode
    from cc.tools.plan_mode.plan_mode_tool import EnterPlanModeTool, ExitPlanModeTool

    registry.register(EnterPlanModeTool())
    registry.register(ExitPlanModeTool())

    # BriefTool — conversation summary stats
    from cc.tools.brief.brief_tool import BriefTool

    registry.register(BriefTool())

    # LSPTool — language server protocol integration (stub)
    from cc.tools.lsp.lsp_tool import LSPTool

    registry.register(LSPTool())

    # --- Tier 3: 协作工具（Team / Swarm）---
    # 这些工具在这里只做「骨架注册」，_build_engine() 会做二次布线注入 team_context
    from cc.tools.send_message.send_message_tool import SendMessageTool
    from cc.tools.team.team_create_tool import TeamCreateTool
    from cc.tools.team.team_delete_tool import TeamDeleteTool

    registry.register(TeamCreateTool())
    registry.register(TeamDeleteTool())
    registry.register(SendMessageTool())

    # AgentTool — 依赖 call_model_factory 来给子 agent 创建 call_model
    # call_model_factory 为 None 时（理论上不会发生）跳过注册
    if call_model_factory is not None:
        from cc.tools.agent.agent_tool import AgentTool

        registry.register(AgentTool(
            parent_registry=registry,
            call_model_factory=call_model_factory,
            model=model,
        ))

    return registry


async def _connect_mcp_servers(cwd: str, registry: ToolRegistry) -> None:
    """Load MCP configs and connect servers.

    MCP (Model Context Protocol) 允许外部进程向 Claude Code 暴露自定义工具。
    配置来源: ~/.claude/mcp.json + 项目级 .mcp.json
    每个 MCP server 连接后，其工具会被动态注册到 registry 中，
    从此与内置工具一视同仁地参与 query_loop 的工具调用流程。
    """
    from cc.mcp.client import connect_mcp_server
    from cc.mcp.config import load_mcp_configs

    configs = load_mcp_configs(cwd)
    for config in configs:
        logger.info("Connecting MCP server: %s", config.name)
        await connect_mcp_server(config, registry)


def _load_env() -> dict[str, str]:
    """Load config from environment variables and project .env file.

    Priority: environment variables > .env file.
    Supported keys: ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, DASHSCOPE_API_KEY.
    """
    result: dict[str, str] = {}

    # Load from .env in project root first (lower priority)
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()

    # Environment variables override .env
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "DASHSCOPE_API_KEY"):
        val = os.environ.get(key)
        if val:
            result[key] = val

    return result


def _get_api_key() -> str | None:
    """Get API key from environment variable or project .env file."""
    return _load_env().get("ANTHROPIC_API_KEY")


def _make_call_model_factory(client: object) -> object:
    """Factory that creates call_model functions for a given model.

    Used by AgentTool to create call_model for sub-agents with different models.

    === call_model 三层抽象 ===

    理解 call_model 是理解整个系统依赖注入的关键：

    1. stream_response(client, model=..., ...)
       ↑ 最底层：直接调 Anthropic SDK，返回 AsyncIterator[QueryEvent]

    2. call_model(**kwargs) → AsyncIterator[QueryEvent]
       ↑ 中间层：绑定了 client + model + max_tokens 的闭包
       query_loop 只认识这个签名，不关心底层是哪个 provider

    3. call_model_factory(model=..., max_tokens=...) → call_model
       ↑ 最顶层：给 AgentTool 用，让子 agent 可以指定不同的 model

    为什么 query_loop 不直接拿 client？
    → 解耦。测试时可以注入 mock_call_model，不需要真实 API 调用。
    → 子 agent 复用同一个 factory，自动继承父 agent 的 client 配置。

    注意：_build_engine() 中实际使用的 factory 是通过 engine_ref 闭包实现的，
    能够透传到 QueryEngine.make_call_model()，而非直接使用这个基础版本。
    """

    def factory(model: str = "claude-sonnet-4-20250514", max_tokens: int = 16384) -> object:
        async def call_model(**kwargs: object) -> AsyncIterator[QueryEvent]:
            # query_loop 在 max_output_tokens 恢复时会传入 max_tokens=65536（ESCALATED_MAX_TOKENS）
            # 这里用 pop 取出来，如果 query_loop 没传就用构造时的默认值
            effective_max: object = kwargs.pop("max_tokens", max_tokens)
            async for event in stream_response(client, model=model, max_tokens=effective_max, **kwargs):  # type: ignore[arg-type]
                yield event

        return call_model

    return factory


def _make_call_model(client: object, model: str, max_tokens: int = 16384) -> object:
    """Create a call_model function bound to a specific model.

    与 _make_call_model_factory 的区别：factory 返回的是「能造 call_model 的函数」，
    这个函数直接返回「一个绑定好的 call_model」。用于不需要动态切换 model 的场景。
    """

    async def call_model(**kwargs: object) -> AsyncIterator[QueryEvent]:
        # 同上：允许 query_loop 在运行时覆盖 max_tokens
        effective_max: object = kwargs.pop("max_tokens", max_tokens)
        async for event in stream_response(client, model=model, max_tokens=effective_max, **kwargs):  # type: ignore[arg-type]
            yield event

    return call_model


def _build_system(cwd: str, model: str, claude_md: str | None) -> str:
    """Build the system prompt with memories and CLAUDE.md.

    === system prompt 的组成 ===

    build_system_prompt() 返回一个 list[str]，每个元素是一个「段落」，
    按以下顺序拼接（由 builder.py 控制）：

      1. Intro      — 身份声明 + 安全指令
      2. System     — 工具选择、输出格式等系统级行为
      3. Doing tasks — 任务执行原则
      4. Actions    — 高风险操作的确认规范
      5. Using tools — 工具使用规范（优先用专用工具而非 Bash）
      6. Tone/Style — 语气和风格
      7. Output efficiency — 简洁输出
      8. Environment — 运行时环境信息（cwd、OS、model 等）
      9. SUMMARIZE   — 工具结果摘要提示
     10. Memory      — 如果 memory_dir 存在，注入 memory 行为指令 + MEMORY.md 索引
     11. CLAUDE.md   — 如果找到 CLAUDE.md，注入用户自定义指令

    这些段落最终用 "\\n\\n" 拼成一个字符串，作为 API 请求的 system 参数。
    """
    from cc.memory.session_memory import get_memory_dir, load_memory_index

    # memory_dir 路径: ~/.claude/projects/<sha256(cwd)[:12]>/memory/
    mem_dir = get_memory_dir(cwd)
    # 提前 mkdir —— 让模型在 tool_use 中可以直接往这个目录写 memory 文件
    # 不在 get_memory_dir() 里做是因为读取路径不应该有写副作用
    mem_dir.mkdir(parents=True, exist_ok=True)
    # 读取 MEMORY.md 索引（一行一条的 markdown 链接列表），作为 memory prompt 的内容
    memory_index = load_memory_index(cwd)

    parts = build_system_prompt(
        cwd=cwd,
        model=model,
        claude_md_content=claude_md,
        memory_dir=str(mem_dir),
        memory_index_content=memory_index,
    )
    return "\n\n".join(parts)


def _build_engine(
    client: object,
    model: str,
    cwd: str,
    *,
    is_interactive: bool = True,
) -> QueryEngine:
    """Build a QueryEngine with all runtime dependencies wired.

    Wiring: PermissionContext, TaskRegistry, BackgroundAgentManager, hooks.

    === 这是整个系统最关键的装配函数 ===

    _build_engine() 把散落的组件焊接成一台可以运转的机器：

    装配步骤（按执行顺序）：
      Step 1: 加载 CLAUDE.md → 构建 system prompt → 加载 hooks
      Step 2: 注入 coordinator prompt（如果处于 coordinator 模式）
      Step 3: 创建 PermissionContext（交互/非交互两种模式）
      Step 4: 创建 TaskRegistry + BackgroundAgentManager + TeamContext
      Step 5: 构建 call_model_factory（通过 engine_ref 延迟绑定）
      Step 6: 调用 _build_registry() 注册所有工具
      Step 7: 「二次布线」—— 用运行时依赖重新注册 AgentTool、TeamCreate 等
      Step 8: 组装 QueryEngine 实例

    为什么需要「二次布线」？
    → _build_registry() 注册工具时，TaskRegistry / TeamContext 等还不存在
    → 它们是在 _build_engine() 中创建的
    → 所以必须先注册一个「骨架版」工具，再用完整版覆盖
    → 这就是为什么你会看到 registry._tools.pop() + registry.register() 的模式

    engine_ref 闭包技巧：
    → factory 闭包捕获了 engine_ref（一个列表），engine 创建后才 append 进去
    → 这样 AgentTool 调用 factory 时，engine 已经存在，可以走 engine.make_call_model()
    → 如果 engine 还不存在（理论上不会），降级到 _make_call_model() 基础版
    """
    # === Step 1: 基础构建 ===
    claude_md = load_claude_md(cwd)       # 从 cwd 向上搜索 .claude/CLAUDE.md
    system = _build_system(cwd, model, claude_md)  # 拼装完整 system prompt
    hooks = load_hooks()                  # 从 ~/.claude/settings.json 读取 hook 配置

    # === Step 2: Coordinator 模式 ===
    # 如果设置了环境变量 CC_COORDINATOR=1，会在 system prompt 前面追加
    # coordinator 专用指令（教模型如何分配任务给 teammates）
    from cc.swarm.coordinator import maybe_inject_coordinator_prompt

    system = maybe_inject_coordinator_prompt(system)

    # === Step 3: 权限上下文 ===
    # PermissionMode.ACCEPT_EDITS: 文件编辑自动放行，危险操作仍需确认
    # is_interactive=False 时（print mode / 子 agent）：所有权限请求自动放行
    from cc.permissions.gate import PermissionContext, PermissionMode

    permission_ctx = PermissionContext(
        mode=PermissionMode.ACCEPT_EDITS,
        is_interactive=is_interactive,
    )

    # === Step 4: 运行时基础设施 ===
    from cc.session.task_registry import TaskRegistry as _TaskRegistry
    from cc.tools.agent.background import BackgroundAgentManager

    task_registry = _TaskRegistry()       # 跟踪所有后台任务（agent/teammate）的状态机
    bg_manager = BackgroundAgentManager(task_registry=task_registry)  # 管理后台 agent 的生命周期

    # TeamContext — 可变团队状态容器（team_name、成员列表）
    # 创建时是空的，TeamCreateTool 触发时才激活
    from cc.swarm.team_context import TeamContext

    team_context = TeamContext()

    # === Step 5: call_model_factory（延迟绑定）===
    # engine_ref 是一个列表，engine 创建后才会被 append 进来
    # 这解决了「factory 需要 engine，但 engine 的构造又需要 factory」的鸡生蛋问题
    engine_ref: list[QueryEngine] = []

    def _factory(m: str | None = None, max_tokens: int = 16384) -> object:
        if engine_ref:
            # 正常路径：engine 已创建，走 QueryEngine.make_call_model()
            return engine_ref[0].make_call_model(model=m, max_tokens=max_tokens)
        # 降级路径：engine 还未创建（理论上不会走到这里）
        return _make_call_model(client, m or model, max_tokens)

    # === Step 6: 工具注册（第一遍）===
    registry = _build_registry(cwd, call_model_factory=_factory, model=model)

    # === Step 7: 二次布线 ===
    # 以下代码用完整版工具实例覆盖 _build_registry() 中注册的骨架版

    # 7a. AgentTool 二次布线：注入 bg_manager + cwd
    from cc.tools.agent.agent_tool import AGENT_TOOL_NAME, AgentTool

    if registry.get(AGENT_TOOL_NAME) is not None:
        registry._tools.pop(AGENT_TOOL_NAME, None)
        registry.register(AgentTool(
            parent_registry=registry,
            call_model_factory=_factory,
            model=model,
            bg_manager=bg_manager,
            cwd=cwd,
        ))

    # 7b. TeamCreate/Delete 二次布线：注入 team_context
    from cc.tools.team.team_create_tool import TeamCreateTool

    tc = registry.get("TeamCreate")
    if isinstance(tc, TeamCreateTool):
        tc._team_context = team_context

    td = registry.get("TeamDelete")
    if td is not None:
        td._team_context = team_context  # type: ignore[attr-defined]

    # 7c. SendMessage 二次布线：注入 team_context（使其能动态解析 team_name）
    from cc.tools.send_message.send_message_tool import SendMessageTool

    sm = registry.get("SendMessage")
    if isinstance(sm, SendMessageTool):
        sm._team_context = team_context

    # 7d. TaskStopTool 二次布线：注入 task_registry（使其能取消后台任务）
    from cc.tools.task_tools.task_tools import TaskStopTool

    task_stop = registry.get("TaskStop")
    if isinstance(task_stop, TaskStopTool):
        task_stop._task_registry = task_registry

    # === Step 8: 组装 QueryEngine ===
    # QueryEngine 是 main.py 和 query_loop 之间的中间层
    # 它持有：client, model, registry, system_prompt, hooks, permission_ctx
    # 并暴露 run_turn()（REPL 单轮）和 submit()（一次性提交）两个驱动接口
    engine = QueryEngine(
        client=client,  # type: ignore[arg-type]
        model=model,
        registry=registry,
        system_prompt=system,
        hooks=hooks,
        permission_ctx=permission_ctx,
    )
    # 挂载额外的运行时状态（QueryEngine 构造函数未显式声明这些属性）
    engine._task_registry = task_registry  # type: ignore[attr-defined]
    engine._bg_manager = bg_manager  # type: ignore[attr-defined]
    engine._team_context = team_context  # type: ignore[attr-defined]
    # 关键：把 engine 放入 engine_ref，让 _factory 闭包从此能访问到 engine
    engine_ref.append(engine)
    return engine


def _read_multiline_input() -> str:
    """Read user input with multi-line support.

    多行输入判定规则（_needs_continuation）：
    - 行尾有反斜杠 \\ → 继续
    - 括号不平衡 ()[]{{}} → 继续
    - 三引号未闭合 \"\"\"/''' → 继续
    """
    lines: list[str] = []
    try:
        first_line = console.input("[bold blue]> [/]")
    except EOFError:
        raise
    except KeyboardInterrupt:
        console.print()
        return ""

    lines.append(first_line)

    while _needs_continuation(lines):
        try:
            next_line = console.input("[dim]... [/]")
            lines.append(next_line)
        except (EOFError, KeyboardInterrupt):
            break

    return "\n".join(lines)


def _needs_continuation(lines: list[str]) -> bool:
    """Check if input needs more lines."""
    text = "\n".join(lines)
    if text.rstrip().endswith("\\"):
        return True
    open_count = text.count("(") + text.count("[") + text.count("{")
    close_count = text.count(")") + text.count("]") + text.count("}")
    if open_count > close_count:
        return True
    return text.count('"""') % 2 != 0 or text.count("'''") % 2 != 0


# ---------------------------------------------------------------------------
# --print mode
# ---------------------------------------------------------------------------


async def _run_print_mode(prompt: str, model: str) -> None:
    """Non-interactive mode: single prompt -> output -> exit.

    P0.5a: Uses QueryEngine to encapsulate runtime dependencies.

    print mode 是最简路径：
      API key → client → _build_engine() → MCP → engine.submit(prompt)
    不需要 session 持久化、memory extraction、resume 等 REPL 专属逻辑。
    engine.submit() 内部创建 transcript [UserMessage(prompt)]，然后直接跑 query_loop。
    """
    env = _load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error: No API key found.[/]")
        sys.exit(1)

    client = create_client(api_key=api_key, base_url=env.get("ANTHROPIC_BASE_URL"))
    cwd = str(Path.cwd())

    engine = _build_engine(client, model, cwd, is_interactive=False)

    # MCP
    await _connect_mcp_servers(cwd, engine.registry)

    async for event in engine.submit(prompt, max_turns=20):
        render_event(event)


# ---------------------------------------------------------------------------
# REPL mode
# ---------------------------------------------------------------------------


async def _run_repl(model: str, resume_id: str | None = None) -> None:
    """Interactive REPL mode.

    P0.5a: Uses QueryEngine for runtime wiring.

    === REPL 主循环结构 ===

    整个函数分为三个阶段：

    【初始化阶段】
      - 创建 client + engine + MCP 连接
      - 注册 skills 为 slash commands
      - 创建 ExtractionCoordinator（后台 memory 提取）
      - 如果有 resume_id，加载并修复旧 transcript + task snapshot

    【主循环】 while True:
      A. 读取用户输入（支持多行）
      B. 判断是否是 slash command（/clear, /compact, /model, /skill）
         → 是：立即处理，continue 回到循环顶部
         → 否：构造 UserMessage，append 到 transcript
      C. 收件箱轮询（如果处于 team 模式，拉取 teammate 消息注入 transcript）
      D. engine.run_turn() → 驱动一轮 query_loop，消费事件流渲染 UI
      E. 后处理：
         - save_session(): 持久化 transcript + task snapshot
         - _bg_extract(): 后台异步提取 memory（不阻塞下一轮输入）

    【退出】
      - 用户输入 EOF（Ctrl+D）时退出
    """
    env = _load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error: No API key found. Set ANTHROPIC_API_KEY env var or add it to .env file.[/]")
        sys.exit(1)

    client = create_client(api_key=api_key, base_url=env.get("ANTHROPIC_BASE_URL"))
    cwd = str(Path.cwd())

    engine = _build_engine(client, model, cwd)

    # MCP
    await _connect_mcp_servers(cwd, engine.registry)

    # Skills — load and register as slash commands
    skills = load_skills(cwd)
    if skills:
        from cc.commands.registry import register_command

        for skill in skills:
            _name = skill.name

            def _make_skill_handler(name: str) -> object:
                def handler(**_kwargs: object) -> str:
                    return f"__SKILL__{name}"
                return handler

            register_command(skill.name, skill.description, _make_skill_handler(_name))

    # messages 是 engine 内部 transcript 的引用——同一个 list 对象
    # REPL 中对 messages 的操作（append/clear/extend）直接影响 engine 的状态
    messages: list[Message] = engine.messages
    # _bg_tasks 持有后台 memory extraction 的 asyncio.Task 引用，防止被 GC 回收
    _bg_tasks: set[asyncio.Task[None]] = set()

    # ExtractionCoordinator 替代了直接调用 extract_memories()
    # 它内部维护增量计数（_last_extracted_count）和 coalescing 逻辑：
    # 如果上一次提取还在运行，新请求会被合并（设置 dirty 标记），等上一次完成后自动重跑
    from cc.memory.extractor import ExtractionCoordinator

    extraction_coord = ExtractionCoordinator()

    # === Resume 恢复流程 ===
    # 从 ~/.claude/sessions/<session_id>.jsonl 加载旧 transcript
    # 必须做 validate_transcript() 修复，因为上次可能是中途崩溃退出的，
    # transcript 末尾可能有 orphaned tool_use（没有配对的 tool_result）
    # → 不修复的话 API 调用会报协议错误
    if resume_id:
        from cc.session.storage import load_session, load_task_snapshot

        loaded = load_session(resume_id)
        if loaded:
            from cc.session.recovery import validate_transcript

            repaired = validate_transcript(loaded)
            messages.extend(repaired)

            # 恢复 TaskRegistry 快照（后台任务状态）
            # 非终态任务（RUNNING/PENDING）会被标记为 KILLED，因为对应的 asyncio.Task 已丢失
            task_snap = load_task_snapshot(resume_id)
            if task_snap and hasattr(engine, '_task_registry'):
                engine._task_registry.restore(task_snap)

            console.print(f"[dim]Resumed session {resume_id} ({len(messages)} messages)[/]")
        else:
            console.print(f"[yellow]Session {resume_id} not found, starting fresh.[/]")

    from uuid import uuid4

    from cc.ui.renderer import print_welcome

    print_welcome()
    session_id = resume_id or str(uuid4())[:8]
    claude_md = load_claude_md(cwd)

    # ==========================================
    # === REPL 主循环开始 ===
    # ==========================================
    while True:
        # --- A. 读取用户输入 ---
        try:
            user_input = _read_multiline_input()
        except EOFError:
            console.print("\nBye!")
            break

        if not user_input.strip():
            continue

        # --- B. Slash command 处理 ---
        # slash command 不进入 query_loop，而是在 REPL 层直接处理
        # 每个 command handler 返回一个标记字符串（__CLEAR__, __MODEL__xxx 等）
        if user_input.strip().startswith("/"):
            from cc.commands.registry import get_command, parse_slash_command

            cmd_name, cmd_args = parse_slash_command(user_input)
            cmd = get_command(cmd_name)
            if cmd is None:
                console.print(f"[red]Unknown command: /{cmd_name}[/]")
                continue

            result = cmd.handler(
                args=cmd_args,
                current_model=engine.model,
                total_input_tokens=engine.total_input_tokens,
                total_output_tokens=engine.total_output_tokens,
            )

            # 各 slash command 的处理分支：
            # __CLEAR__   → 清空 transcript（messages.clear()）
            # __COMPACT__ → 手动触发 compact（压缩 transcript）
            # __MODEL__x  → 切换模型 + 重建 system prompt
            # __SKILL__x  → 把 skill prompt 作为 UserMessage 注入 transcript
            if result == "__CLEAR__":
                messages.clear()
                console.print("[dim]Conversation cleared.[/]")
                continue
            elif result == "__COMPACT__":
                from cc.compact.compact import compact_messages

                compacted = await compact_messages(
                    messages,
                    engine.make_call_model(max_tokens=4096),
                )
                messages.clear()
                messages.extend(compacted)
                console.print("[yellow]Context compacted.[/]")
                continue
            elif isinstance(result, str) and result.startswith("__MODEL__"):
                new_model = result[len("__MODEL__"):]
                # 切换 provider: 百炼模型需要用 DASHSCOPE_API_KEY + 百炼 base_url
                from cc.commands.registry import DASHSCOPE_BASE_URL, is_dashscope_model

                env = _load_env()
                if is_dashscope_model(new_model):
                    ds_key = env.get("DASHSCOPE_API_KEY")
                    if not ds_key:
                        console.print("[red]Error: DASHSCOPE_API_KEY not set. Add it to .env or environment.[/]")
                        continue
                    engine._client = create_client(api_key=ds_key, base_url=DASHSCOPE_BASE_URL)  # type: ignore[assignment]
                else:
                    # 切回 Claude 原生
                    engine._client = create_client(  # type: ignore[assignment]
                        api_key=env.get("ANTHROPIC_API_KEY"),
                        base_url=env.get("ANTHROPIC_BASE_URL"),
                    )
                engine.model = new_model
                engine.system_prompt = _build_system(cwd, engine.model, claude_md)
                console.print(f"[dim]Model changed to: {engine.model}[/]")
                continue
            elif isinstance(result, str) and result.startswith("__SKILL__"):
                from cc.skills.loader import get_skill_by_name

                skill_name = result[len("__SKILL__"):]
                found_skill = get_skill_by_name(skills, skill_name)
                if found_skill:
                    messages.append(UserMessage(content=found_skill.prompt))
                    console.print(f"[dim]Skill /{skill_name} activated[/]")
                else:
                    console.print(f"[red]Skill not found: {skill_name}[/]")
                    continue
            else:
                console.print(result)
                continue
        else:
            # 普通用户输入 → 构造 UserMessage 追加到 transcript
            # 这是 transcript 的第一个写入点：用户消息落盘
            messages.append(UserMessage(content=user_input))

        # 记录到输入历史（~/.claude/history.jsonl），用于 session 列表展示
        add_to_history(HistoryEntry(
            display=user_input[:200],
            timestamp=time.time(),
            project=cwd,
            session_id=session_id,
        ))

        # --- C. 收件箱轮询（仅 Team 模式）---
        # 在进入 query_loop 之前，检查是否有 teammate 发来的消息
        # 如果有，把它们包装成 <task-notification> 格式的 UserMessage 注入 transcript
        # 这样模型在下一轮就能看到 teammate 的汇报
        if hasattr(engine, '_team_context') and engine._team_context.is_active:
            from cc.swarm.identity import TEAM_LEAD_NAME
            from cc.swarm.mailbox import TeammateMailbox

            try:
                mailbox = TeammateMailbox(engine._team_context.team_name)
                inbox = mailbox.receive(TEAM_LEAD_NAME)
                if inbox:
                    mailbox.mark_all_read(TEAM_LEAD_NAME)
                    for msg in inbox:
                        notification = f"<task-notification>\n[From {msg.from_name}]: {msg.text}\n</task-notification>"
                        messages.append(UserMessage(content=notification))
                        console.print(f"[dim]Received message from {msg.from_name}[/]")
            except Exception as e:
                logger.debug("Inbox poll failed: %s", e)

        # --- D. 驱动内核 ---
        # engine.run_turn() 内部调用 query_loop()，这是状态机运转的入口
        # query_loop 是一个 async generator，每 yield 一个 QueryEvent 就在这里被消费
        # 事件类型：TextDelta（文本增量）、ToolUseStart（工具调用开始）、
        #           ToolResultReady（工具结果）、TurnComplete（单轮结束）、ErrorEvent 等
        try:
            async for event in engine.run_turn():
                render_event(event)  # UI 渲染：纯消费，不影响内核状态
                if isinstance(event, TurnComplete):
                    engine._total_input_tokens += event.usage.input_tokens
                    engine._total_output_tokens += event.usage.output_tokens
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted.[/]")
            continue

        # --- E. 后处理 ---

        # E1. 持久化 transcript + task 状态
        # 每轮结束都存一次，这样即使下次崩溃也能 resume 恢复
        task_snap = engine._task_registry.snapshot() if hasattr(engine, '_task_registry') else None
        save_session(session_id, messages, task_snapshot=task_snap)

        # E2. 后台 memory extraction
        # 用一个低配的 call_model（max_tokens=1024）去扫描最近的对话，
        # 提取值得长期记忆的信息保存到 memory 文件 + 更新 MEMORY.md 索引
        # 整个过程是异步的（asyncio.create_task），不阻塞下一轮用户输入
        _extraction_call = engine.make_call_model(max_tokens=1024)
        _extraction_msgs = messages
        _extraction_cwd = cwd

        async def _bg_extract(
            msgs: list[Message] = _extraction_msgs,
            wd: str = _extraction_cwd,
            call: object = _extraction_call,
        ) -> None:
            try:
                saved = await extraction_coord.request_extraction(msgs, wd, call)
                if saved:
                    console.print(f"[dim]Saved {len(saved)} memory(s): {', '.join(saved)}[/]")
            except Exception as e:
                logger.debug("Memory extraction skipped: %s", e)

        task = asyncio.create_task(_bg_extract())
        _bg_tasks.add(task)                       # 持有引用防止 GC
        task.add_done_callback(_bg_tasks.discard)  # 完成后自动移除


# ===========================================================================
# CLI 入口
# ===========================================================================

@click.command()
@click.option("-p", "--print", "print_mode", is_flag=True, help="Non-interactive mode")
@click.option("--model", default="claude-sonnet-4-20250514", help="Model to use")
@click.option("--verbose", is_flag=True, help="Verbose output")
@click.option("-c", "--resume", "resume_id", default=None, help="Resume session by ID")
@click.argument("prompt", required=False)
def main(
    print_mode: bool,
    model: str,
    verbose: bool,
    resume_id: str | None,
    prompt: str | None,
) -> None:
    """cc-python-claude -- Python reimplementation of Claude Code CLI.

    两种运行模式：
      1. print mode (-p): echo "prompt" | python -m cc -p → 单次输出后退出
      2. REPL mode (默认): 交互式对话循环，支持 /command、resume、memory
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    if print_mode:
        if not prompt:
            prompt = sys.stdin.read().strip()
        if not prompt:
            console.print("[red]Error: No prompt provided.[/]")
            sys.exit(1)
        asyncio.run(_run_print_mode(prompt, model))
    else:
        asyncio.run(_run_repl(model, resume_id=resume_id))


if __name__ == "__main__":
    main()
