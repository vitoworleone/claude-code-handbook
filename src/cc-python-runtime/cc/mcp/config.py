"""MCP server configuration loading.

从两个来源加载 MCP 服务器配置：
1. 用户级配置：~/.claude/settings.json 中的 mcpServers 字段
2. 项目级配置：项目根目录下的 .mcp.json 文件

项目级配置会追加在用户级配置之后，两者都有效。
如果同名服务器在两处都有定义，目前不做去重（都会被加载）。

Corresponds to TS: services/mcp/config.ts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server."""

    # 服务器的唯一标识名称（来自配置文件的 key）
    name: str
    # 传输协议类型："stdio"（子进程）| "sse"（Server-Sent Events）| "http"
    transport: str  # "stdio" | "sse" | "http"
    # stdio 模式下的启动命令（如 "npx"、"python"）
    command: str = ""
    # 传给 command 的命令行参数列表
    args: list[str] = field(default_factory=list)
    # 传给子进程的环境变量（仅 stdio 模式有效）
    env: dict[str, str] = field(default_factory=dict)
    # SSE/HTTP 模式下的服务器 URL
    url: str = ""


def load_mcp_configs(
    cwd: str,
    claude_dir: Path | None = None,
) -> list[McpServerConfig]:
    """Load MCP server configurations from settings and .mcp.json.

    Corresponds to TS: services/mcp/config.ts.

    Sources (in order):
    1. ~/.claude/settings.json -> mcpServers
    2. .mcp.json in project root
    """
    configs: list[McpServerConfig] = []

    # --- 来源 1：用户级全局设置 ---
    settings_path = (claude_dir or Path.home() / ".claude") / "settings.json"
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            mcp_servers = settings.get("mcpServers", {})
            for name, server_config in mcp_servers.items():
                cfg = _parse_server_config(name, server_config)
                if cfg:
                    configs.append(cfg)
        except (json.JSONDecodeError, OSError) as e:
            # 配置文件损坏时记录警告但不中断启动流程
            logger.warning("Failed to load MCP config from settings: %s", e)

    # --- 来源 2：项目级 .mcp.json ---
    # 这个文件通常随项目代码一起版本控制，定义项目专属的 MCP 工具
    mcp_json = Path(cwd) / ".mcp.json"
    if mcp_json.is_file():
        try:
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            mcp_servers = data.get("mcpServers", {})
            for name, server_config in mcp_servers.items():
                cfg = _parse_server_config(name, server_config)
                if cfg:
                    configs.append(cfg)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load .mcp.json: %s", e)

    return configs


def _parse_server_config(name: str, raw: dict[str, Any]) -> McpServerConfig | None:
    """Parse a single MCP server config entry."""
    # 兼容两种 key 名："type"（新版格式）和 "transport"（旧版格式）
    transport = raw.get("type", raw.get("transport", ""))

    # 只允许已知的传输类型，跳过未知类型避免运行时错误
    if transport not in ("stdio", "sse", "http"):
        logger.warning("Skipping MCP server '%s': unknown transport '%s'", name, transport)
        return None

    if transport == "stdio":
        # stdio 模式必须指定 command，这是启动子进程的入口
        command = raw.get("command", "")
        if not command:
            logger.warning("Skipping MCP server '%s': missing command for stdio transport", name)
            return None
        return McpServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=raw.get("args", []),
            env=raw.get("env", {}),
        )

    # SSE 或 HTTP 模式：需要指定服务器 URL
    url = raw.get("url", "")
    if not url:
        logger.warning("Skipping MCP server '%s': missing url for %s transport", name, transport)
        return None
    return McpServerConfig(
        name=name,
        transport=transport,
        url=url,
    )
