# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from mewcode.config import ConfigError, load_config
from mewcode.hooks import HookConfigError, HookEngine, load_hooks
from mewcode.permissions import PermissionMode


def main() -> None:
    # 先确保 .mewcode/ 目录存在，否则下面写 debug.log 会因目录不存在而崩溃
    Path(".mewcode").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".mewcode/debug.log",
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="mewcode", description="MewCode AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    if args.p is not None:
        asyncio.run(_run_prompt(config, permission_mode, hook_engine, args.p))
        return

    from mewcode.app import MewCodeApp
    from mewcode.driver import NoAltScreenDriver

    app = MewCodeApp(
        providers=config.providers,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        teammate_mode=config.teammate_mode,
        enable_coordinator_mode=config.enable_coordinator_mode,
        driver_class=NoAltScreenDriver,
    )
    app.run()


async def _run_prompt(config, permission_mode, hook_engine, prompt: str) -> None:
    from mewcode.agent import Agent
    from mewcode.client import create_client, resolve_context_window
    from mewcode.conversation import ConversationManager
    from mewcode.memory.instructions import load_instructions
    from mewcode.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from mewcode.tools import create_default_registry
    from mewcode.agents.loader import AgentLoader
    from mewcode.agents.task_manager import TaskManager
    from mewcode.agents.trace import TraceManager
    from mewcode.tools.agent_tool import AgentTool
    from mewcode.tools.impl.tool_search import ToolSearchTool
    from mewcode.teams.manager import TeamManager
    from mewcode.teams.models import BackendType
    from mewcode.tools.team_create import TeamCreateTool
    from mewcode.tools.team_delete import TeamDeleteTool
    from mewcode.worktree import WorktreeManager
    from mewcode.config import WorktreeConfig

    provider = config.providers[0]
    client = create_client(provider)
    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = os.getcwd()
    home = Path.home()

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=home / ".mewcode" / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".mewcode" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".mewcode" / "permissions.local.yaml",
        ),
        mode=permission_mode,
    )

    instructions = load_instructions(work_dir)
    registry = create_default_registry()
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
    )

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    agent_tool = AgentTool(
        agent_loader=agent_loader,
        task_manager=task_manager,
        trace_manager=trace_manager,
        parent_agent=agent,
        enable_fork=config.enable_fork,
        provider_config=provider,
        worktree_manager=wt_manager,
        team_manager=team_manager,
    )
    registry.register(agent_tool)
    registry.register(TeamCreateTool(
        team_manager=team_manager,
        parent_agent=agent,
        teammate_mode="in-process",
        is_interactive=False,
        enable_coordinator_mode=config.enable_coordinator_mode,
    ))
    registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    conv = ConversationManager()
    last_result = await agent.run_to_completion(prompt, conv)
    print(last_result, flush=True)

    if not team_manager._teams:
        return

    import sys
    for i in range(90):
        await asyncio.sleep(2)
        running = {k: not t.done() for k, t in task_manager._async_tasks.items()}
        completed_ids = [t.id for t in task_manager._tasks.values() if t.status != "running"]
        print(f"[poll {i}] running={running} completed={completed_ids} teams={list(team_manager._teams.keys())} queue_size={task_manager._notify_queue.qsize()}", file=sys.stderr, flush=True)
        notes = drain_notifications()
        if not notes:
            has_running = any(v for v in running.values())
            if not has_running:
                print(f"[poll {i}] no running tasks, breaking", file=sys.stderr, flush=True)
                break
            continue
        for note in notes:
            conv.add_system_reminder(note)
        last_result = await agent.run_to_completion(
            "Teammate notifications received. Process them and continue.", conv
        )
        print(last_result, flush=True)


if __name__ == "__main__":
    main()

