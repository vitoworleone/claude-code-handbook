"""Permission gate — minimal tool permission checking.

P2a: Mode-based permission checking with non-interactive semantics.
Corresponds to TS: types/permissions.ts + hooks/toolPermission/.

Key design:
- PermissionMode controls strictness (bypass/acceptEdits/default)
- PermissionContext wraps mode + interactivity for each session
- New/unknown tools default to ASK (whitelist approach)
- Non-interactive contexts (--print, background, teammate) fail-fast on ASK

权限系统的核心设计理念是"白名单"模式：
只有明确列入白名单的工具才能自动执行，其余一律需要用户确认。
这样即使未来新增工具，也不会因为遗漏权限配置而导致意外执行。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cc.permissions.rules import PermissionRules

logger = logging.getLogger(__name__)

# 只读工具白名单 —— 这些工具不会修改文件系统，在所有模式下都自动允许
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep", "TaskGet", "TaskList", "ToolSearch", "Brief",
    "TaskCreate", "TaskUpdate",
})
# 编辑工具集 —— 会修改文件但不执行命令，在 ACCEPT_EDITS 模式下自动允许
EDIT_TOOLS = frozenset({
    "Edit", "Write", "NotebookEdit", "TodoWrite",
})


class PermissionMode(Enum):
    """Tool execution permission modes.

    三级权限模式，严格程度递增：
    - BYPASS: 完全跳过权限检查，所有工具自动允许（危险，仅限受信环境）
    - ACCEPT_EDITS: 读取和编辑操作自动允许，命令执行等需确认（推荐的交互模式）
    - DEFAULT: 仅读取操作自动允许，其余均需确认（最安全的默认模式）
    """
    BYPASS = "bypassPermissions"   # All tools auto-allowed
    ACCEPT_EDITS = "acceptEdits"   # Read + Edit auto-allowed, rest ASK
    DEFAULT = "default"            # Only read auto-allowed, rest ASK


class PermissionDecision(Enum):
    """Result of a permission check.

    ALLOW: 直接执行，无需用户确认
    ASK: 需要询问用户是否允许（交互模式下弹出提示，非交互模式下拒绝）
    DENY: 直接拒绝，不询问用户
    """
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


def check_permission(
    mode: PermissionMode,
    tool_name: str,
    tool_input: dict[str, object] | None = None,
) -> PermissionDecision:
    """Check if a tool should be allowed, denied, or needs user approval.

    Whitelist approach: only explicitly listed tools get ALLOW.
    Everything else defaults to ASK (safe for new/unknown tools).

    判定流程（按优先级）：
    1. BYPASS 模式 → 一律 ALLOW
    2. 只读工具 → ALLOW（所有模式下都安全）
    3. 编辑工具 + ACCEPT_EDITS 模式 → ALLOW
    4. 其余情况（Bash、Agent 等高危工具，或未知新工具）→ ASK
    """
    # BYPASS 模式跳过所有权限检查
    if mode == PermissionMode.BYPASS:
        return PermissionDecision.ALLOW

    # 只读工具在任何模式下都安全执行
    if tool_name in READ_ONLY_TOOLS:
        return PermissionDecision.ALLOW

    # 编辑工具在 ACCEPT_EDITS 模式下自动允许
    if tool_name in EDIT_TOOLS and mode == PermissionMode.ACCEPT_EDITS:
        return PermissionDecision.ALLOW

    # 所有未列入白名单的工具（Bash、Agent、WebFetch 等）→ 需要用户确认
    # 这包括未来可能新增的工具，确保安全默认行为
    return PermissionDecision.ASK


class PermissionContext:
    """Session-scoped permission context.

    Wraps mode + interactivity. Non-interactive contexts (--print,
    background agent, teammate) cannot prompt the user.

    PermissionContext 是每个会话的权限状态容器，封装了：
    - mode: 权限模式（决定自动允许哪些工具）
    - is_interactive: 是否可以向用户提问（非交互模式下 ASK → 拒绝）
    - rules: 自定义规则（优先于模式检查，允许精细控制）
    - _always_allow: 运行时累积的"始终允许"决定（用户选择 "a" 后记住）
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.ACCEPT_EDITS,
        is_interactive: bool = True,
        rules: PermissionRules | None = None,
    ) -> None:
        self.mode = mode
        self.is_interactive = is_interactive
        self.rules = rules
        # 存储用户选择"always allow"的工具名，避免每次都弹窗确认
        self._always_allow: set[str] = set()

    async def check(
        self,
        tool_name: str,
        tool_input: dict[str, object],
    ) -> bool:
        """Check permission and potentially prompt user.

        Returns True if allowed, False if denied.

        完整判定流程：
        1. 检查 _always_allow 缓存（用户曾选 "a" 的工具直接放行）
        2. 检查自定义规则（rules，P2b 扩展点）
        3. 执行模式检查（check_permission）
        4. 如果结果是 ASK：
           - 非交互模式 → 直接拒绝（fail-fast）
           - 交互模式 → 弹窗询问用户
        """
        # 已被用户标记为"始终允许"的工具，跳过所有检查
        if tool_name in self._always_allow:
            return True

        # P2b: 自定义规则优先于模式检查，允许用户通过 settings.json 精细控制
        if self.rules is not None:
            from cc.permissions.rules import apply_rules

            rules_decision = apply_rules(self.rules, tool_name, tool_input)
            if rules_decision is not None:
                # 规则明确匹配，直接按规则决定（不再走模式检查）
                if rules_decision == PermissionDecision.DENY:
                    return False
                if rules_decision == PermissionDecision.ALLOW:
                    return True

        # 模式检查：根据 PermissionMode 和工具白名单判定
        decision = check_permission(self.mode, tool_name, tool_input)

        if decision == PermissionDecision.ALLOW:
            return True

        if decision == PermissionDecision.ASK:
            if not self.is_interactive:
                # 非交互模式（如 --print、后台 agent）无法询问用户，直接拒绝
                # 这是安全设计：宁可拒绝也不能在无人监督时自动执行高危操作
                logger.info("Permission denied (non-interactive): %s", tool_name)
                return False

            # 交互模式：向用户展示工具信息并等待确认
            return await self._prompt_user(tool_name, tool_input)

        # DENY 决定：直接拒绝
        return False

    async def _prompt_user(
        self,
        tool_name: str,
        tool_input: dict[str, object],
    ) -> bool:
        """Prompt the user for approval in interactive mode.

        向用户展示工具名和输入摘要，提供三种选择：
        - y/yes: 本次允许
        - n/no: 本次拒绝
        - a/always: 本次允许，且记住该工具后续自动允许（存入 _always_allow）
        """
        from cc.ui.renderer import console

        # 截断过长的输入预览，避免刷屏
        from cc.ui.renderer import _shorten_paths

        input_preview = _shorten_paths(str(tool_input))
        if len(input_preview) > 200:
            input_preview = input_preview[:200] + "..."

        console.print(f"\n[yellow]Permission required: {tool_name}[/]")
        console.print(f"[dim]{input_preview}[/]")

        try:
            response = console.input("[bold]Allow? (y/n/a=always): [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # 用户中断输入时视为拒绝
            return False

        if response in ("a", "always"):
            # 记住选择，后续同类工具调用不再弹窗
            self._always_allow.add(tool_name)
            return True
        return response in ("y", "yes")
