"""Permission rules — load allow/deny rules from settings.json.

P2b: Rule-based permission checking that runs before mode-based check.
Rules support glob matching for Bash commands: "Bash:git*" matches "git status".

规则系统是权限系统的扩展层（P2b），允许用户通过 settings.json 精细配置
哪些工具/命令应该自动允许或拒绝，无需逐次弹窗确认。

Rules format in ~/.claude/settings.json:
{
  "permissions": {
    "allow": ["Edit", "Bash:git*"],
    "deny": ["Agent"]
  }
}

规则优先级：deny > allow > 无匹配（fallthrough 到模式检查）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from cc.permissions.gate import PermissionDecision

logger = logging.getLogger(__name__)


@dataclass
class PermissionRules:
    """Allow/deny rules loaded from settings.json.

    allow: 允许规则列表，匹配的工具调用自动放行
    deny: 拒绝规则列表，匹配的工具调用直接拒绝（优先级高于 allow）
    """

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


def load_permission_rules(claude_dir: str | Path) -> PermissionRules:
    """Load permission rules from ~/.claude/settings.json.

    Returns empty rules if file doesn't exist or is malformed.

    采用防御式解析：文件不存在、JSON 格式错误、字段类型不对等情况
    都返回空规则（而非抛异常），确保规则系统的故障不影响正常使用。
    """
    settings_path = Path(claude_dir) / "settings.json"
    if not settings_path.is_file():
        return PermissionRules()

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load settings.json: %s", e)
        return PermissionRules()

    permissions = data.get("permissions", {})
    # 类型校验：permissions 必须是字典
    if not isinstance(permissions, dict):
        return PermissionRules()

    allow = permissions.get("allow", [])
    deny = permissions.get("deny", [])

    # 类型校验：allow 和 deny 必须是列表
    if not isinstance(allow, list):
        allow = []
    if not isinstance(deny, list):
        deny = []

    # 将列表元素强制转为字符串，防止用户配置了非字符串值
    return PermissionRules(
        allow=[str(r) for r in allow],
        deny=[str(r) for r in deny],
    )


def _matches_rule(rule: str, tool_name: str, tool_input: dict[str, object] | None) -> bool:
    """Check if a single rule matches the tool invocation.

    规则语法支持两种形式：
    - "ToolName" —— 精确匹配工具名，如 "Edit" 匹配所有 Edit 调用
    - "Bash:pattern" —— 匹配工具名 "Bash" 且 command 参数符合 glob 模式
      例如 "Bash:git*" 匹配 git status、git commit 等所有 git 命令

    使用 fnmatch 进行 glob 匹配（支持 *、?、[seq] 等通配符）。
    """
    if ":" in rule:
        # 冒号分隔的规则：工具名:命令模式
        rule_tool, rule_pattern = rule.split(":", 1)
        if tool_name != rule_tool:
            return False
        # 需要 tool_input 来获取具体命令
        if tool_input is None:
            return False
        # 从工具输入中提取 command 字段进行 glob 匹配
        command = str(tool_input.get("command", ""))
        return fnmatch(command, rule_pattern)

    # 简单规则：工具名精确匹配
    return tool_name == rule


def apply_rules(
    rules: PermissionRules,
    tool_name: str,
    tool_input: dict[str, object] | None = None,
) -> PermissionDecision | None:
    """Check tool invocation against permission rules.

    Returns:
        PermissionDecision.ALLOW if an allow rule matches,
        PermissionDecision.DENY if a deny rule matches,
        None if no rule matches (fall through to mode-based check).

    判定策略：deny 优先于 allow。
    这意味着如果同一个工具/命令同时匹配了 deny 和 allow 规则，
    deny 规则生效。这是安全设计：拒绝规则不应被允许规则覆盖。

    返回 None 表示没有任何规则匹配，调用方应 fallthrough 到模式检查。
    """
    # 先检查 deny 规则（deny 优先）
    for rule in rules.deny:
        if _matches_rule(rule, tool_name, tool_input):
            logger.info("Permission denied by rule '%s': %s", rule, tool_name)
            return PermissionDecision.DENY

    # 再检查 allow 规则
    for rule in rules.allow:
        if _matches_rule(rule, tool_name, tool_input):
            logger.info("Permission allowed by rule '%s': %s", rule, tool_name)
            return PermissionDecision.ALLOW

    # 无规则匹配 —— 返回 None 让调用方 fallthrough 到模式检查
    return None
