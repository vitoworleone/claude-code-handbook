# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandType


async def handle_plan(ctx: CommandContext) -> None:
    ctx.ui.set_plan_mode(True)
    ctx.ui.add_system_message("已切换到 Plan 模式 — 只读，禁止写入和命令执行")
    if ctx.args:
        ctx.ui.send_user_message(ctx.args)


PLAN_COMMAND = Command(
    name="plan",
    aliases=["p"],
    description="切换到 Plan 模式",
    usage="/plan [任务描述]",
    type=CommandType.LOCAL_UI,
    handler=handle_plan,
)

