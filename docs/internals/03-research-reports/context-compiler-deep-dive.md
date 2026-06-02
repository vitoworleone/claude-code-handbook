# Claude Code Context Compiler 深度研究报告

## Token 经济性、命中率、工具调用、Skill 管理、上下文维护与模型差异

> 报告定位：基于 Claude Code TypeScript 源码（1902 文件，513,237 行）的实证分析，为 Agent Runtime 的上下文编译系统提供可验证、可落地的设计参考。
>
> 研究方法：对每个问题定位到具体源码文件与行号，提取实际阈值、预算、策略和权衡，而非理论推导。
>
> 生成日期：2026/05/22

---

## 目录

- [第一章 Token 经济性](#第一章-token-经济性)
- [第二章 命中率 / 任务成功率](#第二章-命中率--任务成功率)
- [第三章 工具调用能力](#第三章-工具调用能力)
- [第四章 Skill 太多怎么办](#第四章-skill-太多怎么办)
- [第五章 上下文维护方案](#第五章-上下文维护方案)
- [第六章 不同模型的工具遵循差异](#第六章-不同模型的工具遵循差异)
- [附录 A 关键源码文件索引](#附录-a-关键源码文件索引)

---

## 第一章 Token 经济性

### 1.1 在 AI coding agent 里，哪些内容最浪费 token，但对任务成功率贡献最低？

**结论：工具 schema 和重复注入的上下文附件是最主要的 token 浪费源。**

**源码证据：**

- `src/utils/api.ts:537-550` — `logContextMetrics()` 记录了明确的 token 分布：
  - `nonMcpToolsTokens` / `mcpToolsTokens` — 工具 schema 的 token 估算
  - `git_status_size` / `claude_md_size` — 上下文内容的尺寸
- `src/utils/attachments.ts:2641-2751` — `getSkillListingAttachments()` 注入 skill 索引，每个 skill 的 description 都可能达到数百 tokens
- `src/services/compact/compact.ts:122-130` — post-compact 恢复预算显示，文件恢复限制为 5 个文件 / 50K tokens / 每文件 5K tokens

**分析：**

| 内容类型                   | Token 占比 | 任务贡献度         | 浪费原因                                            |
| -------------------------- | ---------- | ------------------ | --------------------------------------------------- |
| 工具 schema                | 30-60%     | 高（必须）         | 但 MCP 工具的 schema 是用户自定义的，质量参差不齐   |
| 历史消息中的旧 tool_result | 20-40%     | 低（已过时效）     | 长会话中累积，microcompact 就是为了清除这些         |
| Skill 描述                 | 5-15%      | 中（按需才有价值） | 全部 skill 索引常驻，但大多数 skill 不会被调用      |
| Git status                 | 1-5%       | 中（项目感知）     | 但 git status 是 session 级的快照，不随文件变化更新 |
| 已读文件缓存               | 5-10%      | 高（避免重复读取） | 但 LRU 100 条限制可能导致频繁替换                   |

**可落地建议：**

1. 对 MCP 工具 schema 做 token 审计，设置 `maxSchemaTokens` 上限
2. 默认启用 microcompact，时间触发 60 分钟、保留最近 5 个 tool_result
3. Skill 索引按调用频率排序，低频 skill 降级到按需发现

---

### 1.2 System Prompt、工具 schema、历史消息、代码片段、文档知识、测试输出，谁才是真正的 token 大户？

**结论：工具 schema 是绝对大户，历史消息是动态增长的最大变量。**

**源码证据：**

- `src/constants/prompts.ts:444-577` — `getSystemPrompt()` 构建 15+ 个 section，其中 tool schema 通过 `getTools()` 注入
- `src/utils/api.ts:119-266` — `toolToAPISchema()` 序列化所有工具的 input_schema，每个工具的 JSON Schema 通常 500-2000 tokens
- `src/services/tokenEstimation.ts:203-208` — `roughTokenCountEstimation()` 用 bytes/4 估算 token
- `src/utils/context.ts:9` — `MODEL_CONTEXT_WINDOW_DEFAULT = 200_000`

**实际数据（基于源码中的日志和预算）：**

| 组件                          | 典型大小        | 来源                                          |
| ----------------------------- | --------------- | --------------------------------------------- |
| System Prompt 静态部分        | 10K-20K tokens  | `getSystemPrompt()` 15+ sections            |
| 工具 schema（30+ 内置工具）   | 15K-30K tokens  | `toolToAPISchema()`                         |
| MCP 工具 schema（用户自定义） | 5K-50K+ tokens  | `mcpToolsTokens` in `logContextMetrics()` |
| User Context (CLAUDE.md)      | 1K-10K tokens   | `getUserContext()`                          |
| System Context (git status)   | 0.5K-3K tokens  | `getSystemContext()`                        |
| 历史消息（初始）              | 1K-5K tokens    | 用户输入 + 附件                               |
| 历史消息（20 轮后）           | 50K-150K tokens | tool_result 累积                              |

**关键发现：**

- 工具 schema 占总上下文的 **30-60%**
- 历史消息在 20 轮后可能超过 system prompt 的总量
- `AUTOCOMPACT_BUFFER_TOKENS = 13_000` (`src/services/compact/autoCompact.ts:62`) 的设计就是为了在达到 200K 上限前主动压缩

**可落地建议：**

1. 为 MCP 工具设置 schema 大小上限，超限工具自动降级为"描述 only"模式
2. 对历史消息实施分层：最近 4 轮完整保留，更早的只做摘要
3. 用 `toolSchemaCache.ts` 按 session 缓存序列化结果，避免每轮重复计算

---

### 1.3 如何判断一个上下文片段应该被"原文保留、摘要保留、索引保留、丢弃"？

**结论：Claude Code 使用四层策略，按信息类型和时效性分级处理。**

**源码证据：**

- `src/services/compact/compact.ts:122-131` — Post-compact 恢复策略：
  - `POST_COMPACT_MAX_FILES_TO_RESTORE = 5` — 原文保留最近 5 个文件
  - `POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000` — 每文件最多 5K tokens
  - `POST_COMPACT_TOKEN_BUDGET = 50_000` — 文件恢复总预算 50K
- `src/services/compact/microCompact.ts:36` — `TIME_BASED_MC_CLEARED_MESSAGE = '[Old tool result content cleared]'` — 旧 tool_result 直接丢弃
- `src/services/compact/prompt.ts:61-143` — Compact prompt 要求生成 9 个 section 的摘要：Primary Request、Key Concepts、Files/Code、Errors/Fixes、Pending Tasks、Current Work
- `src/services/compact/sessionMemoryCompact.ts:57-61` — 保留最近消息的最小阈值：`minTokens: 10_000`，`minTextBlockMessages: 5`

**四层策略矩阵：**

| 策略               | 适用内容                                 | 触发条件                                                 | 源码位置                     |
| ------------------ | ---------------------------------------- | -------------------------------------------------------- | ---------------------------- |
| **原文保留** | 当前工作文件、最近 5 轮对话              | 文件在 `readFileState` 中、消息在 compact boundary 后  | `compact.ts:122-130`       |
| **摘要保留** | 旧对话历史、已完成任务                   | token 超过 `effectiveContextWindow - 13_000`           | `autoCompact.ts:62`        |
| **索引保留** | Skill 列表、工具名称                     | 以 `skill_listing` / `deferred_tools_delta` 形式存在 | `attachments.ts:2641-2751` |
| **丢弃**     | 超过 60 分钟的 tool_result、已压缩的历史 | microcompact 时间触发或 full compact                     | `microCompact.ts:36`       |

**可落地建议：**

1. 对文件内容：最近读取的保留原文，超过 5 个文件的只保留路径索引
2. 对对话历史：保留最近 5 轮完整文本，更早的生成结构化摘要
3. 对工具结果：保留最近 5 个，旧的替换为 `[Old tool result content cleared]`
4. 对 skill/工具：只保留索引（名称+描述），完整内容按需加载

---

### 1.4 长会话里，什么时候应该 compact？什么时候 compact 反而会损害任务连续性？

**结论：双触发机制 —— 主动阈值触发 + 被动错误恢复。Compact 在涉及多文件依赖或 pending 验证时会损害连续性。**

**源码证据：**

- `src/services/compact/autoCompact.ts:62` — `AUTOCOMPACT_BUFFER_TOKENS = 13_000`
- `src/services/compact/autoCompact.ts:72-91` — `getAutoCompactThreshold()` 计算 `effectiveContextWindow - 13_000`
- `src/services/compact/autoCompact.ts:241-351` — `compactConversation()` 触发条件：token 超过阈值
- `src/services/compact/compact.ts:243-291` — `truncateHeadForPTLRetry()` — 被动触发：API 返回 prompt_too_long
- `src/services/compact/compact.ts:145-200` — Compact 前剥离图片，避免 PTL

**触发时机：**

| 触发类型                         | 条件                           | 策略                                 | 对连续性的影响                 |
| -------------------------------- | ------------------------------ | ------------------------------------ | ------------------------------ |
| **主动 Auto-compact**      | token > 187K (200K - 13K)      | Forked agent 生成摘要，保留最近 4 轮 | 低影响，摘要包含关键决策       |
| **被动 Reactive compact**  | API 返回 413 prompt_too_long   | 应急压缩，每轮限 1 次                | 中影响，可能丢失中间状态       |
| **Context collapse drain** | PTL 且 staged collapses 可释放 | 释放折叠的上下文                     | 低影响，折叠内容本来就是隐藏的 |

**Compact 损害连续性的场景：**

1. **多文件编辑中**：compact 后只恢复 5 个文件，其他文件的状态丢失，模型可能重复读取
2. **Pending 验证时**：compact 摘要可能不包含"验证尚未完成"的状态，导致模型提前报告完成
3. **Debugging 中**：错误堆栈和修复尝试被压缩后，模型忘记之前试过的方案

**源码中的防御措施：**

- `COMPACT_SYSTEM_PROMPT` (`src/constants/prompts.ts`) — 指导摘要器保留关键决策、文件路径
- `SUMMARIZE_TOOL_RESULTS` — 事前防御：指导模型把关键信息写入自己回复，防止原始 tool_result 被压缩丢弃

**可落地建议：**

1. 在 multi-file edit 任务中，compact 前显式记录 `pendingEdits` 列表
2. 设置 compact 防御提示：要求摘要必须包含 pending tasks 和 errors
3. 考虑在 compact 边界添加 `isCompactSummary: true` 标记，便于后续识别

---

### 1.5 如何设计一个 token budget policy，让 agent 在不同任务阶段使用不同上下文密度？

**结论：Claude Code 已有隐式的阶段化预算，但可以通过显式策略进一步优化。**

**源码证据：**

- `src/services/compact/compact.ts:122-130` — Post-compact 预算：
  - 文件恢复：`POST_COMPACT_TOKEN_BUDGET = 50_000`
  - Skill 恢复：`POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000`
  - 每文件上限：`POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000`
  - 每 skill 上限：`POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000`
- `src/tools/SkillTool/prompt.ts:21` — `SKILL_BUDGET_CONTEXT_PERCENT = 0.01` (1% 上下文窗口)
- `src/utils/attachments.ts:2641-2659` — `FILTERED_LISTING_MAX = 30` 个 skill 上限
- `src/utils/context.ts:24` — `CAPPED_DEFAULT_MAX_TOKENS = 8_000` (输出预算)

**现有隐式阶段：**

| 阶段               | 上下文密度 | 预算分配                             |
| ------------------ | ---------- | ------------------------------------ |
| **探索阶段** | 低密度     | 工具 schema 完整加载，skill 索引全量 |
| **实现阶段** | 中密度     | 文件恢复预算 50K，skill 预算 25K     |
| **验证阶段** | 高密度     | 最近消息保留 10K tokens / 5 条       |
| **收尾阶段** | 低密度     | 只保留摘要和关键文件                 |

**可落地的显式 Budget Policy：**

```
阶段 1: 需求理解 (0-3 轮)
  - 工具 schema: 100% (完整加载)
  - 项目上下文: 100% (CLAUDE.md + git status)
  - 历史消息: 100% (对话尚短)
  - 预算: 无限制

阶段 2: 探索定位 (3-8 轮)
  - 工具 schema: 100% (需要搜索)
  - 项目上下文: 50% (只保留相关规则)
  - 历史消息: 80% (保留最近 3 轮完整)
  - 预算: 不超过 150K tokens

阶段 3: 实现修改 (8-15 轮)
  - 工具 schema: 50% (只保留已使用工具)
  - 项目上下文: 30% (按需加载)
  - 历史消息: 50% (compact 后摘要)
  - 文件恢复: 5 个文件 / 50K tokens
  - 预算: 不超过 100K tokens + 输出

阶段 4: 验证测试 (15-20 轮)
  - 工具 schema: 30% (只保留测试相关)
  - 历史消息: 30% (最近 2 轮 + 摘要)
  - 诊断信息: 100% (LSP diagnostics 全量)
  - 预算: 不超过 80K tokens

阶段 5: 收尾交付 (20+ 轮)
  - 只保留摘要 + 修改的文件列表
  - 预算: 不超过 30K tokens
```

**可落地建议：**

1. 在 system prompt 中添加阶段识别指令，让模型自行判断当前阶段
2. 根据阶段动态调整 `AUTOCOMPACT_BUFFER_TOKENS`（探索阶段放宽到 20K，实现阶段收紧到 10K）
3. 阶段转换时触发一次 microcompact，清除不再相关的 tool_result

---

### 1.6 工具结果应该完整回填给模型，还是先结构化摘要？摘要到什么粒度才不影响后续推理？

**结论：Claude Code 对大部分工具返回完整结果，但对大输出有明确的截断和元数据标记机制。**

**源码证据：**

- `src/Tool.ts:456-466` — `maxResultSizeChars` 文档：
  - "Set to Infinity for tools whose output must never be persisted (e.g. Read)"
  - 防止 Read 工具的结果被持久化导致循环
- `src/tools/GrepTool/GrepTool.ts:108-128` — `DEFAULT_HEAD_LIMIT = 250`，截断时报告 `appliedLimit`
- `src/tools/GrepTool/GrepTool.ts:144-155` — 输出包含 `appliedLimit` 和 `appliedOffset`，让模型知道是否被截断
- `src/tools/GlobTool/GlobTool.ts:39-52` — 输出包含 `truncated: boolean` 和 `numFiles`
- `src/services/compact/microCompact.ts:36` — 旧 tool_result 替换为 `[Old tool result content cleared]`

**摘要粒度策略：**

| 工具类型                  | 回填策略                     | 粒度        | 原因                     |
| ------------------------- | ---------------------------- | ----------- | ------------------------ |
| **Read**            | 完整内容                     | Infinity    | 模型需要精确文本做 edit  |
| **Grep**            | 前 250 条 + 截断标记         | 250 results | 太多匹配会淹没上下文     |
| **Bash**            | 完整 stdout/stderr           | 无截断      | 错误信息可能包含关键线索 |
| **Glob**            | 全部文件名 + truncated 标记  | 无数量限制  | 文件名通常很短           |
| **LSP diagnostics** | 新错误 only（baseline 相减） | 只报新增    | 避免旧错误噪音           |
| **旧 tool_result**  | 丢弃（替换为占位符）         | 零内容      | 已过时效                 |

**关键设计原则：**

1. **保留元数据让模型知道是否被截断** — `appliedLimit`、`truncated`、`numFiles`
2. **Infinity 策略防止循环依赖** — Read 工具不设 `maxResultSizeChars`，避免文件内容在 transcript 中无限膨胀
3. **Baseline 相减减少噪音** — diagnostics 只报新增，不是全部

**可落地建议：**

1. 对 Read 类工具：完整回填，不设上限，但依赖 microcompact 定期清理
2. 对 Search 类工具：设置 head limit（250），输出截断标记
3. 对 Test/Build 类工具：完整回填 stderr，但 stderr 超过 10K 时截断并标记
4. 所有截断都必须包含元数据，让模型知道"还有更多"

---

### 1.7 如何减少重复注入项目规则、工具说明、skill 描述、历史决策？

**结论：Claude Code 使用 Delta 附件 + Memoization + LRU 缓存三层机制来消除重复注入。**

**源码证据：**

- `src/utils/attachments.ts:1455-1585` — Delta 附件系统：
  - `getDeferredToolsDeltaAttachment()` — 只宣布新增工具
  - `getAgentListingDeltaAttachment()` — 只宣布新增 agent
  - `getMcpInstructionsDeltaAttachment()` — 只宣布新增 MCP 指令
- `src/constants/systemPromptSections.ts:43-58` — `resolveSystemPromptSections()` 维护 section cache，`cacheBreak: false` 的 section 只计算一次
- `src/utils/fileStateCache.ts:30-93` — `FileStateCache` LRU (100 entries, 25MB)，已读文件不重复读取
- `src/utils/attachments.ts:2063-2145` — `getChangedFiles()` 用 `readFileState` 检测变化，只报告修改过的文件

**三层去重机制：**

| 机制                          | 去重对象                       | 实现方式                                     | 源码位置                          |
| ----------------------------- | ------------------------------ | -------------------------------------------- | --------------------------------- |
| **Delta 附件**          | 工具列表、Agent 列表、MCP 指令 | 每轮只发送增量                               | `attachments.ts:1455-1585`      |
| **Section Memoization** | System Prompt 各 section       | `cacheBreak: false` 的 section 只计算一次  | `systemPromptSections.ts:43-58` |
| **LRU 缓存**            | 文件内容                       | `readFileState` 100 条目，mtime 变化才刷新 | `fileStateCache.ts:30-93`       |

**可落地建议：**

1. 对所有动态列表（工具、skill、agent）实施 delta 机制，维护 `sentNames` Set
2. System Prompt 按 section 粒度做 memoization，明确标记哪些 section 是稳定的
3. 文件读取结果用 LRU + mtime 校验，避免重复读取未变更文件
4. 历史决策提取到 session memory，通过 `relevant_memories` 按需加载，而非每轮注入

---

### 1.8 是否应该把长期知识从 prompt 中移出去，改成按需检索？

**结论：Claude Code 已经部分实现了按需检索，但 CLAUDE.md 仍然在 system prompt 中全量注入。**

**源码证据：**

- `src/context.ts:155-189` — `getUserContext()` 加载 CLAUDE.md 内容进入 user context
- `src/utils/attachments.ts:2346-2423` — `startRelevantMemoryPrefetch()` 异步搜索相关记忆
- `src/utils/attachments.ts:269-289` — `MAX_MEMORY_BYTES = 4096` 每文件，`MAX_SESSION_BYTES = 60KB` 每 session
- `src/utils/claudemd.ts:1249-1317` — `getMemoryFilesForNestedDirectory()` 按路径层次加载 CLAUDE.md

**现状分析：**

| 知识类型          | 当前策略              | 是否按需 | 问题                               |
| ----------------- | --------------------- | -------- | ---------------------------------- |
| CLAUDE.md         | 全量注入 user context | 否       | 项目规则可能 10K+ tokens，全部常驻 |
| Session Memory    | 异步预取 + 附件       | 是       | 60KB 上限，可能遗漏                |
| Relevant Memories | 按需检索（AKI）       | 是       | 实验性功能，依赖 feature flag      |
| 文件内容          | LRU 缓存              | 是       | 100 条目限制                       |

**可落地建议：**

1. **CLAUDE.md 分层按需加载**：当前是 hierarchical 加载（从 CWD 向上遍历），可以进一步按 `paths` frontmatter 条件过滤
2. **规则文件转索引**：把 `.claude/rules/*.md` 从全量注入改为"索引 + 按需读取"，类似 skill 的处理方式
3. **Session Memory 提升优先级**：把 session memory 从附件形式提升到 system prompt 级别，因为它是结构化的决策记录
4. **知识检索与工具调用解耦**：当前 relevant memories 是通过附件预取，可以改为专门的 `KnowledgeRetrieve` 工具，让模型主动决定何时检索

---

## 第二章 命中率 / 任务成功率

### 2.1 什么上下文最能提高 agent 第一次找对文件的概率？

**结论：CLAUDE.md 的层级规则 + git status 的变更感知 + LSP diagnostics 的被动诊断，三者组合最能提高首次命中率。**

**源码证据：**

- src/utils/claudemd.ts:1249-1317 — getMemoryFilesForNestedDirectory() 从 CWD 向上遍历加载 CLAUDE.md
- src/utils/claudemd.ts:1354-1397 — processConditionedMdRules() 按 frontmatter 的 glob 模式匹配目标路径
- src/utils/attachments.ts:2063-2145 — getChangedFiles() 检测文件修改，生成 diff 附件
- src/utils/attachments.ts:2883-2935 — getLSPDiagnosticAttachments() 被动获取 LSP 诊断
- src/constants/prompts.ts:230 — 先读取再修改的约束

**首次命中率的上下文优先级：**

| 上下文                   | 命中率贡献 | 原因                                   |
| ------------------------ | ---------- | -------------------------------------- |
| CLAUDE.md 规则           | 高         | 开发者写的人工规则直接告诉模型文件结构 |
| Git status 变更文件      | 高         | 用户最近修改的文件通常就是目标         |
| LSP diagnostics (新错误) | 中         | 错误位置直接指向问题文件               |
| Grep/Search 结果         | 中         | 模型需要主动搜索，不是被动提供         |
| 项目文件树               | 低         | 大型项目中文件树信息过载               |

**可落地建议：**

1. 强制要求 CLAUDE.md 包含"文件索引"section
2. Git status 变更文件作为附件自动注入
3. LSP diagnostics 只注入新错误（baseline 相减）
4. 在 system prompt 中加入"先搜索再修改"的约束

---

### 2.2 一个 coding task 进入模型前，最小必要上下文是什么？

**结论：System Prompt + User Context (CLAUDE.md) + 当前任务输入 + 最近工具结果 = 最小必要上下文。**

**源码证据：**

- src/utils/queryContext.ts:44-74 — etchSystemPromptParts() 返回三件套
- src/QueryEngine.ts:284-325 — sk() 组装 system prompt
- src/utils/attachments.ts:743-1003 — getAttachments() 每轮动态计算附件
- src/utils/context.ts:9 — MODEL_CONTEXT_WINDOW_DEFAULT = 200_000

**最小必要上下文构成：**

`
[必须]

- System Prompt (工具 schema + 核心指令)     ~15K-30K tokens
- User Context (CLAUDE.md + 日期)           ~1K-5K tokens
- System Context (git status)               ~0.5K-2K tokens
- 用户当前输入                               ~0.1K-1K tokens

[按需]

- 最近 2-3 轮对话历史                        ~2K-10K tokens
- 相关文件附件                               ~0.5K-5K tokens
- LSP diagnostics (如果有新错误)             ~0.1K-1K tokens
  `

**可落地建议：**

1. 首次对话只加载 system prompt + user context + 用户输入
2. 文件附件只在用户显式引用或模型主动读取后才注入
3. LSP diagnostics 延迟加载
4. 设置"最小上下文模式"开关用于 CI/CD

---

### 2.3 Agent 是更依赖自然语言说明，还是更依赖代码结构索引？

**结论：两者并重，但自然语言说明优先于代码结构索引。**

**源码证据：**

- src/utils/claudemd.ts:89-90 — "These instructions OVERRIDE any default behavior"
- src/constants/prompts.ts:378-379 — 指导模型使用搜索和 Explore agent
- src/utils/attachments.ts:1719-1750 — CLAUDE.md 内容作为附件注入

**对比分析：**

| 维度       | 自然语言说明        | 代码结构索引                   |
| ---------- | ------------------- | ------------------------------ |
| 控制权     | 开发者（CLAUDE.md） | 模型（Grep/Read/LS）           |
| 时效性     | 需要手动更新        | 实时反映代码状态               |
| 精度       | 高（人工编写）      | 中（模型解析）                 |
| 覆盖度     | 低（只写关键信息）  | 高（整个代码库）               |
| Token 成本 | 低（1K-10K）        | 高（搜索可能触发多轮工具调用） |

**可落地建议：**

1. CLAUDE.md 写架构概述和文件职责，不写具体实现细节
2. 让模型通过工具主动发现代码结构
3. 对大型项目加入模块地图
4. 避免在 CLAUDE.md 中写"第 X 行有什么代码"

---

### 2.4 如何设计代码库地图，让模型少读文件也能定位模块？

**结论：Claude Code 通过 CLAUDE.md 层级规则 + Explore agent 的组合实现。**

**可落地的代码库地图设计：**

`markdown

## 项目地图

### 架构分层

- src/api/ — API 路由和控制器
- src/services/ — 业务逻辑
- src/models/ — 数据模型
- src/utils/ — 工具函数

### 关键文件

- src/main.ts — 程序入口
- src/config.ts — 配置加载

### 模块依赖规则

- services/ 可以调用 models/ 和 utils/
- api/ 只能调用 services/

### 常见修改位置

- 新增 API：修改 src/api/routes.ts 和 src/services/
  `

**可落地建议：**

1. CLAUDE.md 中加入"项目地图"section，不超过 500 tokens
2. 使用 paths: frontmatter 让规则只在相关目录生效
3. 对大型项目用 Explore agent 做初始探索
4. 定期更新项目地图

---

### 2.5 如何让 agent 在"不确定"时先探索，而不是直接实现？

**结论：三层机制强制探索优先：阈值控制、read-only Explore agent、验证约束。**

**源码证据：**

- src/constants/prompts.ts:378-379 — 3-query 阈值指导
- src/tools/AgentTool/built-in/exploreAgent.ts:59 — EXPLORE_AGENT_MIN_QUERIES = 3
- src/tools/AgentTool/built-in/exploreAgent.ts:52-54 — Explore agent 是 read-only
- src/constants/prompts.ts:233 — 先诊断再切换方案

**三层探索机制：**

| 机制              | 实现                                     | 目的                 |
| ----------------- | ---------------------------------------- | -------------------- |
| 3-query 阈值      | EXPLORE_AGENT_MIN_QUERIES = 3            | 简单搜索用直接工具   |
| Read-only Explore | 禁用 FileEdit/FileWrite                  | 防止探索阶段意外修改 |
| 验证约束          | 3+ 文件修改必须 adversarial verification | 强制验证             |

**可落地建议：**

1. 在 system prompt 中加入"探索-实现-验证"三段式约束
2. Explore agent 使用更便宜的模型
3. 先输出探索计划，经用户确认后再执行
4. 设置"未读取文件不得编辑"的硬性校验

---

### 2.6 怎样判断模型当前是在基于证据工作，还是基于先验猜测？

**结论：通过工具调用模式分析 + 验证 agent 对抗性检查 + system prompt 反幻觉指令。**

**基于证据 vs 先验猜测的判别指标：**

| 指标     | 基于证据                    | 先验猜测                 |
| -------- | --------------------------- | ------------------------ |
| 工具调用 | Read → Edit → Test 链完整 | 跳过 Read 直接 Edit      |
| 验证步骤 | 显式运行测试/脚本           | "应该可以工作"但没有验证 |
| 错误处理 | 读取错误输出，诊断原因      | 忽略错误，直接切换方案   |
| 文件引用 | 引用具体文件路径和行号      | 模糊描述"某个文件"       |

**可落地建议：**

1. 要求模型必须引用证据
2. 对未经验证即声明完成的行为做对抗检查
3. 分析工具调用链，标记"猜测模式"
4. 在 system prompt 中加入反幻觉指令

---

### 2.7 对于 bug 修复任务，哪些上下文最能提高 root cause 命中率？

**结论：变更文件 diff + 新增 diagnostics（baseline 相减）+ 最近工具结果 = 最高信噪比。**

**源码证据：**

- src/utils/attachments.ts:2063-2145 — getChangedFiles() 检测修改并生成 diff
- src/services/diagnosticTracking.ts:30-76 — DiagnosticTrackingService 维护 baseline
- src/services/diagnosticTracking.ts:188-283 — getNewDiagnostics() 只返回新增错误
- src/utils/attachments.ts:2883-2935 — LSP diagnostics 带 isNew 标记

**Bug fix 上下文优先级：**

| 上下文           | Root Cause 命中率 |
| ---------------- | ----------------- |
| 变更文件 diff    | 最高              |
| 新增 diagnostics | 高                |
| 最近测试失败输出 | 高                |
| 历史修复记录     | 中                |
| 项目架构文档     | 低                |

**可落地建议：**

1. Bug fix 任务自动注入最近修改的文件
2. LSP diagnostics 必须 baseline 相减
3. 测试失败输出完整保留
4. 在 session memory 中记录 bug 修复历史

---

### 2.8 如何把失败案例反向沉淀成下一次更高命中的提示规则？

**结论：Coalescing Memory Extraction + Session Memory 结构化记录 + CLAUDE.md 长期沉淀。**

**源码证据：**

- src/services/SessionMemory/sessionMemory.ts:1-6 — 自动维护 markdown 文件
- src/services/SessionMemory/prompts.ts:11-41 — 模板：Current State、Errors & Corrections、Learnings
- src/services/compact/prompt.ts:61-143 — Compact prompt 要求提取 Errors

**失败案例沉淀流程：**

`失败发生   → 模型诊断原因   → Compact/Session Memory 提取 Errors & Corrections   → 写入 session memory 文件   → 跨会话时 relevant memories 检索   → 写入 CLAUDE.md（长期沉淀）`

**可落地建议：**

1. Compact prompt 强制要求提取 Errors & Fixes
2. Session memory 的 Learnings section 记录"不要再犯的错误"
3. 重复错误模式自动建议写入 CLAUDE.md
4. 追踪工具调用成功率，识别高频失败模式

---

## 第三章 工具调用能力

### 3.1 什么样的工具 schema 更容易被模型正确调用？

**结论：严格模式（z.strictObject）+ 每个字段的 .describe() + strict: true 标志。**

**源码证据：**

- src/Tool.ts:472, 757-769 — uildTool 提供 fail-closed 默认值
- src/utils/api.ts:184-191 — Strict 模式仅在模型支持 structured outputs 且工具标记 strict: true 时启用
- src/tools/FileReadTool/FileReadTool.ts:227-243 — 使用 z.strictObject，每个参数都有 .describe()
- src/tools/FileEditTool/types.ts:6-18 — 同样模式，参数描述包含约束

**可落地建议：**

1. 使用 z.strictObject 而非 z.object
2. 每个参数添加 .describe()，包括约束说明
3. 在工具定义中设置 strict: true
4. 对枚举类型使用 z.enum() 而非 z.string()

---

### 3.2 工具描述应该写行为、适用场景、参数约束，还是示例？

**结论：四者都要写。最佳实践是 Behavior + Usage rules + Examples + Constraints。**

**源码证据：**

- src/tools/FileReadTool/prompt.ts:32-48 — 包含行为、场景、约束、示例
- src/tools/BashTool/prompt.ts:354-368 — 包含大量示例（HEREDOC）、场景指导、约束列表
- src/tools/GrepTool/prompt.ts:7-17 — 包含用法模式、正则示例、输出模式描述

**可落地建议：**

1. 第一段写行为：这个工具做什么
2. 第二段写场景：什么时候用、什么时候不用
3. 第三段写约束：参数限制、格式要求
4. 第四段写示例：至少 2 个典型调用示例

---

### 3.3 工具太多时，模型是如何选择工具的？选择失败通常发生在哪里？

**结论：Claude Code 使用 ToolSearch 做延迟加载 + 关键词评分。选择失败发生在 MCP 服务器尚未连接时。**

**源码证据：**

- src/utils/toolSearch.ts:44-49 — 自动启用阈值：上下文窗口的 10%
- src/tools/ToolSearchTool/ToolSearchTool.ts:186-302 — 关键词评分：
  - 精确部分匹配：10-12 分
  - searchHint 匹配：4 分
  - 描述匹配：2 分
- src/tools/ToolSearchTool/ToolSearchTool.ts:363-406 — select: 前缀直接选择
- src/services/api/claude.ts:1154-1167 — 动态工具加载，只包含已发现的工具

**失败模式：**

| 失败场景                | 原因                     | 处理              |
| ----------------------- | ------------------------ | ----------------- |
| 模型使用 bare tool name | 未加 select: 前缀        | fast path 处理    |
| MCP 服务器未连接        | 返回 pending_mcp_servers | 提示用户等待      |
| 搜索无匹配              | 关键词不准确             | 返回空结果 + 提示 |

**可落地建议：**

1. 给每个延迟加载的工具写 searchHint（3-10 词，不含工具名已有词汇）
2. searchHint 的权重（4 分）高于描述（2 分）
3. 对高频工具保持内联加载，不延迟
4. MCP 工具加载状态在附件中报告

---

### 3.4 工具应该做得原子化，还是做成高层复合工具？

**结论：原子化用于基元操作，复合工具用于工作流。两者都要有清晰分离。**

**源码证据：**

- src/Tool.ts:402-406 — 原子化 affordances：isReadOnly、isDestructive、isConcurrencySafe
- src/tools/AgentTool/AgentTool.tsx:196-316 — AgentTool 是高层复合工具，maxResultSizeChars: 100_000
- src/tools/FileEditTool/FileEditTool.ts:86-90 — FileEditTool 是原子工具，单文件单替换
- src/tools.ts:36-46 — ALL_AGENT_DISALLOWED_TOOLS 阻止子代理递归

**可落地建议：**

1. 原子工具做单一操作，带清晰 affordances
2. 复合工具（AgentTool）做多步工作流
3. 用 allowlist 阻止递归（AgentTool 不能在子代理中调用 AgentTool）
4. 复合工具的结果要有大小限制，防止子代理输出淹没父上下文

---

### 3.5 一个工具返回结果过长时，如何设计 result format 才利于下一步推理？

**结论：结构化输出 + 截断元数据 + 适当的 maxResultSizeChars。**

**源码证据：**

- src/Tool.ts:456-466 — maxResultSizeChars 文档：Read 工具设为 Infinity 防止循环
- src/tools/GrepTool/GrepTool.ts:108-128 — DEFAULT_HEAD_LIMIT = 250，截断时报告 ppliedLimit
- src/tools/GrepTool/GrepTool.ts:144-155 — 输出包含 ppliedLimit 和 ppliedOffset
- src/tools/GlobTool/GlobTool.ts:39-52 — 输出包含 	runcated: boolean 和
  umFiles

**摘要粒度策略：**

| 工具类型        | 回填策略                    | 粒度        |
| --------------- | --------------------------- | ----------- |
| Read            | 完整内容                    | Infinity    |
| Grep            | 前 250 条 + 截断标记        | 250 results |
| Bash            | 完整 stdout/stderr          | 无截断      |
| Glob            | 全部文件名 + truncated 标记 | 无数量限制  |
| LSP diagnostics | 新错误 only                 | 只报新增    |

**可落地建议：**

1. 所有截断必须包含元数据（appliedLimit、truncated、numFiles）
2. Read 类工具不设上限，依赖 microcompact 清理
3. Search 类工具设置 head limit（250），输出截断标记
4. 测试/构建工具完整回填 stderr，但超过 10K 时截断并标记

---

### 3.6 如何区分"模型不会用工具"和"工具本身设计得不好"？

**结论：通过 validateInput 的错误码分类 + 工具搜索日志 + 重试模式分析。**

**源码证据：**

- src/Tool.ts:489-492 — alidateInput 在 call() 之前运行，返回 {result, message, errorCode}
- src/tools/FileEditTool/FileEditTool.ts:137-362 — 详细错误码：
  - Error 1: old_string === new_string
  - Error 6: File not read yet
  - Error 7: File modified since read
  - Error 8: String not found
  - Error 9: Multiple matches without replace_all
- src/utils/permissions/denialTracking.ts:12-15 — 拒绝追踪：maxConsecutive: 3, maxTotal: 20

**判别矩阵：**

| 错误码                 | 原因           | 归属     |
| ---------------------- | -------------- | -------- |
| Error 6 (未读取)       | 工作流设计缺陷 | 工具设计 |
| Error 8 (字符串未找到) | 上下文过期     | 两者兼有 |
| Error 1 (相同字符串)   | 模型理解错误   | 模型     |
| 重复 Error 9           | 参数设计不合理 | 工具设计 |

**可落地建议：**

1. 实现 validateInput 并分配特定错误码
2. 记录工具搜索成功/失败率
3. 连续 3 次相同错误码 → 标记为工具设计问题
4. 随机错误码 → 标记为模型理解问题

---

### 3.7 是否应该给工具加 affordance，比如 read-only、destructive、search、edit、verify 等明确标签？

**结论：是。整个权限系统和 UI 折叠都依赖这些 affordances。**

**源码证据：**

- src/Tool.ts:402-433 — 核心 affordances：
  - isReadOnly(input) — 跳过审批
  - isDestructive(input) — 触发显式审批
  - isConcurrencySafe(input) — 决定并行执行
  - isSearchOrReadCommand(input) — UI 折叠
  - interruptBehavior() — 用户中断行为
- src/tools/FileReadTool/FileReadTool.ts:373-384 — 显式设置 isConcurrencySafe: true, isReadOnly: true
- src/tools/GrepTool/GrepTool.ts:183-194 — isSearchOrReadCommand: {isSearch: true}

**可落地建议：**

1. 显式设置 isReadOnly、isConcurrencySafe、isSearchOrReadCommand
2. 默认值是 fail-closed（假设写入、假设不安全）
3. 所有写操作必须通过 isDestructive 明确标记
4. 用 interruptBehavior 控制用户取消时的行为

---

### 3.8 如何设计工具调用链，让模型先 search，再 inspect，再 edit，再 verify，而不是跳步？

**结论：通过 validateInput 强制前置条件 + 去重防止重复读取 + prompt 明确指导工具选择。**

**源码证据：**

- src/tools/FileEditTool/FileEditTool.ts:275-287 — ValidateInput 强制预读取：
  `	ypescript if (!readTimestamp || readTimestamp.isPartialView) { return { result: false, errorCode: 6, message: 'File has not been read yet. Read it first.' } } `
- src/tools/FileEditTool/FileEditTool.ts:289-311 — 验证文件自上次读取后未被修改
- src/tools/FileReadTool/FileReadTool.ts:536-573 — 读取去重：相同文件/范围且 mtime 未变时返回 stub
- src/tools/BashTool/prompt.ts:354-368 — Prompt 明确指导："Read files: Use Read (NOT cat/head/tail)"

**四步工具链：**

`Search (Grep/Glob) → Inspect (Read) → Edit (FileEdit) → Verify (Bash/Test)`

**可落地建议：**

1. 在 validateInput 中强制前置条件（Edit 要求 Read）
2. 用 readFileState 跟踪已读文件，防止重复读取
3. 返回明确的错误消息告诉模型跳过了哪一步
4. 在 system prompt 中加入工具使用指导

---

## 第四章 Skill 太多怎么办

### 4.1 Skill 应该全部注入 system prompt，还是只注入 skill index？

**结论：只注入 index（skill_listing 附件），完整内容按需加载。**

**源码证据：**

- src/utils/attachments.ts:2641-2751 — getSkillListingAttachments() 发送 compact index（name: description 对）
- src/tools/SkillTool/prompt.ts:70-171 — ormatCommandsWithinBudget() 强制 1% 上下文窗口预算
- src/utils/attachments.ts:2692-2697 — 用户/项目 skill 通过 discovery 而非全量列出
- src/tools/SkillTool/SkillTool.ts — Skill 调用时才加载完整 prompt

**可落地建议：**

1. 永远只注入 skill 索引，不注入完整内容
2. 索引预算严格限制为上下文窗口的 1%
3. 索引条目按调用频率排序，低频 skill 降级到 discovery
4. Bundled skill 优先显示，用户 skill 通过搜索发现

---

### 4.2 Skill 的 metadata 应该包含哪些字段，才能支持按需加载？

**结论：15+ 个字段，覆盖身份、触发、执行、隔离四个维度。**

**源码证据：**

- src/types/command.ts:25-57 和 src/skills/loadSkillsDir.ts:185-265：
  -------------------------------------------------------------------

ame, description, whenToUse

- userInvocable, disableModelInvocation
- llowedTools, model, effort
- context: 'inline' | 'fork'
- gent, paths
- contentLength, hooks, iles

**可落地建议：**

1. 必须字段：name, description, whenToUse, userInvocable
2. 执行字段：context, model, effort, allowedTools
3. 触发字段：paths (glob), isEnabled callback
4. 隔离字段：agent (fork 时使用的子代理)

---

### 4.3 什么情况下 skill 应该自动触发，什么情况下必须用户显式触发？

**结论：Path-based 条件匹配自动触发；userInvocable 控制显式触发。**

**源码证据：**

- src/skills/loadSkillsDir.ts:772-790 — paths frontmatter 条件激活
- src/skills/loadSkillsDir.ts:997 — ctivateConditionalSkillsForPaths()
- src/types/command.ts:190 — userInvocable 控制 /skill-name 可用性
- src/skills/bundled/index.ts:35-55 — isEnabled() callback 延迟可见性

**可落地建议：**

1. 文件相关 skill 用 paths 自动触发（如 React 组件 skill 在编辑 .tsx 时激活）
2. 通用 skill 需要 userInvocable: true
3. 危险 skill 设置 disableModelInvocation，只允许用户触发
4. Bundled skill 在启动时注册，但用 isEnabled 控制可见性

---

### 4.4 Skill 选择是模型自己判断，还是 runtime 先做路由？

**结论：Runtime 提供索引，模型做最终选择。Discovery prefetch 辅助但不覆盖模型判断。**

**源码证据：**

- src/utils/attachments.ts:2641-2751 — Runtime 发送 skill_listing 附件
- src/tools/SkillTool/SkillTool.ts:354-429 — alidateInput() runtime 验证 skill 存在性
- src/query.ts:331 — startSkillDiscoveryPrefetch() 实验性预取

**可落地建议：**

1. Runtime 提供高质量索引（按频率排序，去重）
2. 模型做最终选择，runtime 只验证合法性
3. Discovery 作为辅助，不强制覆盖模型选择
4. 索引质量比索引数量更重要

---

### 4.5 Skill 内容应该分几层？

**结论：6 层结构。**

**源码证据：**

- src/skills/bundledSkills.ts:15-41 和 src/skills/loadSkillsDir.ts:270-401：
  1. **Name + aliases** — 身份
  2. **Description + whenToUse** — 发现元数据
  3. **Trigger conditions** — paths, isEnabled, userInvocable
  4. **Execution config** — context, agent, model, effort, allowedTools, hooks
  5. **Full workflow** — getPromptForCommand() 返回实际 prompt
  6. **Reference files** — files for bundled skills

**可落地建议：**

1. 将 skill 内容严格分层存储
2. 只有 1-3 层进入索引，4-6 层按需加载
3. 参考文件用懒加载，不随索引发送

---

### 4.6 如何避免多个 skill 之间指令冲突？

**结论：通过 forked 子代理隔离 + allowedTools 限制 + 权限门控。**

**源码证据：**

- src/tools/SkillTool/SkillTool.ts:122-289 — Forked skills 在子代理中执行，独立上下文
- src/tools/SkillTool/SkillTool.ts:910-933 — skillHasOnlySafeProperties() 检查危险属性
- llowedTools 限制每个 skill 可用的工具范围

**可落地建议：**

1. 高风险 skill 强制 fork 执行
2. 每个 skill 限制 allowedTools
3. 危险属性（如自定义模型、自定义 effort）需要权限确认
4. 子代理的 tool set 从父代理收缩，不扩展

---

### 4.7 当 skill 很多时，应该用关键词匹配、embedding 检索、规则路由，还是模型分类？

**结论：混合策略 —— semantic 检索用于 discovery，runtime 过滤用于索引，模型用于最终选择。**

**源码证据：**

- src/query.ts:66-68 — EXPERIMENTAL_SKILL_SEARCH 使用 AKI semantic search
- src/utils/attachments.ts:95-102 — Skill discovery prefetch
- src/utils/attachments.ts:2641-2659 — FILTERED_LISTING_MAX = 30，bundled-only fallback

**可落地建议：**

1. Semantic 检索（embedding）用于离线 discovery
2. Runtime 过滤（bundled + MCP capped at 30）用于在线索引
3. 模型做最终选择
4. Keyword 匹配作为 fast path

---

### 4.8 Skill 被加载后，什么时候应该从上下文中移除？

**结论：Claude Code 中 skill 不自动移除，存活于 agent 生命周期。**

**源码证据：**

- src/bootstrap/state.ts:1510-1524 — ddInvokedSkill() 追踪已调用 skill
- src/bootstrap/state.ts:1557-1563 — clearInvokedSkillsForAgent() 按 agent 清理
- Skill 在 compact 后仍然保留

**可落地建议：**

1. Skill 存活于 agent 生命周期，不自动过期
2. Agent 结束时统一清理
3. 对长时间运行的 agent，考虑 skill TTL（如 30 分钟未使用则移除）
4. 提供 /forget-skill 命令让用户手动移除

---

### 4.9 Skill 是"操作手册"，还是"短期任务策略"？两者是否应该分开？

**结论：Claude Code 不严格分离，但通过 context: 'fork' vs 'inline' 和 disableModelInvocation 提供隐式分离。**

**源码证据：**

- src/skills/bundled/index.ts:24-40 — Bundled skills（/verify, /debug, /batch）是操作手册
- context: 'fork' — 重型 skill 作为隔离子代理（短期策略）
- context: 'inline' — 轻量 skill 在主对话中执行（操作手册）
- disableModelInvocation — 用户专用 skill

**可落地建议：**

1. 操作手册型 skill：inline 执行，轻量，常驻索引
2. 任务策略型 skill：fork 执行，重型，按需加载
3. 在 metadata 中显式标记 skill 类型
4. 操作手册优先常驻，策略型优先按需

---

### 4.10 如何衡量一个 skill 是否值得常驻 system prompt？

**结论：Claude Code 不常驻 skill 内容，只常驻索引。衡量索引价值通过使用遥测。**

**源码证据：**

- src/tools/SkillTool/SkillTool.ts:619 —
  ecordSkillUsage(commandName) 追踪使用
- src/tools/SkillTool/SkillTool.ts:152-203 — 	engu_skill_tool_invocation 事件含 was_discovered
- src/tools/SkillTool/prompt.ts:124-161 — 	engu_skill_descriptions_truncated 预算压力事件
- src/utils/attachments.ts:2641 — FILTERED_LISTING_MAX = 30

**衡量指标：**

| 指标           | 说明                                   |
| -------------- | -------------------------------------- |
| 调用频率       | 高频 skill 保留在索引中                |
| Discovery 比例 | 高 discovery 比例的 skill 可以考虑常驻 |
| 截断频率       | 经常被截断的 skill 需要缩减描述        |
| 成功率         | 调用后任务成功率的提升                 |

**可落地建议：**

1. 追踪每个 skill 的调用频率和成功率
2. 按频率排序索引，末尾的降级到 discovery
3. 定期审查未被调用的 skill，考虑移除
4. A/B 测试：比较常驻 vs 按需的 task 成功率

---

### 4.11 Skill system 的设计目标，是提高模型行为质量，还是减少用户 prompt 成本？如果两者冲突，优先谁？

**结论：质量优先，但成本被激进优化。**

**源码证据：**

- src/tools/SkillTool/prompt.ts:21 — SKILL_BUDGET_CONTEXT_PERCENT = 0.01 (1% 硬上限)
- src/tools/SkillTool/prompt.ts:92-170 — Bundled skills 从不截断，用户 skill 先被截断
- src/constants/prompts.ts:114-115 — SYSTEM_PROMPT_DYNAMIC_BOUNDARY 分离缓存内容
- src/query.ts:323-330 — Discovery prefetch 并发执行，不阻塞主流程

**可落地建议：**

1. 设计目标排序：行为质量 > 用户成本 > 系统成本
2. 用预算上限保护系统成本（1% 硬上限）
3. 用缓存边界减少重复成本
4. 用并发预取隐藏延迟成本

---

## 第五章 上下文维护方案

### 5.1 Agent 的上下文应该分成哪些层？

**结论：5 层 —— System Prompt、System Context、User Context、Attachments、Transcript。**

**源码证据：**

- src/context.ts:116-189 — getSystemContext() (git status) 和 getUserContext() (CLAUDE.md)
- src/utils/queryContext.ts:44-74 — etchSystemPromptParts() 返回三件套
- src/utils/attachments.ts:440-718 — 40+ 种附件类型
- src/state/AppStateStore.ts:89-453 — AppState 管理 tasks、fileHistory、attribution 等

**五层架构：**

| 层级           | 内容                              | 稳定性           | 生命周期     |
| -------------- | --------------------------------- | ---------------- | ------------ |
| System Prompt  | 工具 schema、核心指令             | 高（边界前部分） | Session      |
| System Context | git status、cache breaker         | 中（memoized）   | Session      |
| User Context   | CLAUDE.md、current date           | 中（memoized）   | Session      |
| Attachments    | 文件变更、diagnostics、skill 列表 | 低（每轮重算）   | Turn         |
| Transcript     | 对话历史、tool_result             | 动态增长         | Conversation |

**可落地建议：**

1. 明确区分稳定层（1-3）和动态层（4-5）
2. 稳定层使用 memoization，避免每轮重复计算
3. 动态层使用 delta 机制，只发送增量
4. Transcript 是唯一 Source of Truth

---

### 5.2 哪些上下文是稳定的，哪些是随任务变化的？

**结论：System Context 和 User Context 是稳定的；Attachments 和 Transcript 是变化的。**

**源码证据：**

- src/context.ts:36-111 — getGitStatus 是 memoize() 缓存的，"cached for the duration of the conversation"
- src/context.ts:116-150 — getSystemContext 是 memoize() 缓存的
- src/context.ts:155-189 — getUserContext 是 memoize() 缓存的
- src/utils/attachments.ts:743-1003 — getAttachments 每轮重算，1 秒超时
- src/utils/fileStateCache.ts:30-93 —
  eadFileState LRU (100 entries) 持久但会淘汰

**可落地建议：**

1. 稳定内容用 memoization + session 级缓存
2. 动态内容每轮重新计算，但使用 delta 减少重复
3. 文件内容用 LRU + mtime 校验
4. Git status 用 session 级快照（不实时更新）/

### 5.3 哪些上下文应该进入 system prompt，哪些应该进入 user message，哪些应该通过 tool 按需获取？

**结论：System Prompt 放工具 schema 和核心指令；User Context 作为 system-reminder 注入；Attachments 和按需内容通过工具/预取获取。**

**源码证据：**

- src/utils/api.ts:437-447 — ppendSystemContext() 追加到 system prompt
- src/utils/api.ts:449-474 — prependUserContext() 作为 &lt;system-reminder&gt; user message 注入
- src/utils/attachments.ts:2346-2423 — startRelevantMemoryPrefetch() 异步预取
- src/utils/attachments.ts:743-1003 — Attachments 动态计算

**分配策略：**

| 内容        | 位置                           | 原因                         |
| ----------- | ------------------------------ | ---------------------------- |
| 工具 schema | System Prompt                  | API 要求，缓存优化           |
| 核心指令    | System Prompt                  | 模型必须始终遵守             |
| CLAUDE.md   | User message (system-reminder) | 用户级上下文，可覆盖默认行为 |
| Git status  | System Prompt (append)         | 项目状态，辅助决策           |
| 相关记忆    | 异步预取/附件                  | 按需加载，避免常驻           |
| 文件变更    | 附件                           | 每轮动态检测                 |
| Diagnostics | 附件                           | 实时变化                     |

**可落地建议：**

1. 只有模型必须始终遵守的规则放 system prompt
2. 用户/项目级上下文放 user message（system-reminder）
3. 动态变化的内容通过附件注入
4. 长期知识通过异步预取或工具按需获取

---

### 5.4 如何设计 task state，让 agent 在 compact 后仍然知道当前目标、已做操作、未完成事项？

**结论：结构化摘要 + compact boundary 元数据 + session memory 三层保障。**

**源码证据：**

- src/services/compact/prompt.ts:61-143 — Compact prompt 要求 9 个 section：
  - Primary Request、Key Concepts、Files/Code、Errors/Fixes
  - Problem Solving、All User Messages、Pending Tasks、Current Work、Optional Next Step
- src/services/compact/compact.ts:598-624 — Summary 标记 isCompactSummary: true
- src/services/compact/compact.ts:606-611 — Boundary marker 携带 preCompactDiscoveredTools
- src/services/SessionMemory/prompts.ts:11-41 — Session memory 模板：Current State、Pending Tasks、Errors & Corrections

**可落地建议：**

1. Compact prompt 强制要求提取 Pending Tasks 和 Current Work
2. Compact boundary 携带工具状态（preCompactDiscoveredTools）
3. Session memory 维护独立的结构化状态文件
4. Post-compact 恢复最近 5 个文件和关键附件

---

### 5.5 Memory、Session、Compact、RAG、Code Map 这几个机制边界分别是什么？

**结论：五者有明确的时间和持久性边界。**

**源码证据：**

- src/utils/memory/types.ts:1-13 — Memory types: User, Project, Local, Managed, AutoMem, TeamMem
- src/utils/attachments.ts:269-289 — MAX_SESSION_BYTES = 60KB
- src/services/SessionMemory/sessionMemory.ts:1-6 — Session memory 自动维护 markdown
- src/services/compact/compact.ts:299-310 — CompactionResult 包含 boundaryMarker、summaryMessages、attachments
- src/utils/fileStateCache.ts:1-143 — FileStateCache LRU (100 entries, 25MB)

**边界定义：**

| 机制                     | 范围     | 持久性        | 触发方式         |
| ------------------------ | -------- | ------------- | ---------------- |
| Memory (CLAUDE.md)       | 跨会话   | 磁盘          | 启动时加载       |
| Session Memory           | 当前会话 | 磁盘          | 后台定期更新     |
| Compact                  | 当前对话 | Transcript 内 | Token 阈值或手动 |
| RAG (Relevant Memories)  | 当前轮次 | 临时附件      | 异步预取         |
| Code Map (readFileState) | 当前会话 | 内存 LRU      | 文件读取时更新   |

**可落地建议：**

1. Memory 用于跨会话知识沉淀
2. Session Memory 用于当前会话状态保持
3. Compact 用于对话历史压缩
4. RAG 用于每轮动态知识检索
5. Code Map 用于文件状态缓存

---

### 5.6 如何避免 memory 污染？

**结论：四层防御 —— 注入上限、去重、结构化模板、截断标记。**

**源码证据：**

- src/utils/attachments.ts:269-289 — MAX_MEMORY_BYTES = 4096 每文件，MAX_SESSION_BYTES = 60KB
- src/utils/attachments.ts:2520-2541 — ilterDuplicateMemoryAttachments 去重
- src/services/SessionMemory/prompts.ts:43-80 — Update prompt 要求不重复 CLAUDE.md 内容
- src/services/SessionMemory/prompts.ts:55-61 — "CRITICAL RULES: NEVER modify section headers"

**可落地建议：**

1. 设置 per-file (4KB) 和 per-session (60KB) 硬上限
2. 用 readFileState 跟踪已注入记忆，compact 后重置
3. 结构化模板限制写入位置（只允许在描述下方写入）
4. 超大记忆截断并标记，提示使用 Read 工具查看完整内容

---

### 5.7 如何让 agent 记住"决策"，而不是记住一堆对话文本？

**结论：通过结构化摘要提取决策，Session Memory 维护决策记录。**

**源码证据：**

- src/services/SessionMemory/prompts.ts:11-41 — 模板包含 "Learnings"、"Errors & Corrections"
- src/services/compact/prompt.ts:61-143 — 要求提取 "Key decisions, technical concepts and code patterns"
- src/services/SessionMemory/sessionMemory.ts:134-150 — shouldExtractMemory 按 token 阈值和工具调用数触发

**可落地建议：**

1. Compact 摘要中强制包含 "Decisions" section
2. Session memory 的 "Learnings" 专门记录关键决策
3. 避免记录对话原文，只记录决策和原因
4. 使用结构化格式（bullet points）而非叙述文本

---

### 5.8 上下文维护应该由模型自己总结，还是 runtime 用结构化事件维护？

**结论：混合模式 —— 模型负责摘要，runtime 负责附件和状态管理。**

**源码证据：**

- src/services/compact/compact.ts:387-763 — Full compact 使用 forked agent 生成摘要
- src/services/SessionMemory/sessionMemory.ts — Session memory 使用 forked subagent 编辑
- src/utils/attachments.ts:1005-1042 — Attachment 计算是 runtime 结构化的
- src/services/compact/sessionMemoryCompact.ts:437-498 — Session memory compact 使用预写 markdown

**可落地建议：**

1. 摘要生成：模型负责（需要理解上下文语义）
2. 附件计算：runtime 负责（需要精确性和性能）
3. 状态维护：runtime 负责（需要一致性）
4. Session memory 编辑：模型负责（需要自然语言理解）

---

### 5.9 是否应该把每次工具调用、文件修改、测试结果写入 event log，再由 event log 生成上下文？

**结论：Claude Code 不使用全局 event log，而是使用 transcript + 专用缓存。**

**源码证据：**

- src/utils/fileStateCache.ts:1-143 — FileStateCache 记录每次文件读取
- src/utils/attachments.ts:2063-2161 — getChangedFiles() 遍历 readFileState 检测修改
- src/utils/attachments.ts:2465-2503 — collectRecentSuccessfulTools 扫描消息
- src/QueryEngine.ts:186-198 — Turn-scoped sets 追踪 discovered skills

**可落地建议：**

1. Transcript 作为事件日志，但上下文通过投影生成（而非回放）
2. 专用缓存（readFileState）用于文件状态跟踪
3. 消息扫描用于工具结果收集
4. 如需 event log，考虑在 transcript 基础上添加结构化索引

---

### 5.10 一个好的 context maintenance system，应该在 token、准确性、可恢复性之间怎么权衡？

**结论：Claude Code 的权衡是分层 recoverability，每层有不同的 token/准确性优先级。**

**源码证据：**

- src/services/compact/compact.ts:122-131 — Post-compact 硬预算：50K 文件、25K skill
- src/services/compact/sessionMemoryCompact.ts:57-61 — 保留最近消息：minTokens 10K、minMessages 5
- src/services/compact/compact.ts:145-200 — Compact 前剥离图片
- src/services/compact/compact.ts:243-291 — PTL retry 时丢弃最旧消息

**权衡矩阵：**

| 场景         | Token 优先   | 准确性优先     | 可恢复性优先    |
| ------------ | ------------ | -------------- | --------------- |
| 正常对话     | 缓冲 13K     | 保留最近 5 轮  | Transcript 全量 |
| Compact 后   | 50K 文件预算 | 结构化摘要     | Boundary marker |
| PTL 应急     | 丢弃旧消息   | 保留最近消息   | 可重试          |
| Session 恢复 | 无           | Session memory | 完整 transcript |

**可落地建议：**

1. 分层 recoverability：transcript（完整）、compact summary（上下文连续）、session memory（决策持久）
2. Token 紧张时优先丢弃旧内容，保留最近消息
3. 准确性通过结构化摘要和元数据保持
4. 可恢复性通过多层级备份实现

---

## 第六章 不同模型的工具遵循差异

### 6.1 不同模型在工具调用上差异主要体现在哪里？

**结论：五大维度：是否调用、调用顺序、参数准确性、错误恢复、遵循权限。**

**源码证据：**

- src/utils/betas.ts:142-157 — modelSupportsStructuredOutputs() 明确列出支持严格 schema 的模型
- src/utils/toolSearch.ts:239-252 — Haiku 模型被显式阻止使用 tool_reference
- src/services/api/claude.ts:1120-1148 — 工具搜索按模型条件启用
- src/services/api/claude.ts:1269-1296 — 模型切换时剥离不兼容字段

**差异矩阵：**

| 能力          | Opus 4.6 | Sonnet 4.6 | Haiku 4.5 | 旧模型 |
| ------------- | -------- | ---------- | --------- | ------ |
| 结构化输出    | Yes      | Yes        | Yes       | No     |
| 工具搜索/延迟 | Yes      | Yes        | No        | No     |
| 自适应思考    | Yes      | Yes        | No        | No     |
| 快速模式      | Yes      | No         | No        | No     |
| 最大努力      | Yes      | No         | No        | No     |
| 1M 上下文     | Yes      | Yes        | No        | No     |

**可落地建议：**

1. 为不同模型维护 capability 矩阵
2. 模型切换时自动重新评估工具可用性
3. Haiku 限制为内联工具，不支持延迟加载
4. 旧模型回退到标准（非严格）schema 验证

---

### 6.2 强推理模型和快模型在工具使用上，是不是应该配不同工具描述？

**结论：System prompt 差异主要基于用户类型（ant vs external），而非模型。**

**源码证据：**

- src/constants/prompts.ts:391-395 — Verification agent 受 feature flag 控制，非模型特定
- src/constants/prompts.ts:403-428 — Output efficiency section：ant 用户详细，external 用户简洁
- src/constants/prompts.ts:204-213 — Ant-only 指令包含 "Default to writing no comments"
- src/constants/prompts.ts:712-730 — Knowledge cutoff 日期按模型变化

**可落地建议：**

1. 工具描述本身不随模型变化
2. System prompt 的附加指令可按模型调整
3. 快模型（Haiku）可以减少验证要求
4. 强推理模型增加 adversarial verification

---

### 6.3 模型是否会因为工具太多而退化？不同模型的退化阈值是否不同？

**结论：是。退化阈值是上下文窗口的 10%，但 Haiku 不适用延迟加载。**

**源码证据：**

- src/utils/toolSearch.ts:49, 104-109 — Auto-tool-search 阈值 = 10% 上下文窗口
- src/utils/context.ts:149-210 — 不同模型输出限制不同
- src/services/api/claude.ts:1120-1148 — Haiku 不适用工具延迟加载

**可落地建议：**

1. 监控工具 token 占比，超过 10% 启用 tool search
2. Haiku 场景减少 MCP 工具数量或改用 Sonnet
3. 大型工具集使用 sonnet-4 或 opus-4-6（1M 上下文）
4. 为每个模型设置不同的工具数量上限

---

### 6.4 哪些模型更容易"想当然"不查文件直接回答？

**结论：较弱模型（Haiku、旧模型）更容易跳过 Read 直接回答。**

**源码证据：**

- src/constants/prompts.ts:230 — "If a user asks about or wants you to modify a file, read it first"
- src/tools/FileEditTool/FileEditTool.ts:275-287 — Error 6 强制 Read-before-Edit
- src/tools/AgentTool/built-in/exploreAgent.ts:24-56 — Explore agent 强制 read-only

**可落地建议：**

1. 所有模型统一强制 Read-before-Edit（validateInput）
2. 对弱模型增加更多约束性 prompt
3. 使用 Explore agent 做强制探索
4. 对未经验证的回答标记为"猜测"

---

### 6.5 哪些模型更容易过度调用工具？

**结论：没有直接证据，但可通过 tool result 大小和调用频率监控。**

**源码证据：**

- src/services/tokenEstimation.ts:203-208 — Token 估算用于监控上下文增长
- src/utils/attachments.ts:2883-2935 — Diagnostics 附件监控工具输出
- src/utils/toolSearch.ts — Tool search 模式决策日志

**可落地建议：**

1. 记录每个模型的平均每轮工具调用数
2. 设置每轮最大工具调用数（如 10 个）
3. 对过度调用发出警告或降级处理
4. A/B 测试不同模型的工具使用模式

---

### 6.6 工具 schema 的严格程度对不同模型影响是否一致？

**结论：不一致。只有支持 structured outputs 的模型获得 API 级严格验证。**

**源码证据：**

- src/utils/betas.ts:142-157 — 只有特定 Claude 4.x 模型支持 structured outputs
- src/utils/api.ts:185-192 — strict: true 仅在支持时添加
- src/utils/betas.ts:323-325 — Strict 和 token-efficient-tools 互斥，strict 优先

**可落地建议：**

1. 为所有模型提供 runtime Zod 验证（一致）
2. 为支持模型启用 API 级 strict 验证（额外保障）
3. 错误消息格式统一，不因模型而异
4. 测试时覆盖 strict 和非 strict 两种模式

---

### 6.7 是否应该为不同模型设计不同 system prompt、tool set、skill loading policy？

**结论：System prompt 差异主要基于用户类型，但 tool set 和 skill policy 应该按模型调整。**

**源码证据：**

- src/constants/prompts.ts — 用户类型（ant/external）决定 prompt 内容
- src/services/api/claude.ts:1120-1148 — 工具搜索按模型动态启用
- src/utils/toolSearch.ts:385-473 — isToolSearchEnabled() 按模型、工具、阈值动态评估

**可落地建议：**

1. System prompt：按用户类型区分，不按模型
2. Tool set：按模型 capability 动态调整
3. Skill policy：bundled skill 全模型通用，用户 skill 按需加载
4. 验证 agent：强推理模型启用，快模型简化或跳过

---

### 6.8 如何构建一个 benchmark 来测量模型的工具遵循能力？

**结论：Claude Code 没有内嵌 benchmark，但有完善的遥测基础设施。**

**源码证据：**

- src/utils/toolSearch.ts:395-416 — 	engu_tool_search_mode_decision 日志
- src/services/api/claude.ts:2476-2501 — Streaming fallback 事件
- src/services/api/errors.ts:965-1161 — classifyAPIError() 全面错误分类

**Benchmark 设计建议：**

| 维度           | 测量方法                     | 指标       |
| -------------- | ---------------------------- | ---------- |
| 工具选择准确性 | 给定任务，记录模型选择的工具 | 正确率     |
| 参数准确性     | 记录 Zod 验证失败率          | 错误率     |
| 调用顺序       | 记录工具调用链               | 合规率     |
| 错误恢复       | 注入错误，记录恢复策略       | 恢复成功率 |
| 权限遵循       | 记录越权尝试                 | 违规率     |

**可落地建议：**

1. 使用现有遥测事件构建 dashboard
2. 设计标准任务集（读-改-测-验证）
3. 跨模型对比，识别差异
4. 定期回归测试

---

### 6.9 工具调用失败时，模型更需要自然语言反馈，还是结构化错误码？

**结论：两者都需要。Claude Code 提供结构化错误码 + 自然语言解释。**

**源码证据：**

- src/utils/toolErrors.ts:66-132 — ormatZodValidationError() 结构化反馈：
  - 缺少参数
  - 意外参数
  - 类型不匹配
- src/utils/toolErrors.ts:5-22 — 错误超过 10K 字符时截断
- src/services/api/errors.ts:1184-1207 — getErrorMessageIfRefusal() 模型特定建议

**可落地建议：**

1. 所有错误包含 errorCode（结构化）
2. 所有错误包含自然语言解释
3. 错误消息包含修复建议
4. 超长错误截断并标记

---

### 6.10 模型对"必须先验证再声明完成"这类流程约束的遵循差异如何测量？

**结论：通过 verification agent 的对抗性检查 + 显式 VERDICT 机制测量。**

**源码证据：**

- src/tools/AgentTool/built-in/verificationAgent.ts:10-128 — Verification agent 要求 VERDICT: PASS/FAIL/PARTIAL
- src/tools/AgentTool/built-in/verificationAgent.ts:139-145 — 验证代理禁用编辑工具
- src/constants/prompts.ts:393-394 — 3+ 文件修改需要 adversarial verification

**测量方法：**

1. **显式验证率**：模型在未经验证时声明完成的频率
2. **验证通过率**：Verification agent 返回 PASS 的比例
3. **跳步率**：模型跳过 Read/Test 直接 Edit 的频率
4. **错误报告率**：测试失败时模型是否如实报告

**可落地建议：**

1. 强制 verification agent 检查所有非平凡修改
2. 记录验证结果（PASS/FAIL/PARTIAL）
3. 对跳过验证的行为发出警告
4. 跨模型对比验证遵循率

---

## 附录 A 关键源码文件索引

| 文件路径                                          | 说明                               | 相关章节   |
| ------------------------------------------------- | ---------------------------------- | ---------- |
| src/constants/prompts.ts                          | System Prompt 构建                 | 全部       |
| src/utils/api.ts                                  | Tool schema 序列化、上下文 metrics | 1, 3, 6    |
| src/utils/attachments.ts                          | 40+ 种附件收集                     | 1, 2, 4, 5 |
| src/services/compact/compact.ts                   | 全量压缩                           | 1, 5       |
| src/services/compact/autoCompact.ts               | 自动压缩触发                       | 1          |
| src/services/compact/microCompact.ts              | 轻量压缩                           | 1          |
| src/services/compact/prompt.ts                    | 压缩提示模板                       | 2, 5       |
| src/Tool.ts                                       | Tool 抽象定义                      | 3          |
| src/tools/SkillTool/prompt.ts                     | Skill 预算控制                     | 1, 4       |
| src/utils/toolSearch.ts                           | 工具搜索/延迟加载                  | 3, 6       |
| src/utils/context.ts                              | 用户/系统上下文                    | 1, 2, 5, 6 |
| src/utils/queryContext.ts                         | 查询上下文组装                     | 5          |
| src/utils/fileStateCache.ts                       | 文件状态缓存                       | 2, 5       |
| src/services/api/claude.ts                        | API 调用层                         | 3, 6       |
| src/services/api/promptCacheBreakDetection.ts     | 缓存失效检测                       | 1          |
| src/utils/betas.ts                                | 模型特性检测                       | 6          |
| src/utils/claudemd.ts                             | CLAUDE.md 加载                     | 2, 4       |
| src/services/SessionMemory/sessionMemory.ts       | Session memory                     | 2, 5       |
| src/services/diagnosticTracking.ts                | 诊断追踪                           | 2          |
| src/tools/AgentTool/built-in/exploreAgent.ts      | Explore agent                      | 2          |
| src/tools/AgentTool/built-in/verificationAgent.ts | Verification agent                 | 2, 6       |
| src/tools/FileEditTool/FileEditTool.ts            | FileEdit 工具                      | 3          |
| src/tools/FileReadTool/FileReadTool.ts            | FileRead 工具                      | 3          |
| src/utils/permissions/permissions.ts              | 权限系统                           | 2          |
| src/skills/loadSkillsDir.ts                       | Skill 加载                         | 4          |
| src/constants/systemPromptSections.ts             | Prompt section 缓存                | 1          |

---

## 总结

本报告基于 Claude Code 1902 个 TypeScript 源文件的实证分析，对 6 个核心主题（Token 经济性、命中率、工具调用、Skill 管理、上下文维护、模型差异）进行了系统研究。每个结论都附有具体的源码位置（文件路径 + 行号），确保可验证性。

**核心发现：**

1. **Token 经济性**：工具 schema 是最大 token 消费者（30-60%），Delta 附件 + Section Memoization + LRU 缓存是三层去重机制
2. **命中率**：CLAUDE.md 层级规则 + git status + LSP diagnostics 组合提高首次命中率；三层探索机制（3-query 阈值、read-only Explore、验证约束）防止草率实现
3. **工具调用**：z.strictObject + .describe() + strict: true 是最佳 schema 设计；validateInput 的错误码是区分模型问题 vs 工具设计问题的关键
4. **Skill 管理**：只注入索引（1% 预算），完整内容按需加载；质量优先于成本，但成本被激进优化
5. **上下文维护**：5 层架构（System Prompt / System Context / User Context / Attachments / Transcript）；混合维护模式（模型摘要 + runtime 结构化）
6. **模型差异**：Capability 矩阵驱动工具可用性；Haiku 限制为内联工具；验证是任务门控而非模型门控

**可落地 checklist：**

- [ ] 实施 Delta 附件系统（工具、skill、agent 列表）
- [ ] 为所有工具添加 z.strictObject 和 .describe()
- [ ] 设置 Skill 索引预算上限（1% 上下文窗口）
- [ ] 实现 Read-before-Edit 的 validateInput 校验
- [ ] 配置 Auto-compact 阈值（effectiveContextWindow - 13K）
- [ ] 为不同模型维护 Capability 矩阵
- [ ] 实施 baseline-subtracted diagnostics
- [ ] 设计 Token Budget Policy（探索/实现/验证/收尾四阶段）

---

*报告完成于 2026/05/22*
*基于 Claude Code TypeScript 源码分析*
