"""Token estimation utilities.

Corresponds to TS: services/tokenEstimation.ts.

=== 用途 ===

本模块提供快速（O(n)）的 token 数量估算，用于判断当前对话是否接近
上下文窗口上限，从而决定是否需要触发 auto-compact（自动压缩）。

为什么不用精确的 tokenizer？
  1. 精确 tokenize 需要加载 ~2MB 的 BPE 词表 + 编解码开销
  2. auto-compact 的阈值判断只需要粗略估计（误差 20% 以内即可）
  3. 估算成本几乎为零（只做 len(bytes) / ratio），可以在每轮循环中调用

估算精度:
  - 英文文本: ~4 bytes/token（BPE 平均值），误差约 10-20%
  - JSON 结构: ~2 bytes/token（大量标点和短 key），使用更小的 ratio
  - 中文文本: 实际约 2-3 bytes/token，用 4 bytes/token 会低估，
    但对 compact 阈值判断来说偏保守（宁可早压缩也不要 prompt_too_long）

=== 模块关系 ===

  被依赖: cc/core/query_loop.py（Phase 1 auto-compact 检查）
  相关: cc/compact/compact.py（should_auto_compact 使用本模块的估算结果）
"""

from __future__ import annotations

# 自然语言文本的平均 bytes/token 比（基于 Claude 的 BPE tokenizer 统计）
BYTES_PER_TOKEN = 4
# JSON/结构化数据的 bytes/token 比（标点、短 key 占比高，token 更密集）
JSON_BYTES_PER_TOKEN = 2


def estimate_tokens(text: str, bytes_per_token: int = BYTES_PER_TOKEN) -> int:
    """Rough token count estimation.

    Corresponds to TS: services/tokenEstimation.ts roughTokenCountEstimation().

    Args:
        text: Input text to estimate.
        bytes_per_token: Bytes per token ratio (default 4, use 2 for JSON).

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    # 使用 UTF-8 编码长度（而非 len(text)）来估算，
    # 因为 BPE tokenizer 基于字节操作，中文/emoji 占多个字节
    # max(1, ...) 确保非空文本至少返回 1 token
    return max(1, len(text.encode("utf-8")) // bytes_per_token)


async def count_tokens_api(
    client: object,
    messages: list[dict[str, object]],
    model: str = "claude-sonnet-4-20250514",
) -> int:
    """Count tokens using the Anthropic API's count_tokens endpoint.

    Corresponds to TS: services/tokenEstimation.ts countMessagesTokensWithAPI().

    与 estimate_tokens() 不同，这个函数调用 Anthropic API 获取精确 token 数。
    精确计数的代价是需要网络请求（~100ms 延迟），因此仅在需要精确值时使用
    （如计费展示），不用于 auto-compact 的高频阈值检查。
    """
    import anthropic

    if not isinstance(client, anthropic.AsyncAnthropic):
        raise TypeError("client must be an AsyncAnthropic instance")

    result = await client.messages.count_tokens(
        model=model,
        messages=messages,  # type: ignore[arg-type]
    )
    return result.input_tokens


def estimate_messages_tokens(messages: list[dict[str, object]]) -> int:
    """Estimate token count for a list of API messages.

    这是 query_loop Phase 1 中调用的主入口函数。
    它遍历所有消息，根据 content 类型选择不同的估算策略：
      - str content（纯文本）: 用 BYTES_PER_TOKEN=4
      - 非 str content（tool_result 等结构化数据）: 序列化为 JSON 后用 JSON_BYTES_PER_TOKEN=2

    注意: 这里不计算 system prompt 和 tool schemas 的 token，
    实际的 input_tokens 会比估算值高。但 auto-compact 的阈值设得足够保守
    （通常是 context_window 的 70%），所以这个低估是可以接受的。
    """
    import json

    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        else:
            # 结构化内容（如 tool_result 的 list[dict]）先序列化为 JSON 再估算
            # 使用 JSON_BYTES_PER_TOKEN 因为 JSON 结构的 token 密度更高
            total += estimate_tokens(json.dumps(content), bytes_per_token=JSON_BYTES_PER_TOKEN)
    return total
