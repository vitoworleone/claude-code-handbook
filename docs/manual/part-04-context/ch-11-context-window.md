# 第11章 上下文窗口管理

"为什么 Claude 有时候突然变蠢了？""为什么它开始重复自己的话？""为什么明明刚说过的事转眼就忘了？"

这三个问题的答案通常是同一个：**上下文窗口快满了。**

本章不讲你怎么写更好的 prompt（那是第 5 章的事），而是讲上下文窗口本身——它是什么、里面有什么、Claude Code 怎么管理它、以及你遇到问题时该怎么诊断和处置。

---

## 11.1 诊断：为什么上下文是最稀缺的资源

### 问题：我的上下文有 200K/1M token，为什么还是不够用？

答案藏在 Transformer 架构的数学性质里。

MBZUAI 的学术论文 *Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems*（以下简称 "Dive into Claude Code"）将上下文窗口列为 Claude Code 架构的**绑定资源约束**（binding resource constraint）。论文的原话是：

> "The context window is the binding resource constraint. Five distinct context-reduction strategies execute before every model call, and several other subsystem decisions exist to limit context consumption."

翻译：**上下文窗口是最稀缺的资源。每次模型调用前执行五种不同的上下文缩减策略，多个其他子系统决策的存在目的就是限制上下文消耗。**

为什么窗口大不能解决问题？CMU、Yale、Amazon 等联合发表的综述 *Agent Harness Engineering: A Survey*（以下简称 "Agent Harness 综述"）给出了三层解释。

#### O(n²)：每加一个 token，注意力关系翻倍

> "The transformer's self-attention mechanism computes pairwise relationships between every token in the context. For n tokens, this produces n² pairwise weights; compute and memory scale quadratically with context length. Doubling the context does not double cost — it quadruples it."

翻译：Transformer 的自注意力机制计算**每对 token 之间**的关系权重。n 个 token 产生 n² 个成对权重。上下文翻倍不是成本翻倍——是**四倍**。

FlashAttention 等工程优化降低了常数因子，但 O(n²) 的结构不变。

#### U 形注意力曲线：位置比内容更影响准确率

综述引用了 Liu et al. (2024) 的关键实证发现：

> "On multi-document question answering with 20 input documents, accuracy drops by more than 30% when the relevant document sits in the middle of the context, compared to placing it at the start or end. This U-shaped performance curve holds across models, tasks, and context lengths, including models trained specifically on long contexts."

翻译：在 20 个文档的多文档问答任务中，相关信息放在上下文中间时，准确率比放在开头或结尾**下降超过 30%**。这个 U 形性能曲线跨模型、跨任务、跨上下文长度都成立——包括专门为长上下文训练的模型。

**实际含义**：信息怎么排列，和信息是否存在，同等重要。AI 检索到了正确内容但放错了位置，跟没检索到差不多。

#### 上下文腐烂（Context Rot）：退化开始于窗口满之前

综述引用了 Hong et al. (2025) 对 18 个前沿模型（包括 GPT-4.1、Claude Opus 4、Gemini 2.5、Qwen3）的系统评估：

> "Every model degraded as input grew. The degradation was non-uniform and task-specific. A model rated for 200K tokens may show significant performance loss at 50K. This phenomenon, which Hong et al. call context rot, is not an edge case. It is the normal operating condition for any agent that accumulates tool results, intermediate reasoning, and file contents over multiple steps."

翻译：**每个模型都随输入增长而退化。** 标称 200K token 的模型，可能在 50K token 时就开始显著退化。这被称为**上下文腐烂**——它不是边界情况，而是任何累积工具结果、中间推理和文件内容的 Agent 的**正常操作条件**。

#### Smart Zone vs Dumb Zone

Matt Pocock（见第 6 章）把这个问题概括为两个区域：

> "Every time you add a token to an LLM, it's like you're adding a team to a football league. The number of matches scales quadratically. Each token has an attention relationship to every other token."

| 区域 | 特征 | 策略 |
|------|------|------|
| **Smart Zone**（~100K token 以下） | 注意力关系最松弛，产出最高质量 | 保持任务在此区域 |
| **Dumb Zone**（~100K token 以上） | 上下文过载，开始做愚蠢决策 | 避免在此区域工作 |

> 不论窗口是 200K 还是 1M，大约 100K token 后模型就开始变蠢。这个阈值会随模型迭代提升，但原则不变。

### 处置：你现在能做什么？

如果你的 Claude 出现了以下症状，先怀疑上下文过载：

| 症状 | 可能原因 | 处置 |
|------|---------|------|
| 开始重复自己说过的话 | 上下文中的早期信息被"挤出"注意力范围 | `/compact` 或 `/clear` |
| 问已经回答过的问题 | 对话历史太长，检索失败 | `/clear` 并在 prompt 中重新描述当前状态 |
| 忘记你刚给的约束 | 约束在上下文中的位置不够靠前 | 把约束写进 CLAUDE.md（每次自动注入） |
| 建议明显变蠢 | 在 Dumb Zone 中工作 | `/clear`，用更好的 prompt 重来 |
| 响应变慢 | O(n²) 的直接后果 | 同上 |

**黄金法则**：同一问题纠正 2 次以上——不要在这个上下文里死磕，`/clear` 或用更好的提示重新开始。不要试图"修好"一个已经腐烂的上下文。

---

## 11.2 诊断：我的上下文里到底有什么？

### 问题：我没给 Claude 多少东西，为什么上下文还是满了？

你没给的东西，Claude Code 自己加了。理解上下文窗口的组装过程，才能判断什么占地方、什么可以精简。

"Dive into Claude Code"（Section 7.1）通过源码逆向还原了完整的组装顺序。以下是从模型视角看，每次调用前上下文窗口里塞入了什么——按注入顺序排列：

```
┌─────────────────────────────────────────────────────────────┐
│                   Claude Code 上下文窗口组装全景图           │
├───────────────┬─────────────────────────────────────────────┤
│  注入顺序     │  内容与来源                                 │
├───────────────┼─────────────────────────────────────────────┤
│               │                                             │
│ (1)           │ 系统提示 (System Prompt)                    │
│ 启动时加载    │  • 基础系统提示（~8K-15K tokens）           │
│ 不可控制      │  • output styles 修改的输出格式块           │
│               │  • --append-system-prompt 的自定义注入      │
│               │                                             │
│ (2)           │ 环境信息 (Environment Info)                 │
│ 启动时加载    │  • git status（当前仓库状态）               │
│ 可部分控制    │  • 缓存破坏注入（仅内部构建）               │
│               │  • memoized per session, 不会重复计算       │
│               │                                             │
│ (3)           │ CLAUDE.md 层级 (4级指令)                    │
│ 启动时加载    │  • Managed: OS级策略（不可排除）            │
│ 你可完全控制  │  • User: ~/.claude/CLAUDE.md（全局个人）    │
│               │  • Project: ./CLAUDE.md + .claude/rules/*.md│
│               │  • Local: CLAUDE.local.md（gitignored）     │
│               │  • memoized per session                     │
│               │                                             │
│ (4)           │ 路径范围规则 (Path-scoped Rules)            │
│ 懒加载        │  • root→CWD 的无条件规则在启动时加载        │
│ 你可完全控制  │  • 子目录规则在 Agent 读到对应文件时才加载  │
│               │                                             │
│ (5)           │ 自动记忆 (Auto Memory)                      │
│ 异步预取      │  • LLM 扫描 ~/.claude/projects/.../memory/  │
│ 你可编辑      │    下所有 .md 文件的 header，选最多 5 个    │
│               │  • 在对话进行中异步注入（不阻塞首轮响应）     │
│               │                                             │
│ (6)           │ 工具元数据 (Tool Metadata)                  │
│ 启动时加载    │  • Skill 描述（仅 frontmatter，不含正文）   │
│ 可部分控制    │  • MCP 工具名称列表                         │
│               │  • 延迟工具定义（通过 ToolSearch 按需加载）  │
│               │                                             │
│ (7)           │ 对话历史 (Conversation History)             │
│ 持续累积      │  • 用户消息                                 │
│ 不可控制      │  • 模型回复（文本 + 工具调用）              │
│               │  • 工具结果（文件内容、命令输出等）          │
│               │  • 附件消息（plan、skill 状态公告等）        │
│               │  • 这是上下文消耗的**最大头**                │
│               │                                             │
│ (8)           │ 工具执行结果 (Tool Results)                 │
│ 每轮注入      │  • 文件读取（Read/Grep/Glob 结果）          │
│ 可部分控制    │  • 命令执行输出（Bash/PowerShell stdout）   │
│               │  • 子代理摘要（Subagent summary，非完整历史）│
│               │  • 每个结果有 budget 上限（见 11.3 第一层）  │
│               │                                             │
│ (9)           │ 压缩摘要 (Compact Summaries)                │
│ 压缩时注入    │  • CompactBoundaryMessage（元数据）         │
│ 系统自动      │  • UserMessage(isCompactSummary=true)（内容）│
│               │  • 替代已被压缩的早期历史                   │
├───────────────┴─────────────────────────────────────────────┤
│ 延迟注入（Late Injection，在对话进行中注入）                │
│  • relevant-memory prefetch（相关记忆异步预取结果）         │
│  • MCP instructions deltas（新连接 MCP 服务器指令增量）     │
│  • agent listing deltas（代理列表变更增量）                 │
│  • background agent task notifications（后台任务通知）      │
└─────────────────────────────────────────────────────────────┘
```

### 关键架构区分：System Prompt 位置 vs User Context 位置

"Dive into Claude Code"（Section 7.1）揭示了一个对日常使用至关重要的架构细节：

> "The system prompt assembly combines system context with the base prompt via asSystemPrompt(). User context (CLAUDE.md and date) is prepended to the message array via prependUserContext(). This separation means CLAUDE.md content occupies a different structural position in the API request than the system prompt, potentially affecting model attention patterns."

翻译：系统提示和 CLAUDE.md 在 API 请求中占据**不同的结构位置**。这影响了模型对它们的注意力分配模式。

具体来说：

| 注入位置 | 内容 | 模型的注意力权重 |
|---------|------|----------------|
| System Prompt 位置 | 系统提示、output styles | 最高（模型几乎不可能违反） |
| User Context 位置 | CLAUDE.md、日期 | 较高，但**概率性遵守**而非确定性（见第 5 章） |
| Conversation 位置 | 对话历史、工具结果 | 依位置递减（中间位置最弱——U 形曲线） |

### 行动指南：你该怎么做？

知道了这些，你可以主动优化上下文消耗：

| 你可以控制的 | 行动 |
|------------|------|
| **CLAUDE.md（来源 3）** | 控制在 200 行以内（见第 5 章）。每多一行都消耗每一次模型调用的 token |
| **Path-scoped Rules（来源 4）** | 用路径范围规则替代全局规则。仅在 Agent 读到匹配文件时才加载，不浪费上下文 |
| **Memory（来源 5）** | 保持 MEMORY.md 整洁。过期或冲突的 memory 文件不仅浪费 token，还可能导致模型做出错误决策 |
| **工具元数据（来源 6）** | 只连接你真正需要的 MCP 服务器。每个 MCP 工具的名称和 schema 都在消耗上下文 |
| **对话历史（来源 7/8）** | 你不直接控制，但可以通过频繁 `/compact`、用子代理隔离探索任务、控制单次对话的任务范围来间接管理 |
| **压缩摘要（来源 9）** | 通过 `/compact <instructions>` 给压缩器自定义指令，告诉它该保留什么 |

**最重要的策略**：把"上下文预算"当成一个真实的有限资源来管理。每次你让 Claude Code 做一件新的事，都在消耗这个预算。计划你的会话，不要让一个会话同时包含"探索新仓库"+"读三个长文件"+"写复杂功能"+"修 bug"+"重构"——这些任务应该分散到多个会话或子代理中。

---

## 11.3 诊断：上下文满了之后发生了什么？

### 问题：`/compact` 到底做了什么？为什么有时候自动压缩后 Claude 看起来"忘了一些事"？

你在第 4 章学过 `/compact` 和 `/clear` 的命令，在第 10 章了解了记忆系统。但理解压缩机制本身，才能判断"压缩后丢失了关键信息"是该修你的 CLAUDE.md 还是该调整你的压缩策略。

"Dive into Claude Code"（Sections 4.3 和 7.3）通过源码还原了完整的五层压缩管道。核心设计哲学是"懒惰降级"（Lazy Degradation）：

> "Rather than a single strategy, Claude Code applies five layers in sequence, each with increasing aggressiveness. Apply the least disruptive compression first, escalating only when cheaper strategies prove insufficient."

翻译：不是单一策略，而是**五层按顺序依次执行**，每层比上一层更激进。先用破坏性最小的，不够再升级。

五层在源码中的执行顺序和每层的机制如下：

```
Layer 1: Budget Reduction    → 始终启用，成本极低
    (不够)
Layer 2: Snip                → HISTORY_SNIP feature flag 门控
    (不够)
Layer 3: Microcompact        → CACHED_MICROCOMPACT feature flag 门控
    (不够)
Layer 4: Context Collapse    → CONTEXT_COLLAPSE feature flag 门控
    (不够)
Layer 5: Auto-Compact        → 用户可配置（默认启用）
```

---

### 第 1 层：Budget Reduction（工具结果预算限制）

**触发条件**：始终启用。在每次模型调用前执行。

**做什么**：`applyToolResultBudget()` 函数对每条工具结果强制执行大小上限。超限的输出被替换为内容引用（content reference），而非保留原文。

**关键细节**：
- 豁免工具：`maxResultSizeChars` 不是有限值的工具保留完整输出
- 内容替换被持久化，以便恢复（resume）时重建
- Budget Reduction 在 Microcompact **之前**运行——因为 Microcompact 纯粹是 `tool_use_id` 操作，从不检查内容。两者干净组合

**你能否感知**：基本不能——被替换的内容通常已经被 Agent 消费过了。只有在恢复（resume）会话时，如果 Agent 需要重新引用被预算限制替换掉的工具输出，才会体现出来。

---

### 第 2 层：Snip（轻量级旧历史裁剪）

**触发条件**：`HISTORY_SNIP` feature flag 启用时。

**做什么**：`snipCompactIfNeeded()` 执行一次轻量级裁剪，移除较旧的历史段。返回 `{messages, tokensFreed, boundaryMessage}`。

**关键细节**：
- `snipTokensFreed` 值被显式传递到 auto-compact —— 因为主 token 计数器从最近一条 assistant 消息的 `usage` 字段推导上下文大小，该消息在 snip 后仍存活且带有 pre-snip 的 `input_tokens`。**snip 节省的 token 如果不显式传递，对计数器不可见**
- 这一层基本不产生用户可感知的效果——它裁剪的是"已经被后续推理吸收过"的旧内容

**你能否感知**：几乎不能。这是最透明的压缩层。

---

### 第 3 层：Microcompact（细粒度缓存感知压缩）

**触发条件**：`CACHED_MICROCOMPACT` feature flag 启用时。

**做什么**：Microcompact 以比 Snip 更细的粒度压缩对话历史。总是运行一个基于时间的路径，可选运行一个缓存感知路径。

**关键细节**：
- 当缓存路径启用时，边界消息被**延迟到 API 响应之后**——以便使用实际的 `cache_deleted_input_tokens` 而非预估
- 返回 `{messages, compactionInfo}`，其中 `compactionInfo` 可能包含 `pendingCacheEdits`
- Microcompact 主要压缩工具结果体积——目标是 Read、Shell、Grep、Glob、WebSearch、WebFetch、Edit、Write 等工具产生的大量输出。源码注释描述为"工具结果膨胀"（tool result bloat）缓解

**和完整 Compaction 的区别**：

| | Microcompact | 完整 Compaction |
|--|-------------|----------------|
| 对象 | 工具结果体积 | 对话历史长度 |
| 粒度 | 细（单个工具输出级别） | 粗（多轮对话级别） |
| 成本 | 极低 | 一次模型调用 |

**你能否感知**：有可能。如果你注意到某个之前读取的长文件的全文内容变得不可用了（Agent 转而使用摘要版本），这是 Microcompact 在工作。

---

### 第 4 层：Context Collapse（上下文折叠——读取时虚拟投影）

**触发条件**：`CONTEXT_COLLAPSE` feature flag 启用时。

**做什么**：这是五层中**最特殊的一层**——它不是真正的"压缩"，而是一个对对话历史的读取时投影。

源码注释原文：

> "Nothing is yielded; the collapsed view is a read-time projection over the REPL's full history. Summary messages live in the collapse store, not the REPL array. This is what makes collapses persist across turns."

翻译：**折叠视图是对 REPL 完整历史的读取时投影。摘要消息存储在折叠存储中，不在 REPL 数组中。这就是折叠能跨 turns 持续存在的原因。**

**关键细节**：
- 与其他层不同，Context Collapse **不修改 REPL 的存储历史**
- 通过 `applyCollapsesIfNeeded()` 将 `messagesForQuery` 数组替换为投影视图，所以模型看到折叠后的版本，而完整历史仍可用于恢复
- 这意味着：如果后续需要恢复原始信息，数据没有丢失——只是当前这轮模型调用看不到

**你能否感知**：**完全不能。** 这是五层中最不透明的压缩层——它没有用户可见的输出。模型看到的内容已经变了，但你从对话界面看不出来。

---

### 第 5 层：Auto-Compact（模型生成完整摘要）

**触发条件**：前四层全部执行后，上下文仍超过压力阈值。用户可配置（默认启用）。

**做什么**：这是大多数人说"压缩"——`compactConversation()` 触发一次**独立的模型调用**，生成压缩摘要，然后用这个摘要替代被压缩的对话历史。

**阈值公式**（来源 `autoCompact.ts:28-65`）：

```python
effectiveContextWindow = modelContextWindow - reservedForSummary(20000)
autoCompactThreshold   = effectiveContextWindow - 13000
manualBlockingLimit    = effectiveContextWindow - 3000
```

Token 估算使用**字节除法**而非 BPE tokenizer：

```python
estimated_tokens = len(json.dumps(api_messages).encode()) // 4
```

为什么用 `bytes // 4` 而非精确的 BPE tokenizer？不加载 ~2MB 的 BPE 词表以节省内存和初始化时间。英文文本中 token/byte 比约 0.25-0.3，`bytes // 4` 对英文略微高估（偏保守——更早触发压缩），3000 的 buffer 补偿估算误差。

**压缩执行流程**（来源 `compact.ts:387-748` 和 `compact.ts:1136-1396`）：

```
compactConversation()
  ├─ token 计数
  ├─ 执行 PreCompact hooks（stdout 合并到自定义压缩指令中）
  ├─ streamCompactSummary()
  │  ├─ 优先 runForkedAgent（缓存共享）
  │  └─ 回退 queryModelWithStreaming
  ├─ 如果摘要命中 prompt-too-long → truncateHeadForPTLRetry()（最多 3 次重试）
  ├─ 清除 readFileState / loadedNestedMemoryPaths
  ├─ 创建 post-compact attachments（files, plan, skills, async agents, tools, MCP, hooks）
  ├─ 创建 CompactBoundaryMessage + 摘要 UserMessage
  ├─ 遥测 + 缓存破坏通知
  └─ 执行 PostCompact hooks
```

**压缩后的消息结构**（`buildPostCompactMessages()` 输出）：

```
[boundaryMarker, ...summaryMessages, ...messagesToKeep, ...attachments, ...hookResults]
```

其中 `boundaryMarker` 通过 `annotateBoundaryWithPreservedSegment()` 标注了 `headUuid`、`anchorUuid` 和 `tailUuid`，用于读取时的链式重建。

**断路器**：连续压缩失败 3 次后**停用 auto-compact**，防止无限抖动。

**双防御策略**：

| | COMPACT_SYSTEM_PROMPT | SUMMARIZE_TOOL_RESULTS |
|--|----------------------|------------------------|
| 防御类型 | 事后（压缩发生时才生效） | 事前（压缩发生前就预防） |
| 执行者 | 摘要器（独立模型调用） | 主模型自己 |
| 目标 | 压缩时正确选择保留什么 | 让主模型在回复中内联关键工具输出的摘要，这样即使压缩丢弃了原始输出，信息仍在 |

**Session Memory Compact（Session Memory 优先策略）**：Auto-compact 时会先尝试使用 Session Memory——后台 forked agent 更新的结构化笔记。触发条件：token 增长（初始 10K，后续 5K 间隔）+ 3 次工具调用。如果 Session Memory 压缩后仍超阈值，再回退到传统的完整压缩。这比每次都重新摘要整个历史更快更便宜。

**对压缩摘要模型的限制**：
- `createCompactCanUseTool()` 始终拒绝所有工具——压缩 Agent 只能生成文本，不能调用工具
- Forked compact agent 不设置 `maxOutputTokens`——避免缓存 key 不匹配

**缓存共享实验**：源码注释记录了 2026 年 1 月的一次实验——"false path is 98% cache miss, costs ~0.76% of fleet cache_creation"（假路径 98% 缓存未命中，消耗约 0.76% 的全队列缓存创建配额）。这解释了为什么 compact 路径使用 forked agent 复用缓存。

**你能否感知**：**能。** 压缩后你会看到一条压缩摘要消息，概括了被替代的历史。如果你觉得压缩后的摘要遗漏了重要信息，可以通过 `/compact <instructions>` 给压缩器自定义指令，告诉它额外保留什么。

---

### 源码视角：五层的统一设计

"Dive into Claude Code" 在 Section 3.6 总结了理解这五层的四个关键原则：

**1. 懒惰降级（Lazy Degradation）**。先用最便宜的手段（截断、裁剪），不够再升级到更贵的手段（缓存感知压缩、虚拟投影），最后才用最贵的手段（一次完整的模型调用做摘要）。

**2. Mostly-Append 设计**。压缩**永不修改或删除**已写入的转录行。它只追加新的 boundary 和 summary 事件。论文解释了其目的：

> "The boundary marker is annotated with preserved-segment metadata via annotateBoundaryWithPreservedSegment(), recording headUuid, anchorUuid, and tailUuid to enable read-time chain patching. This mostly-append design means compaction never modifies or deletes previously written transcript lines."

翻译：**压缩不删除数据——它只追加标记，在读取时重建消息链。**

**3. 各层独立但与后续层互操作**。例如 Budget Reduction 在 Microcompact 前运行（因为 Microcompact 不做内容检查），Snip 的 `tokensFreed` 作为显式参数传递给 Auto-compact（因为主计数器从 assistant message usage 推导，看不到 Snip 节省的 token）。

**4. 弹性应对**。Auto-compact 有断路器（连续失败 3 次停用），压缩摘要生成有重试（最多 3 次 `truncateHeadForPTLRetry`），compaction 后 attachment builders 重新发布运行时状态（plans、skills、async agents）——从实时应用状态而非压缩前的旧状态。

---

### 处置：你应该如何应对压缩

| 你遇到的 | 该怎么做 |
|---------|---------|
| 压缩后 Claude 忘了关键决策 | 把关键决策写进 CLAUDE.md（压缩后自动重新注入）或 Session Memory（auto-compact 优先使用） |
| 压缩后后台任务状态丢失 | 工具通过 PreCompact Hook 注册到压缩流程——TaskTool 在压缩前注入"后台任务状态"。你不需要手动处理 |
| 压缩太频繁（每几轮就触发） | 你的单次对话任务范围太大。拆分为子代理、更频繁 `/clear` 切换话题、减少工具输出体积 |
| 压缩后质量明显下降 | 打开 auto-compact 断路器可能在生效（连续失败后停用）。用 `/context` 查看实际使用量，手动 `/compact` |
| 不想自动压缩 | 在 `settings.json` 中设置 `"autoCompact": false`，只在 `/compact` 手动触发 |

---

## 11.4 诊断：我该 Compact 还是 Clear 还是写 Memory？

三个操作容易混淆。以下是它们的明确区分：

| | Compact (/compact) | Clear (/clear) | Memory（自动/手动） |
|--|-------------------|----------------|-------------------|
| **触发者** | 系统自动 或 你手动 | 你手动 | Claude 自动（停止边界）或你手动 |
| **做什么** | 把旧历史替换为模型生成的摘要 | 清空当前上下文，旧对话保存为会话文件 | 提取关键事实写入 Markdown 文件 |
| **数据去哪** | 压缩摘要在当前会话中 | 旧对话保存到 `~/.claude/projects/<project>/<session>.jsonl` | `~/.claude/projects/<project>/memory/*.md` |
| **时间范围** | 会话内 | 跨会话（通过 `--resume` 恢复） | 跨会话持久 |
| **能否恢复** | 被压缩的原始对话不保留（只有摘要） | 可以——`--continue` 或 `--resume` | 始终可读可编辑 |
| **模型参与** | 是（独立模型调用生成摘要） | 否（直接清空消息数组） | 是（提取时）或否（你手动编辑时） |

### 决策树

```
上下文压力大？
  ├─ 还要继续当前话题？
  │    └─ → /compact
  │        如果压缩后丢了关键信息 → 先写进 CLAUDE.md，再 compact
  ├─ 话题要切换了？
  │    └─ → /clear
  │        旧对话自动保存，需要时可以 --resume
  ├─ 有件事你不想在压缩中丢失？
  │    └─ → 写进 CLAUDE.md（每次自动注入）或等着 Claude
  │        自动记忆机制在停止边界时提取
  └─ 同一问题纠正 2 次以上？
        └─ → /clear 然后用更好的提示重来
            继续在旧上下文里纠正只会越纠越偏
```

---

## 11.5 诊断：为什么改了 CLAUDE.md 之后变慢了？

### 问题："我只是在 CLAUDE.md 里加了一行，为什么下一次对话感觉慢了？"

答案很可能是：**Prompt Cache 失效了。**

"Agent Harness 综述"（Section 5.3）引用了 Manus 团队经过五次架构迭代后得出的结论：

> "KV-cache hit rate is the single most important metric for a production-stage AI agent."

翻译：**KV-cache 命中率是生产级 AI Agent 最重要的指标。**

为什么？因为缓存命中和未命中的成本差距悬殊：

> "Cached tokens on Claude Sonnet cost $0.30/MTok compared to $3.00/MTok for uncached tokens."

翻译：**缓存 token 成本是未缓存的十分之一**——$0.30 vs $3.00 每百万 token。

### Prompt Cache 如何工作

当你连续发送多个 API 请求时，如果前缀（prefix）相同，Anthropic API 可以复用之前计算好的 KV-cache，跳过对前缀部分的重新计算。这不仅省钱，而且更快——不需要重新对前缀做自注意力计算。

### 缓存失效的三条规则

综述从缓存模型中推导出三条设计规则：

**规则一：保持提示前缀稳定。** "A single token difference at the start of the system prompt invalidates the cache for everything that follows." 系统提示开头的一个 token 差异，会使后面所有内容的缓存失效。

**规则二：上下文只追加不修改。** "Modifying past actions or observations breaks cache reuse by creating a different prefix sequence." 修改过去的操作或观察，会产生不同的前缀序列，破坏缓存复用。

**规则三：使用确定性序列化。** "Non-stable key ordering in JSON serialization silently invalidates caches across otherwise identical requests." JSON 序列化中不稳定的 key 排序，会在其他完全相同的请求之间静默地使缓存失效。

### 缓存失效的具体触发场景

| 你做了什么 | 为什么缓存失效 |
|-----------|-------------|
| 修改了 CLAUDE.md 的任何内容 | CLAUDE.md 在系统提示之后、对话历史之前注入。修改它改变了前缀 |
| 添加或删除了 MCP 工具 | 工具定义通常出现在序列化上下文的前部。添加/删除工具使后续所有 turns 的缓存内容失效 |
| 启用了新的 Skill | Skill 描述出现在 prompt 前部 |
| 修改了 output style | Output style 替代系统提示中的响应格式块，改变前缀 |
| 对话中切换了模型 | 不同模型有不同的缓存命名空间 |

### 缓存友好的上下文设计

Manus 团队的做法是使用**上下文感知状态机**（context-aware state machine），在解码期间掩码 token logits 以阻止选择不可用的操作——**而不是在运行时修改工具定义列表**。这样工具列表保持稳定，缓存不会因工具可用性变化而失效。

"Agent Harness 综述"对此的总结是：

> "Treat context as append-only: modifying past actions or observations breaks cache reuse. Use deterministic serialization: non-stable key ordering in JSON serialization silently invalidates caches."

翻译：**把上下文视为只追加的——修改过去的操作会破坏缓存复用。使用确定性序列化——JSON 中不稳定的 key 排序会静默失效缓存。**

### 处置

- 如果你发现对话突然变慢或变贵了——先回想你是否刚改了 CLAUDE.md、MCP 配置、或 Skill 列表
- 把 CLAUDE.md 的修改集中在一起做，而不是每次发现一个小问题就去改一行（每次修改都触发缓存失效）
- 设计 CLAUDE.md 时把"稳定部分"（架构决策、代码风格）放在前面，"经常变化的部分"放在后面或用 path-scoped rules 懒加载
- 用 `/context` 查看实际上下文消耗——缓存失效不会直接报警，但成本上升和响应变慢是信号

---

## 11.6 实战：上下文窗口的日常诊断与处置

综合前面五节，形成一套日常操作流程。

### 日常检查清单

| 频率 | 做什么 | 命令/方式 |
|------|--------|---------|
| 每轮对话开始时 | 看一眼 CLAUDE.md 是否仍然准确（没有过时的指令） | 直接打开 `./CLAUDE.md` |
| 感觉 Claude 开始"变蠢" | 检查上下文消耗量 | `/context` |
| 上下文超过 80% | 压缩 | `/compact` |
| 话题切换 | 清空上下文 | `/clear` |
| 发现新规则/模式 | 写进 CLAUDE.md 或 path-scoped rule | 直接编辑文件 |
| 每周 | 审查 CLAUDE.md，删除过时内容 | 直接编辑 + `/init` 重建 |
| 模型升级时 | 审视流程中的检查步骤是否仍必要（见第 8 章 8.2 节） | 直接审阅工作流程 |

### 大任务的分段策略

如果你的任务天然会消耗大量上下文（例如"把这 50 个 Component 类迁移到 Hooks"），不要在单个会话中硬扛。使用以下策略：

**策略一：子代理隔离。** 让主会话派 Explore agent 探索代码库结构，Explore agent 的结果以摘要形式返回——整个探索过程产生的 token 只留在子代理的独立上下文窗口中。

**策略二：分阶段会话。** 会话 A：理解需求 + 设计方案 → `/clear`。会话 B：按方案实现 → `/clear`。会话 C：审查 + 修复。每一段都从一个干净的上下文出发。

**策略三：CLAUDE.md 承载状态。** 把方案、进度、决策写进 CLAUDE.md。每次 `/clear` 后新会话会自动加载。不要依赖"对话历史"来承载任务状态——对话历史是压缩的对象，CLAUDE.md 不是。

### 四级保留策略

按任务阶段分配上下文密度，是避免"该保留的丢了，该丢的还留着"的关键。来源素材给出了四级保留策略：

| 等级 | 适用内容 | 保留形式 |
|---|---|---|
| **原文保留** | 当前编辑代码块、失败断言、用户最新要求 | 原文 |
| **摘要保留** | 已读大文件、测试长输出、已完成探索 | 结构化摘要 |
| **索引保留** | 长期记忆、项目规则、skill 集合、代码库地图 | manifest/index |
| **丢弃** | 重复工具输出、无关寒暄、过期假设 | 不进入 active context |

按任务阶段的 compact 策略：

| 阶段 | 目标 | compact 策略 |
|---|---|---|
| Intake | 理解需求 | 不 compact，保留用户原话 |
| Explore | 找文件、建证据 | 折叠 read/search，保留发现摘要 |
| Diagnose | 找 root cause | 不在假设未收敛前 compact |
| Edit | 最小修改 | 保留精确片段和修改理由 |
| Verify | 运行测试 | 长日志摘要，失败片段原文保留 |
| Wrap | 交付说明 | compact 或写 task state |

### 上下文不足时 Claude 发出的信号

学会识别这些信号，在上下文彻底腐烂之前主动干预：

```
信号 1：Claude 开始重复自己说过的话
  → 这意味着早期信息已被"挤出"注意力范围
  → 处置：/compact 或 /clear

信号 2：Claude 问已经回答过的问题
  → 对话历史中的早期信息在 U 形曲线的谷底，检索失败
  → 处置：/clear + 在 prompt 中重新描述当前状态

信号 3：工具调用结果变得冗余或无关
  → Claude 在"猜"而不是基于证据行动
  → 处置：/clear + 给更明确的任务范围

信号 4：响应速度明显变慢
  → O(n²) 注意力计算的直接后果
  → 处置：/compact
```
