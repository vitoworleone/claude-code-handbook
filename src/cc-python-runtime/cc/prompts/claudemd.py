"""CLAUDE.md file loading.

Corresponds to TS: utils/claudemd.ts.

本模块负责加载和合并多层级的 CLAUDE.md 配置文件。
CLAUDE.md 是用户向模型注入项目级/全局级自定义指令的主要方式。

搜索路径和优先级（从低到高，后加载的内容在拼接结果中排在后面，
因此在 system prompt 中出现得更晚，对模型的影响力更大）：

  1. ~/.claude/CLAUDE.md          — 用户全局指令（对所有项目生效）
  2. 从 cwd 向上到根目录的每层：
     - CLAUDE.md                  — 项目根目录或父级目录的指令
     - .claude/CLAUDE.md          — 隐藏目录中的指令
     - .claude/rules/*.md         — 规则目录中的所有 .md 文件（按文件名排序）
  3. CLAUDE.local.md（仅 cwd）    — 私有的本地指令（通常 .gitignore 中排除）

支持 @path 语法的文件包含指令，并有循环引用检测机制。
"""

from __future__ import annotations

import re
from pathlib import Path


def load_claude_md(cwd: str) -> str | None:
    """Load and merge CLAUDE.md files from the directory hierarchy.

    Corresponds to TS: utils/claudemd.ts loadClaudeMdFiles().

    Search order (lowest to highest priority):
    1. ~/.claude/CLAUDE.md (user global)
    2. From cwd up to root: CLAUDE.md, .claude/CLAUDE.md
    3. .claude/rules/*.md
    4. CLAUDE.local.md (private project-specific)

    Supports @path include directives with circular reference protection.
    """
    contents: list[str] = []

    # --- 第一层：用户全局 CLAUDE.md ---
    user_global = Path.home() / ".claude" / "CLAUDE.md"
    if user_global.is_file():
        text = _read_and_expand(user_global, set())
        if text:
            contents.append(text)

    # --- 第二层：从 cwd 向上遍历到根目录 ---
    # 先收集所有祖先目录，然后从根目录到 cwd 反向处理，
    # 这样越靠近 cwd 的文件越晚加载，优先级越高
    current = Path(cwd).resolve()
    ancestors: list[Path] = []
    while True:
        ancestors.append(current)
        parent = current.parent
        if parent == current:
            # 已到达文件系统根目录
            break
        current = parent

    # 从根到 cwd 的顺序处理（reversed），确保 cwd 的配置最后加载
    for ancestor in reversed(ancestors):
        # 每个祖先目录检查两个位置
        for candidate in [
            ancestor / "CLAUDE.md",
            ancestor / ".claude" / "CLAUDE.md",
        ]:
            # 排除已处理的 user_global，避免重复
            if candidate.is_file() and candidate != user_global:
                text = _read_and_expand(candidate, set())
                if text:
                    contents.append(text)

        # 每个祖先目录的 .claude/rules/ 下的所有 .md 规则文件
        # sorted() 确保加载顺序可预测（按文件名字母序）
        rules_dir = ancestor / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                if rule_file.is_file():
                    text = _read_and_expand(rule_file, set())
                    if text:
                        contents.append(text)

    # --- 第三层：cwd 下的 CLAUDE.local.md（最高优先级） ---
    # 这是私有的本地配置，不应提交到版本控制
    local_md = Path(cwd) / "CLAUDE.local.md"
    if local_md.is_file():
        text = _read_and_expand(local_md, set())
        if text:
            contents.append(text)

    if not contents:
        return None

    # 用双换行拼接所有来源的内容
    return "\n\n".join(contents)


def _read_and_expand(path: Path, seen: set[Path], max_depth: int = 10) -> str:
    """Read a file and expand @include directives.

    读取文件内容并递归展开 @path 包含指令。

    防御机制：
      - seen 集合追踪已访问路径，防止循环引用（A→B→A 的情况）
      - max_depth 限制嵌套深度，防止过深递归
      - 对读取错误（权限不足、编码错误等）静默返回空串

    Args:
        path: File to read.
        seen: Set of already-visited paths (circular reference protection).
        max_depth: Maximum include nesting depth.
    """
    resolved = path.resolve()
    # 循环引用检测：如果已访问过此路径，或嵌套层级耗尽，直接返回空
    if resolved in seen or max_depth <= 0:
        return ""

    seen.add(resolved)

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # 文件不可读时静默跳过，不影响其他文件的加载
        return ""

    # 移除 HTML 块注释（<!-- ... -->），这些通常用于在 CLAUDE.md 中写隐藏备注
    text = re.sub(r"<!--[\s\S]*?-->", "", text)

    # 展开 @include 指令：行首的 @path 会被替换为目标文件的内容
    def expand_include(match: re.Match[str]) -> str:
        include_path_str = match.group(1).strip()

        # 支持三种路径格式：
        if include_path_str.startswith("~/"):
            # ~/path → 用户主目录下的路径
            include_path = Path.home() / include_path_str[2:]
        elif include_path_str.startswith("./") or not include_path_str.startswith("/"):
            # ./path 或 relative/path → 相对于当前文件所在目录
            include_path = path.parent / include_path_str
        else:
            # /absolute/path → 绝对路径
            include_path = Path(include_path_str)

        if include_path.is_file():
            # 递归展开，传入 seen 的副本（每个分支独立追踪访问路径），
            # 深度减一防止无限递归
            return _read_and_expand(include_path, seen.copy(), max_depth - 1)
        return ""

    # 仅匹配行首的 @ 指令（re.MULTILINE），避免误匹配文本中的 @ 符号
    text = re.sub(r"^@(.+)$", expand_include, text, flags=re.MULTILINE)

    return text.strip()
