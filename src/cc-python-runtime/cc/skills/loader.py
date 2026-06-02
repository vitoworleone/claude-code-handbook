"""Skills system — load and execute skill definitions.

技能（Skill）是用户自定义的 Markdown 文件，包含 prompt 模板。
当用户通过 slash command 触发技能时，其 prompt 内容会被注入到对话上下文中。

技能文件支持可选的 YAML frontmatter 来定义元数据（名称、描述、触发模式）。
没有 frontmatter 的文件会以文件名作为技能名，整个文件内容作为 prompt。

Corresponds to TS: skills/loadSkillsDir.ts + skills/bundled/.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A loaded skill definition."""

    # 技能名称，默认取自文件名（不含 .md 后缀），可通过 frontmatter 覆盖
    name: str
    # 技能的简短描述，在技能列表中展示
    description: str
    # 核心内容：注入到对话中的 prompt 文本
    prompt: str
    # 可选的触发模式（如正则表达式），用于自动匹配用户输入触发技能
    trigger: str = ""  # Optional trigger pattern
    # 技能文件的原始路径，用于调试和日志
    source_path: str = ""


def load_skills(cwd: str, claude_dir: Path | None = None) -> list[Skill]:
    """Load skill definitions from skill directories.

    Corresponds to TS: skills/loadSkillsDir.ts.

    Searches:
    1. ~/.claude/skills/      (用户级技能，跨项目共享)
    2. .claude/skills/ in project  (项目级技能，随项目代码分发)
    """
    skills: list[Skill] = []
    base_dir = claude_dir or (Path.home() / ".claude")

    # 按优先级顺序搜索技能目录：用户全局 -> 项目本地
    search_dirs = [
        base_dir / "skills",
        Path(cwd) / ".claude" / "skills",
    ]

    for skills_dir in search_dirs:
        if not skills_dir.is_dir():
            continue

        # 只加载 .md 文件，sorted 保证加载顺序的确定性（按文件名字典序）
        for skill_file in sorted(skills_dir.glob("*.md")):
            skill = _parse_skill_file(skill_file)
            if skill:
                skills.append(skill)

    return skills


def _parse_skill_file(path: Path) -> Skill | None:
    """Parse a skill markdown file with optional YAML frontmatter."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # 文件不可读时静默跳过，不影响其他技能的加载
        return None

    # 默认值：文件名（不含后缀）作为名称，整个文件内容作为 prompt
    name = path.stem
    description = ""
    trigger = ""
    prompt = text

    # 解析 YAML frontmatter（被 --- 包围的头部区域）
    # 格式示例：
    # ---
    # name: my-skill
    # description: A useful skill
    # trigger: /my-skill
    # ---
    # 实际的 prompt 内容...
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        # frontmatter 之后的内容才是实际的 prompt
        prompt = fm_match.group(2).strip()

        # 简单的逐行解析 YAML（不依赖 PyYAML 库）
        # 只支持最基础的 key: value 格式，足以覆盖技能元数据的需求
        for line in frontmatter.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("trigger:"):
                trigger = line.split(":", 1)[1].strip().strip("\"'")

    # prompt 为空的技能没有意义，跳过
    if not prompt.strip():
        return None

    return Skill(
        name=name,
        description=description or f"Skill: {name}",
        prompt=prompt,
        trigger=trigger,
        source_path=str(path),
    )


def get_skill_by_name(skills: list[Skill], name: str) -> Skill | None:
    """Find a skill by name (case-insensitive)."""
    # 大小写不敏感匹配，提升用户体验（用户不需要记住精确的大小写）
    name_lower = name.lower()
    for skill in skills:
        if skill.name.lower() == name_lower:
            return skill
    return None
