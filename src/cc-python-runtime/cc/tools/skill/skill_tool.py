"""SkillTool — load and return skill prompt text by name.

Corresponds to TS: tools/Skill (P5-6).

技能系统（Skill System）允许将复杂的领域知识封装为可复用的 prompt 片段。
模型通过调用该工具加载技能的指令文本，相当于"临时获得"某项专业能力。
例如 "commit" 技能会加载 Git 提交的最佳实践规则。
"""

from __future__ import annotations

import logging
from typing import Any

from cc.skills.loader import Skill, get_skill_by_name
from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

SKILL_TOOL_NAME = "Skill"


class SkillTool(Tool):
    """Load a skill by name and return its prompt text.

    Accepts a list of Skill objects in the constructor.
    The model can invoke this tool to activate a skill's instructions.

    工作流程：模型识别用户意图 → 调用 SkillTool 加载对应技能 →
    技能的 prompt 文本作为工具结果返回 → 模型按照技能指令执行。
    """

    def __init__(self, skills: list[Skill]) -> None:
        # 可用技能列表在启动时注入，通常从 skills/ 目录和用户配置中加载
        self._skills = skills

    def get_name(self) -> str:
        return SKILL_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=SKILL_TOOL_NAME,
            description="Load a skill by name and return its prompt text for the model to follow.",
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "The name of the skill to load",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments for the skill",
                    },
                },
                "required": ["skill"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 只读操作：查找并返回 prompt 文本，不修改任何状态
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        skill_name = tool_input.get("skill", "")
        if not skill_name:
            return ToolResult(content="Error: skill name is required", is_error=True)

        # 按名称查找技能，支持精确匹配和前缀匹配
        found = get_skill_by_name(self._skills, skill_name)
        if found is None:
            # 未找到时列出所有可用技能，帮助模型自纠错
            available = ", ".join(s.name for s in self._skills) if self._skills else "(none)"
            return ToolResult(
                content=f"Error: Skill '{skill_name}' not found. Available skills: {available}",
                is_error=True,
            )

        args = tool_input.get("args", "")
        prompt = found.prompt
        if args:
            # 将参数追加到 prompt 末尾，供技能指令中的模板引用
            prompt = f"{prompt}\n\nArguments: {args}"

        return ToolResult(content=prompt)
