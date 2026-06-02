# 缓存与上下文窗口优化机制完整调研报告

> 生成时间：2026-05-17  
> 研究对象：ClaudeCode-Runtime 的上下文管理、缓存、压缩、session/memory、MCP schema 加载机制  
> 原始报告：`[internal research report]`  
> 原始备份：`[internal research report]`  

---

## 目录

1. [执行摘要](#一执行摘要)
2. [先分清四种“缓存”](#二先分清四种缓存)
3. [权威论文谱系](#三权威论文谱系)
4. [Anthropic 官方机制](#四anthropic-官方机制)
5. [ClaudeCode-Runtime 当前实现](#五claudecode-runtime-当前实现)
6. [源码级关键链路](#六源码级关键链路)
7. [现有报告需要修正的判断](#七现有报告需要修正的判断)
8. [缺失能力与落地设计](#八缺失能力与落地设计)
9. [测试矩阵](#九测试矩阵)
10. [推荐改造优先级](#十推荐改造优先级)
11. [结论](#十一结论)
12. [参考资料](#十二参考资料)

---

## 一、执行摘要

原报告已经正确抓住了 ClaudeCode-Runtime 的主线：它通过 token 估算、auto-compact、reactive compact、manual compact、session 恢复和 usage 统计来避免长会话直接撞上上下文窗口。

但原报告偏工程实现总结，缺了两层内容：

1. **权威论文谱系**：没有把缓存、长上下文、压缩、外部记忆放进 Transformer 以来的研究脉络里。
2. **机制边界澄清**：把 Prompt Caching、KV Cache、Session Cache、Context Compaction、Memory/RAG 容易混在一起讲。

本次补充后的核心判断是：

```text
ClaudeCode-Runtime 目前实现的是“客户端上下文治理”：
  - 估算当前 transcript 的 token 量
  - 超阈值时把旧 transcript 摘要化
  - 遇到 prompt_too_long 时做一次 reactive compact
  - 用 JSONL session 保存完整历史
  - 用 memory extraction 提取长期信息

但它尚未实现“API 级缓存与服务端上下文治理”：
  - 没有向 Anthropic 请求传 cache_control
  - 没有使用 server-side compaction compact_20260112
  - 没有使用 clear_tool_uses_20250919 / clear_thinking_20251015
  - 没有使用 MCP schema defer / lazy tool schema loading
  - 没有精确 token counting 校准策略
```

如果从架构师角度看，这个模块不是单一“缓存功能”，而是一组跨层机制：

```text
模型结构层：Long Context Transformer / Transformer-XL / Compressive Transformer
服务推理层：KV cache / FlashAttention / PagedAttention / StreamingLLM / KIVI
API 前缀层：Anthropic Prompt Caching / Prompt Cache
运行时上下文层：client-side compact / server-side compact / context editing
外部记忆层：RAG / RETRO / MemGPT / session memory / MCP
```

ClaudeCode-Runtime 能直接改的是后三层：API 前缀层、运行时上下文层、外部记忆层。服务推理层的 KV cache 优化通常在模型服务端，客户端 harness 只能通过减少重复前缀、减少工具 schema、减少无效上下文间接受益。

---

## 二、先分清四种“缓存”

很多关于上下文窗口的讨论会把不同层级的 cache 混起来。这里先拆开。

| 名称 | 所在层 | 保存什么 | 谁控制 | ClaudeCode-Runtime 是否能直接实现 |
|---|---|---|---|---|
| KV Cache | 模型服务推理层 | 每层 attention 的 key/value 激活 | 模型服务端 | 不能直接控制 |
| Prompt Caching | API 前缀层 | 稳定 prompt 前缀的处理结果 | Anthropic API + 客户端 cache_control | 可以实现请求标记 |
| Context Compaction | agent runtime 层 | 旧 transcript 的摘要 | Runtime / API context management | 已实现客户端版，未实现服务端版 |
| Session / Memory Cache | 本地持久层 | 完整 transcript、任务状态、长期记忆 | ClaudeCode-Runtime | 已部分实现 |

### 2.1 KV Cache

KV cache 是推理时为了避免每生成一个 token 都重新计算所有历史 token attention 而保存的 key/value 激活。它是模型服务系统里的内存优化问题。

相关论文包括：

- FlashAttention：通过 IO-aware tiling 降低 attention 读写成本。
- PagedAttention/vLLM：用类似虚拟内存分页的方式管理 KV cache。
- StreamingLLM：保留 attention sink token 加滑动窗口以支持稳定流式推理。
- H2O、KIVI、SnapKV：分别代表 KV eviction、KV quantization、KV compression。

对 ClaudeCode-Runtime 来说，KV cache 是背景知识，不是它能直接 patch 的 Python runtime 能力。

### 2.2 Prompt Caching

Prompt caching 是 Anthropic API 暴露给客户端的前缀复用能力。客户端通过 `cache_control` 标记稳定前缀，API 会在后续请求中复用已经缓存的 prompt prefix。

它和 KV cache 的区别是：

```text
KV cache:
  发生在模型服务内部，客户端一般不可见。

Prompt caching:
  发生在 API 请求边界，客户端通过 cache_control 参与控制。
```

ClaudeCode-Runtime 当前只读取了 cache usage 字段，但没有真正写入 `cache_control`。

### 2.3 Context Compaction

Context compaction 是把旧对话从“高分辨率逐条消息”变成“低分辨率摘要”。这和 Compressive Transformer 的思想有相似性：旧信息不是完全删除，而是压缩到更便宜的表示。

ClaudeCode-Runtime 已经实现了客户端版：

```text
old messages -> compact summary -> CompactBoundaryMessage
recent messages -> 原文保留
```

但它尚未使用 Anthropic 官方的 server-side compaction。

### 2.4 Session / Memory

Session 是完整历史的本地持久化。Memory 是长期偏好、项目事实或跨 session 知识的提取和注入。

关键边界是：

```text
存下来了 ≠ 模型当前知道。
只有被放入 messages / system prompt / tool result / notification / compact summary 的内容，才是当前模型可见上下文。
```

这也是理解 session、memory、compact 的核心。

---

## 三、权威论文谱系

### 3.1 从 Transformer 到长上下文

| 论文 | 核心贡献 | 和本项目的关系 |
|---|---|---|
| Attention Is All You Need | 建立 Transformer 与 full self-attention 基础 | 上下文窗口问题的源头：attention 成本随序列变长快速增长 |
| Transformer-XL | segment-level recurrence + relative position | “跨段记忆”的代表，说明历史可以用 memory 形式跨窗口传递 |
| Compressive Transformer | 把旧 memory 再压缩成 compressed memory | 和 compact summary 的思想最接近 |
| Longformer / BigBird | sparse attention 支持长文档 | 属于模型结构优化，runtime 不能直接实现 |
| Lost in the Middle | 长上下文模型对中间位置利用不稳定 | 提醒 runtime 不能只追求窗口变大，还要治理信息位置和摘要质量 |

这条谱系给 ClaudeCode-Runtime 的启发是：客户端 runtime 不改变模型 attention 结构，所以不能靠 Python 代码把模型变成长上下文模型；它能做的是：

- 减少无效 token。
- 把稳定前缀缓存起来。
- 把旧历史摘要化。
- 把长期信息移入 memory/retrieval。
- 把关键状态放在模型更容易使用的位置。

### 3.2 服务端推理优化：KV cache 与 attention 系统

| 论文 | 机制 | 适用层级 |
|---|---|---|
| FlashAttention | IO-aware exact attention | 模型内核 / serving |
| PagedAttention / vLLM | KV cache 分页管理 | LLM serving |
| StreamingLLM | attention sinks + sliding window | streaming inference |
| H2O | heavy-hitter KV eviction | KV cache eviction |
| KIVI | 2bit asymmetric KV quantization | KV cache compression |
| SnapKV | query/head-aware KV selection | KV cache compression |

这些论文很权威，但要放在正确位置：它们解释的是服务端为什么能更便宜地处理长上下文，而不是 ClaudeCode-Runtime 当前 Python 代码能直接操作 KV cache。

本项目可借鉴的是术语和指标：

- 首 token latency。
- decode throughput。
- KV memory pressure。
- cache hit ratio。
- long-context quality degradation。

### 3.3 API 前缀复用：Prompt Cache

相关论文和文档：

- Prompt Cache: Modular Attention Reuse for Low-Latency Inference。
- Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks。
- Anthropic Prompt Caching 官方文档。

这层最适合本项目落地。因为 ClaudeCode-Runtime 已经把 system prompt 组织成多个 section，并且注释中明确说了这是为了未来加 cache_control。

### 3.4 压缩与记忆：Compact、LLMLingua、MemGPT

| 方向 | 代表 | 对 runtime 的启发 |
|---|---|---|
| 摘要压缩 | Compressive Transformer / compact summary | 旧上下文可以降分辨率保留 |
| Prompt compression | LLMLingua | 不是所有自然语言都要原样保留，但要评估语义损失 |
| 分层记忆 | MemGPT | active context 是工作内存，session/memory/retrieval 是外存 |

ClaudeCode-Runtime 目前的 compact 更像“客户端摘要压缩”，memory extraction 更像“轻量长期记忆提取”。还缺的是明确的层级策略：

```text
active window -> compact summary -> session store -> memory index -> retrieval
```

### 3.5 检索式外部记忆：RAG 与 RETRO

RAG 和 RETRO 的价值在于提醒我们：不必把所有历史都塞进上下文窗口。很多内容应该放到外部索引里，需要时再检索。

对 ClaudeCode-Runtime 的落地含义：

- session JSONL 可以作为检索语料。
- compact summary 可以作为低成本索引对象。
- memory extraction 应该带 provenance，能追溯到原始消息或文件。
- MCP 工具结果如果很长，应该先摘要或索引，而不是永久留在 active context。

---

## 四、Anthropic 官方机制

### 4.1 Prompt Caching

Anthropic 官方文档说明，prompt caching 通过 `cache_control` 复用 prompt prefix。缓存范围按请求结构顺序构成：

```text
tools -> system -> messages
```

这对 coding agent 很关键，因为工具 schema 和 system prompt 往往很大，而且多轮会话里大部分内容不变。

官方文档还给出几个重要约束：

- 默认 5 分钟 TTL。
- 1 小时 TTL 可用但写入成本更高。
- cache read token 按更低价格计费。
- cache breakpoint 放在动态内容后面会导致 cache miss。
- 最多可以有多个显式 breakpoint。
- 自动 caching 和显式 block-level caching 可以组合。

当前 runtime 的问题是：它已经读取 cache usage，但没有创建 cache。

### 4.2 Server-side Compaction

Anthropic 官方 compaction 文档提供 `compact_20260112`：

```python
context_management={
    "edits": [{"type": "compact_20260112"}]
}
```

它和本项目的 `compact_messages()` 区别在于：

| 维度 | client-side compact | server-side compact |
|---|---|---|
| 执行位置 | 本地 runtime 调用模型生成摘要 | Anthropic API 内部 context management |
| 可移植性 | 高，任何模型 API 都可模拟 | 低，绑定 Anthropic beta |
| 本地 transcript | 被替换成 CompactBoundaryMessage | 客户端可继续保留完整历史 |
| 使用成本 | 额外一次模型调用 | API 返回 iterations 统计 |
| 控制力 | runtime 可自定义 prompt | provider 管理 |

本项目保留 client-side compact 是合理的，但可以把 server-side compact 做成可选策略。

### 4.3 Context Editing

Anthropic context editing 文档提供：

- `clear_tool_uses_20250919`
- `clear_thinking_20251015`

这对 coding agent 很重要，因为工具结果经常很长，例如文件内容、搜索结果、命令输出。旧工具结果在模型已经处理过之后，不一定需要永久留在 active context。

但它有风险：

```text
清掉 tool result 可以省上下文；
但如果后续 debug 还需要原始命令输出，模型会失去证据。
```

所以合理策略不是“全部清理”，而是：

1. 先保存关键事实到 memory 或 compact summary。
2. 对可重新获取的工具结果做清理。
3. 对不可重现的结果保留更久。
4. 清理事件本身要进入 transcript 或 telemetry。

### 4.4 Token Counting

Anthropic token counting endpoint 能在发送正式消息前估算 input token。官方也说明 token count 仍是估计值，但比本地 bytes/token 更贴近 API 实际。

ClaudeCode-Runtime 现在有两个计数路径：

- `estimate_messages_tokens()`：本地快估，用于每轮高频判断。
- `count_tokens_api()`：Anthropic API 精确计数函数已写，但 query_loop 没有使用。

更好的设计是双层：

```text
每轮：本地快估。
接近阈值：调用 count_tokens API 校准。
校准结果：用于 compact 触发、模型路由、cache policy。
```

---

## 五、ClaudeCode-Runtime 当前实现

当前实现可以概括为四层防御：

```text
Layer 1: estimate_messages_tokens()
  快速估算当前 messages 大小。

Layer 2: proactive auto-compact
  达到 context_window - AUTO_COMPACT_BUFFER 时压缩旧消息。

Layer 3: reactive compact
  API 报 prompt_too_long 时尝试一次压缩恢复。

Layer 4: session + memory
  JSONL 保存完整/压缩后 transcript，后台提取长期 memory。
```

它已经不是一个薄 wrapper，而是有明确上下文生命周期的 runtime。

但是从权威论文和官方文档对照，它还缺 5 个更高级的能力：

1. Prompt caching cache_control。
2. Server-side compaction。
3. Context editing for tool results and thinking blocks。
4. MCP schema defer / lazy loading。
5. Retrieval-backed memory and context regression tests。

---

## 六、源码级关键链路

### 6.1 query_loop 中的 auto-compact

核心入口在 `cc/core/query_loop.py`。每轮请求前先 normalize，再取 tool schema，再估算 messages token。

```python
api_messages = normalize_messages_for_api(messages)
tool_schemas = tools.get_api_schemas()

estimated_tokens = estimate_messages_tokens(api_messages)
if (
    should_auto_compact(estimated_tokens, context_window, compact_consecutive_failures)
    and auto_compact_fn is not None
):
    compacted = await compact_messages(messages, auto_compact_fn)
    if len(compacted) < len(messages):
        messages.clear()
        messages.extend(compacted)
        yield CompactOccurred(summary_preview="Context auto-compacted")
        api_messages = normalize_messages_for_api(messages)
```

这说明本项目的上下文压缩是 transcript 原地替换，而不是只影响本次请求。

### 6.2 compact_messages 的压缩策略

`cc/compact/compact.py` 中的核心常量：

```python
AUTO_COMPACT_BUFFER = 13_000
MAX_CONSECUTIVE_FAILURES = 3
POST_COMPACT_KEEP_TURNS = 4
```

压缩逻辑：

```python
keep_count = POST_COMPACT_KEEP_TURNS * 2
old_messages = messages[:-keep_count]
recent_messages = messages[-keep_count:]

conversation_text = _messages_to_text(old_messages)

summary_messages = [
    UserMessage(content=f"Summarize this conversation:\n\n{conversation_text}"),
]

boundary = CompactBoundaryMessage(summary=summary)
return [boundary, *recent_messages]
```

这和 Compressive Transformer 的思想相似：最近历史保留高分辨率，旧历史降采样为摘要。但这里的压缩质量完全依赖摘要 prompt 和模型输出，没有自动验证。

### 6.3 token_estimation 的快估逻辑

`cc/api/token_estimation.py`：

```python
BYTES_PER_TOKEN = 4
JSON_BYTES_PER_TOKEN = 2

def estimate_tokens(text: str, bytes_per_token: int = BYTES_PER_TOKEN) -> int:
    if not text:
        return 0
    return max(1, len(text.encode("utf-8")) // bytes_per_token)
```

消息级估算：

```python
for msg in messages:
    content = msg.get("content", "")
    if isinstance(content, str):
        total += estimate_tokens(content)
    else:
        total += estimate_tokens(
            json.dumps(content),
            bytes_per_token=JSON_BYTES_PER_TOKEN,
        )
```

这个设计有工程合理性：每轮都跑，必须快。但缺点也明显：

- 不计算 system prompt。
- 不计算 tool schema。
- CJK 文本误差可能较大。
- PDF、多模态、tool result 结构可能估不准。

因此它适合做粗筛，不适合做临界点判断的唯一依据。

### 6.4 claude.py 已读取 cache usage

`cc/api/claude.py` 在 `message_start` 中读取 usage：

```python
usage.input_tokens = getattr(msg_usage, "input_tokens", 0)
usage.cache_creation_input_tokens = getattr(
    msg_usage, "cache_creation_input_tokens", 0
)
usage.cache_read_input_tokens = getattr(
    msg_usage, "cache_read_input_tokens", 0
)
```

这说明 runtime 已经具备“观察 prompt caching 效果”的数据结构。但请求参数里没有 `cache_control`：

```python
params: dict[str, Any] = {
    "model": model,
    "max_tokens": max_tokens,
    "messages": messages,
    "system": system,
}

if tools:
    params["tools"] = tools
```

所以当前状态应描述为：

```text
cache accounting ready,
cache creation not implemented.
```

### 6.5 prompt builder 已经为 cache_control 预留结构

`cc/prompts/builder.py` 返回的是 `list[str]` 而不是单个字符串，注释也直接说明了用途：

```python
def build_system_prompt(...) -> list[str]:
    sections: list[str | None] = [
        get_intro_section(),
        get_system_section(),
        get_doing_tasks_section(),
        get_actions_section(),
        get_using_tools_section(),
        get_tone_style_section(),
        get_output_efficiency_section(),
        compute_env_info(cwd, model),
        SUMMARIZE_TOOL_RESULTS,
    ]
    ...
    return [s for s in sections if s is not None]
```

报告中应该强调：这不是随便返回 list，而是为了未来能够把稳定段落变成 Anthropic system content blocks：

```python
[
    {
        "type": "text",
        "text": static_prompt,
        "cache_control": {"type": "ephemeral"}
    },
    {
        "type": "text",
        "text": dynamic_env_and_memory
    }
]
```

### 6.6 session 是持久化，不是当前上下文

`cc/session/storage.py` 保存完整消息：

```python
with path.open("w", encoding="utf-8") as f:
    for msg in messages:
        record = _message_to_record(msg)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

compact boundary 也会被持久化：

```python
if isinstance(msg, CompactBoundaryMessage):
    return {
        "type": "compact_boundary",
        "summary": msg.summary,
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
    }
```

所以 session 能恢复历史状态，但恢复后模型仍只知道 runtime 注入到 `messages/system/tools` 的内容。

### 6.7 MCP schema 当前是 eager loading

`cc/mcp/client.py` 连接 MCP server 后立即 `list_tools()` 并注册所有工具：

```python
tools_result = await session.list_tools()
for tool in tools_result.tools:
    proxy = McpToolProxy(
        server_name=config.name,
        tool_name=tool.name,
        description=tool.description or "",
        input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {"type": "object"},
        session=session,
    )
    registry.register(proxy)
```

这会带来工具多时的 schema tax。Anthropic SDK 有 defer/loading 相关能力，但当前 runtime 没接入。

---

## 七、现有报告需要修正的判断

### 7.1 “Token 估算偏保守”的描述要更精确

原报告说估算对中文误差更大，并称“偏保守”。但从代码看：

```python
len(text.encode("utf-8")) // 4
```

中文 UTF-8 通常 3 bytes/字。实际 tokenizer 对中文可能接近 1 字 1 token 或更复杂，因此除以 4 未必总是保守，可能低估。更准确表述是：

```text
这是低成本粗估，不保证保守；
尤其不包含 system prompt 和 tool schema，因此整体更可能低估实际请求 token。
```

### 7.2 “默认 200K”需要补充模型差异

原报告把 200K 当作默认窗口。Anthropic 官方 context windows 文档显示，不同 Claude 模型存在 200K 和 1M 等差异，且新模型有 context awareness。

所以报告应改为：

```text
Python Runtime 代码默认值是 200K；
但真实模型窗口应来自 model capability metadata，而不是硬编码。
```

### 7.3 “Prompt Caching 未实现”的结论正确，但要补状态分层

更完整的判断：

```text
已实现：
  - Usage dataclass 有 cache token 字段
  - claude.py 会读取 cache_creation_input_tokens / cache_read_input_tokens
  - builder.py 的 list[str] prompt section 设计适合 cache_control

未实现：
  - API 请求没有 cache_control
  - system/tools/messages 没有 block-level breakpoint
  - 没有 cache policy
  - 没有 cache telemetry report
```

### 7.4 “MCP Schema Defer 未实现”需要连接到 tool tax

原报告说未实现 MCP schema defer 是对的，但应该补原因：

```text
MCP 工具越多，tools schema 越大；
tools 在 Anthropic prompt caching 顺序中位于 system/messages 之前；
所以 eager tool schema 不仅占 context，也影响 cache prefix 结构。
```

---

## 八、缺失能力与落地设计

### 8.1 PromptCachePolicy

建议新增一个策略对象：

```python
class PromptCachePolicy:
    enabled: bool = False
    mode: Literal["automatic", "static_system", "tools_and_system"] = "static_system"
    ttl: Literal["5m", "1h"] = "5m"
```

API adapter 里根据策略构造 Anthropic 请求：

```python
if prompt_cache_policy.enabled and prompt_cache_policy.mode == "automatic":
    params["cache_control"] = {"type": "ephemeral"}
```

对于显式 system block caching：

```python
params["system"] = [
    {
        "type": "text",
        "text": stable_system_text,
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
    },
    {
        "type": "text",
        "text": dynamic_system_text,
    },
]
```

落地要点：

- 静态段落放前面。
- 环境、日期、cwd、memory、CLAUDE.md 等动态段落放后面。
- cache breakpoint 不要放在 timestamp、当前用户输入、动态工具结果之后。
- 记录 cache read/write tokens。

### 8.2 ContextManagementPolicy

建议新增可选 Anthropic 专属策略：

```python
class ContextManagementPolicy:
    server_side_compact: bool = False
    clear_tool_uses: bool = False
    clear_thinking: bool = False
    trigger_input_tokens: int = 100_000
    keep_recent_tool_uses: int = 3
```

请求参数：

```python
params["context_management"] = {
    "edits": [
        {
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": 100_000},
            "keep": {"type": "tool_uses", "value": 3},
        }
    ]
}
```

注意它应该是 feature flag，因为这绑定 Anthropic beta。

### 8.3 Token Counting 校准

当前本地估算很快，但临界时不可靠。建议：

```text
if estimated_tokens > context_window * 0.75:
    exact_tokens = await count_tokens_api(...)
    use exact_tokens for compact decision
else:
    use estimated_tokens
```

这可以避免：

- 过早 compact，损失上下文质量。
- 过晚 compact，触发 prompt_too_long。
- CJK/PDF/tool schema 导致估算偏差。

### 8.4 MCP Schema Defer

当前是：

```text
connect MCP server -> list all tools -> register all schemas -> 每轮都可能进入 tools schema
```

建议变成：

```text
connect MCP server -> register ToolSearch / ToolProxy stub
用户或模型需要某类工具 -> lazy load matching schema -> register concrete tool
```

这样能减少工具 schema tax，并提高 prompt cache 稳定性。

### 8.5 Compact Summary 质量测试

需要新增测试集，而不是只看 compact 有没有执行。

建议测试：

1. 旧消息中给出文件路径，compact 后模型还能说出路径。
2. 旧消息中给出决策原因，compact 后模型还能说明原因。
3. 旧消息中有命令输出错误，compact 后模型还能定位错误。
4. compact 前后继续任务，最终补丁不偏离原目标。
5. 中文长会话触发 compact 时不会过晚。

---

## 九、测试矩阵

| 测试目标 | 方法 | 期望 |
|---|---|---|
| Prompt caching 启用 | 两次相同静态 system prompt 请求 | 第二次 `cache_read_input_tokens > 0` |
| 动态内容破坏 cache | 把 timestamp 放在 breakpoint 前 | cache hit 下降或为 0 |
| system block caching | 静态 system + 动态 env 分 block | 静态段落可复用，动态段落不影响前缀 |
| token 估算校准 | 对同一 messages 调 estimate 与 count_tokens | 记录误差并调整阈值 |
| compact faithfulness | 超长 transcript 触发 compact | 摘要保留文件路径、决策、未完成任务 |
| reactive compact | mock prompt_too_long | 只尝试一次 reactive compact |
| context editing | mock clear_tool_uses response | 本地 transcript 保持完整，API response 有 applied_edits |
| MCP schema defer | 大量 MCP tools | 初始 tools schema token 显著下降 |
| session 恢复 | compact 后保存再恢复 | CompactBoundaryMessage 被正确恢复 |
| memory tiering | 长任务跨 session | 关键事实从 memory 注入，而非全量 replay |

---

## 十、推荐改造优先级

### P0：补 Prompt Caching

收益最大，代码侵入较小，且现有结构已经准备好了。

目标文件：

- `cc/api/claude.py`
- `cc/prompts/builder.py`
- `cc/models/messages.py`
- `cc/core/query_engine.py`

验收标准：

```text
开启 prompt caching 后：
  - 第一次请求出现 cache_creation_input_tokens
  - 第二次稳定前缀请求出现 cache_read_input_tokens
  - 动态 env/memory 不破坏静态 system prefix
```

### P1：补 token counting 校准

目标：

- 不替代快估。
- 只在接近阈值时调用 API count_tokens。
- 把 tool schema 和 system prompt 纳入临界判断。

### P2：补 server-side context management feature flag

目标：

- 仍保留本地 compact。
- Anthropic 模型可选启用 `compact_20260112`。
- 工具密集工作流可选启用 `clear_tool_uses_20250919`。

### P3：补 MCP schema defer

目标：

- 大量 MCP tools 不再一次性塞进 prompt。
- 引入 tool search / lazy schema registry。

### P4：补 retrieval-backed memory

目标：

- session JSONL、compact summary、memory files 可以被检索。
- memory entry 有 provenance。

---

## 十一、结论

原报告的工程判断大体正确，但不够完整。补充权威论文和官方文档后，可以得到更清晰的架构结论：

```text
ClaudeCode-Runtime 当前实现的是客户端上下文治理：
  token estimate -> proactive compact -> reactive compact -> session save -> memory extraction

它尚未实现 API 级上下文效率能力：
  prompt caching -> server-side compact -> context editing -> MCP schema defer

它也尚未形成完整的外部记忆体系：
  session store -> compact summary -> memory index -> retrieval -> provenance
```

从架构学习角度，这个模块非常适合拿来理解 agent runtime 的核心问题：

- 模型上下文是有限资源。
- session 保存不等于模型可见。
- compact 是有损压缩，需要测试。
- prompt caching 是前缀工程，不是魔法。
- tool schema 是真实 token 成本。
- 长上下文不等于长记忆，仍会有注意力稀释和位置偏差。

下一步最值得做的不是继续写概念文档，而是实现一个小闭环：

```text
PromptCachePolicy + cache usage telemetry + 两轮 cache hit 测试
```

这个闭环能把报告里的“已有结构但未实现 cache_control”变成可验证能力。

---

## 十二、参考资料

### 12.1 权威论文

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
- [Transformer-XL: Attentive Language Models Beyond a Fixed-Length Context](https://arxiv.org/abs/1901.02860)
- [Compressive Transformers for Long-Range Sequence Modelling](https://arxiv.org/abs/1911.05507)
- [Longformer: The Long-Document Transformer](https://arxiv.org/abs/2004.05150)
- [BigBird: Transformers for Longer Sequences](https://arxiv.org/abs/2007.14062)
- [Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172)
- [FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness](https://arxiv.org/abs/2205.14135)
- [Efficient Memory Management for Large Language Model Serving with PagedAttention](https://arxiv.org/abs/2309.06180)
- [Efficient Streaming Language Models with Attention Sinks](https://arxiv.org/abs/2309.17453)
- [H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models](https://arxiv.org/abs/2306.14048)
- [KIVI: A Tuning-Free Asymmetric 2bit Quantization for KV Cache](https://arxiv.org/abs/2402.02750)
- [SnapKV: LLM Knows What You are Looking for Before Generation](https://arxiv.org/abs/2404.14469)
- [Prompt Cache: Modular Attention Reuse for Low-Latency Inference](https://arxiv.org/abs/2311.04934)
- [Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks](https://arxiv.org/abs/2601.06007)
- [LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models](https://arxiv.org/abs/2310.05736)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)
- [Improving language models by retrieving from trillions of tokens](https://arxiv.org/abs/2112.04426)

### 12.2 官方文档和规范

- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic Context Windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- [Anthropic Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Anthropic Context Editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Anthropic Token Counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
- [Model Context Protocol Introduction](https://modelcontextprotocol.io/docs/getting-started/intro)
- [vLLM PagedAttention documentation](https://www.mintlify.com/vllm-project/vllm/concepts/paged-attention)

### 12.3 本地源码锚点

- `
- `
- `
- `
- `
- `
- `

### 12.4 research-deep 产物

- `
- `
- `
- `
- `
- `
- `
- `
