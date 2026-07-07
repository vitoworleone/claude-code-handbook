# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import copy

from mewcode.conversation import ConversationManager, Message, ToolResultBlock

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = f"""{FORK_BOILERPLATE_TAG}
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告控制在 500 字以内，格式如下：

Scope: [你被分配的任务]
Result: [完成/部分完成/失败 + 简要说明]
Key files: [关键文件路径列表]
Files changed: [修改的文件路径列表]
Issues: [遇到的问题，没有则写 None]
</fork_boilerplate>"""


class ForkError(Exception):
    pass


def build_forked_messages(
    conversation: ConversationManager,
    task: str,
) -> ConversationManager:
    for msg in conversation.history:
        if FORK_BOILERPLATE_TAG in msg.content:
            raise ForkError(
                "Cannot fork from a forked agent. "
                "Fork nesting is not allowed."
            )

    fork_conv = ConversationManager()
    fork_conv.history = copy.deepcopy(conversation.history)
    fork_conv.env_injected = conversation.env_injected
    fork_conv.ltm_injected = conversation.ltm_injected


    if fork_conv.history:
        last = fork_conv.history[-1]
        if last.role == "assistant" and last.tool_uses:
            existing_result_ids = set()
            if len(fork_conv.history) >= 2:
                candidate = fork_conv.history[-1]
                if candidate.tool_results:
                    existing_result_ids = {
                        tr.tool_use_id for tr in candidate.tool_results
                    }

            pending = [
                tu
                for tu in last.tool_uses
                if tu.tool_use_id not in existing_result_ids
            ]
            if pending:
                placeholders = [
                    ToolResultBlock(
                        tool_use_id=tu.tool_use_id,
                        content="interrupted",
                        is_error=False,
                    )
                    for tu in pending
                ]
                fork_conv.history.append(
                    Message(
                        role="user",
                        content="",
                        tool_results=placeholders,
                    )
                )

    fork_conv.add_user_message(f"{FORK_BOILERPLATE}\n\n你的任务：\n{task}")
    return fork_conv

