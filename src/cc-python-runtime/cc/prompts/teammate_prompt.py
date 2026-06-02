"""Teammate-specific system prompt addendum.

Appended to the full main agent system prompt for teammates. Explains
visibility constraints and communication requirements.

Corresponds to TS: utils/swarm/teammatePromptAddendum.ts
(TEAMMATE_SYSTEM_PROMPT_ADDENDUM).

本模块包含 teammate（队友 agent）模式的 prompt 附加段落。

与 coordinator（协调者）不同，teammate 是被协调者管理的 worker agent，
它们需要通过 SendMessage 工具来通信，普通文本回复对其他队友不可见。

TEAMMATE_SYSTEM_PROMPT_ADDENDUM 会被追加到主 agent 的 system prompt 之后，
build_teammate_prompt_addendum() 则进一步注入特定于该 teammate 的身份信息
（团队名、agent 名、通信规则等）。
"""

from __future__ import annotations

# 对应 TS: utils/swarm/teammatePromptAddendum.ts
# 核心约束：teammate 的文本输出对其他队友不可见，
# 必须使用 SendMessage 工具进行团队内通信
TEAMMATE_SYSTEM_PROMPT_ADDENDUM = """\
# Agent Teammate Communication

IMPORTANT: You are running as an agent in a team. To communicate with anyone on your team:
- Use the SendMessage tool with `to: "<name>"` to send messages to specific teammates
- Use the SendMessage tool with `to: "*"` sparingly for team-wide broadcasts

Just writing a response in text is not visible to others on your team - you MUST use the SendMessage tool.

The user interacts primarily with the team lead. Your work is coordinated through the task system and teammate messaging."""


def build_teammate_prompt_addendum(team_name: str, agent_name: str) -> str:
    """Build the prompt addendum for teammates.

    Corresponds to TS: utils/swarm/teammatePromptAddendum.ts usage.

    为特定 teammate 构建 prompt 附加段落，包含：
    - 基础通信规则（TEAMMATE_SYSTEM_PROMPT_ADDENDUM）
    - 该 teammate 的身份信息（名称、所属团队、agent ID）
    - 团队协作规则（向 team-lead 汇报、不修改他人正在编辑的文件）
    - 任务生命周期（接收任务→自主执行→汇报结果→等待下一个任务）

    为什么需要注入 agent_name 和 team_name？
    因为 teammate 需要知道自己的身份才能正确使用 SendMessage 工具，
    也需要知道团队名称才能找到团队配置文件。

    Args:
        team_name: Name of the team this teammate belongs to.
        agent_name: This teammate's display name.

    Returns:
        Prompt addendum string to append to the system prompt.
    """
    return f"""{TEAMMATE_SYSTEM_PROMPT_ADDENDUM}

# Your Identity
- You are **{agent_name}** in team **{team_name}**
- Your agent ID is `{agent_name}@{team_name}`

# Team Context
- You belong to team "{team_name}"
- The team lead coordinates all work — report results to team-lead via SendMessage
- Other teammates may be working in parallel on different tasks
- To discover teammates: the team config is at ~/.claude/teams/{team_name}/config.json

# Task Lifecycle
1. You receive an initial task prompt when spawned
2. Work autonomously — use tools as needed
3. When done, send your results to team-lead via SendMessage
4. If you need input, send a message to team-lead and wait
5. Being idle is normal — it means you are waiting for the next task

# Important Rules
- Always report your results via SendMessage — text responses alone are not visible
- Do not modify files that another teammate is actively working on
- When uncertain, ask team-lead for clarification via SendMessage
- Keep summaries concise in SendMessage — the full text goes in the message body"""
