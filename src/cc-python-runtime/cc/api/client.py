"""Anthropic API client factory.

Corresponds to TS: services/api/client.ts.

=== 架构角色 ===

本模块提供 Anthropic SDK 客户端的创建逻辑，是整个系统中唯一实例化
AsyncAnthropic 的地方。所有需要 API 客户端的模块都通过依赖注入获取，
而非自行创建，以确保配置（API key、base_url）的统一管理。

=== 模块关系 ===

  被依赖: cc/main.py（在 _build_engine 中调用 create_client 创建客户端）
  产出: anthropic.AsyncAnthropic 实例 → 注入 QueryEngine → 传入 stream_response
"""

from __future__ import annotations

import os

import anthropic

from cc.utils.errors import ConfigError


def create_client(
    api_key: str | None = None,
    base_url: str | None = None,
) -> anthropic.AsyncAnthropic:
    """Create an async Anthropic client.

    Corresponds to TS: services/api/client.ts client creation.

    Args:
        api_key: API key. Falls back to ANTHROPIC_API_KEY env var.
        base_url: Optional base URL override.

    Returns:
        Configured AsyncAnthropic client.

    Raises:
        ConfigError: If no API key is available.
    """
    # 优先使用显式传入的 api_key，其次读环境变量
    # 这个顺序与 Anthropic SDK 的默认行为一致，但我们显式处理
    # 是为了在缺少 key 时给出更友好的错误信息（而非 SDK 的模糊报错）
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise ConfigError(
            "No API key found. Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
        )

    # base_url 优先级：显式参数 > 环境变量 > SDK 默认值 (https://api.anthropic.com)
    # 支持阿里云百炼等 Anthropic API 兼容服务
    resolved_base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
    if resolved_base_url:
        return anthropic.AsyncAnthropic(api_key=resolved_key, base_url=resolved_base_url)
    return anthropic.AsyncAnthropic(api_key=resolved_key)
