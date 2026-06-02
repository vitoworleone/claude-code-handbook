# Claude Code TypeScript 源码深度研究报告（源码锚定版）

> 生成时间：2026-05-20
> 研究对象：` code analysis\src`
> 对照源码：`
> 方法：逐文件阅读 TS 源码关键模块（共计约 1.3MB 核心代码），逐机制锚定到具体行号和代码块，按 `Claim → Source → Evidence → Extension` 结构输出。
> 重要说明：本报告不是抽象摘要，而是把每一个架构决策追溯到具体的 TypeScript 代码片段，给出行号范围和关键变量名。

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [源码规模与模块地图](#2-源码规模与模块地图)
3. [QueryEngine：会话生命周期管家](#3-queryengine会话生命周期管家)
4. [queryLoop：核心状态机深度拆解](#4-queryloop核心状态机深度拆解)
5. [上下文存活：四层压缩防线](#5-上下文存活四层压缩防线)
6. [StreamingToolExecutor：流式工具执行引擎](#6-streamingtoolexecutor流式工具执行引擎)
7. [工具系统：Tool 接口与 buildTool 工厂](#7-工具系统tool-接口与-buildtool-工厂)
8. [技能系统：从 SKILL.md 到运行时注入](#8-技能系统从-skillmd-到运行时注入)
9. [多 Agent 协作：AgentTool 与 Coordinator](#9-多-agent-协作agenttool-与-coordinator)
10. [错误恢复：五条 continue 路径详解](#10-错误恢复五条-continue-路径详解)
11. [Python 复刻版逐模块对照](#11-python-复刻版逐模块对照)
12. [结论与下一步](#12-结论与下一步)

---

## 1. 执行摘要

Claude Code 的 TypeScript 源码是一个**工业级 Agent Runtime Harness**，而不是一个简单的 LLM API 包装器。本轮深度研究揭示了以下核心发现：

1. **`queryLoop` 是一个精密的 `while(true)` 异步生成器**，管理着 9 种状态转换路径（`State` 对象），每种路径对应不同的错误恢复或上下文管理策略。
2. **上下文存活不是单一机制，而是四层防线**：Snip → Microcompact → Context Collapse → Autocompact，四层按顺序执行，互相补充而非互斥。
3. **`StreamingToolExecutor` 实现了真正的流式并发执行**，通过 `isConcurrencySafe` 标记在模型流式输出过程中就开始执行工具，并且具备完整的 sibling error abort 和 fallback discard 机制。
4. **工具系统通过 `buildTool` 工厂函数标准化了 60+ 个工具**，每个工具必须实现 19 个接口方法，涵盖执行、权限、验证、UI 渲染、搜索索引等全生命周期。
5. **技能系统支持三种执行上下文**：inline（注入当前对话）、fork（独立子代理）、agent（指定 agent 类型），并支持 Shell 命令动态注入（`!{command}` 语法）。

---

## 2. 源码规模与模块地图

### 顶层文件

| 文件 | 大小 | 核心职责 |
|:---|:---|:---|
| `main.tsx` | 808KB | CLI 入口、Commander 解析、会话初始化、REPL 启动 |
| `query.ts` | 70KB (1730 行) | 核心状态机 `queryLoop`，while(true) 循环 |
| `QueryEngine.ts` | 48KB (1296 行) | 会话容器，`submitMessage` 入口 |
| `Tool.ts` | 30KB (793 行) | 工具类型定义、`buildTool` 工厂、`ToolUseContext` |
| `commands.ts` | 26KB | Slash 命令注册与分发 |
| `context.ts` | 7KB | 系统/用户上下文构建 |

### 关键子目录

| 目录 | 文件数 | 核心职责 |
|:---|:---|:---|
| `tools/` | 42 个子目录 | 每个工具一个目录（AgentTool 235KB, BashTool, FileEditTool...） |
| `services/compact/` | 11 个文件 | 四层压缩引擎（autoCompact, microCompact, snipCompact, sessionMemoryCompact） |
| `services/tools/` | 4 个文件 | StreamingToolExecutor, toolExecution(62KB), toolHooks, toolOrchestration |
| `skills/` | 4 个文件 + bundled/ | 技能加载、解析、17 个内置技能 |
| `services/mcp/` | 若干 | MCP 协议客户端、配置解析、工具注册 |
| `coordinator/` | 1 个文件 | Coordinator Mode（多 agent 协调者） |

---

## 3. QueryEngine：会话生命周期管家

### Claim
QueryEngine 不是运行时内核，而是**会话级容器**，负责持有跨 turn 的状态并在每个 turn 开始时组装 query 所需的全部上下文。

### Source
`QueryEngine.ts:184-207`

```typescript
export class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]           // 跨 turn 持久化的消息列表
  private abortController: AbortController     // 可取消的控制器
  private permissionDenials: SDKPermissionDenial[] // 权限拒绝记录
  private totalUsage: NonNullableUsage         // 累计 token 消耗
  private readFileState: FileStateCache        // 文件读取缓存（跨 turn 共享）
  private discoveredSkillNames = new Set<string>() // 本 turn 发现的技能
  private loadedNestedMemoryPaths = new Set<string>() // 已加载的嵌套记忆路径
}
```

### Evidence

**3.1 submitMessage 入口（L209-428）**

每次用户提交消息时，`submitMessage` 会：

1. **重建 `processUserInputContext`**（L335-395）：包括消息列表、工具集、模型配置、thinking 配置等。
2. **处理 Slash 命令**（L416-428）：通过 `processUserInput()` 判断是否为 `/compact`、`/clear` 等命令。
3. **先持久化用户消息再调 API**（L450-463）：

```typescript
// L450-455: 关键设计——先写 transcript，再调 API
if (persistSession && messagesFromUserInput.length > 0) {
  const transcriptPromise = recordTranscript(messages)
  if (isBareMode()) {
    void transcriptPromise  // bare 模式下不阻塞
  } else {
    await transcriptPromise // 正常模式下等待写入完成
  }
}
```

这解决了一个关键的**可恢复性问题**：如果进程在 API 调用期间被杀死，transcript 中已经有了用户消息，`--resume` 可以恢复。

**3.2 QueryEngineConfig 中的 `snipReplay` 注入（L159-172）**

```typescript
snipReplay?: (
  yieldedSystemMsg: Message,
  store: Message[],
) => { messages: Message[]; executed: boolean } | undefined
```

这是 History Snip 机制的注入点。SDK 模式下 QueryEngine 直接截断消息列表以控制内存；REPL 模式下保留完整历史用于 UI 滚动回溯，通过 `projectSnippedView` 在 API 调用前做投影。

### Extension
Python 版的 `QueryEngine` 在 `cc/core/query_engine.py` 中实现了基础版本，但缺少：
- `readFileState` 跨 turn 共享的文件缓存
- `snipReplay` 注入点
- `permissionDenials` SDK 级权限拒绝追踪
- `discoveredSkillNames` 技能发现追踪

---

## 4. queryLoop：核心状态机深度拆解

### Claim
`queryLoop` 是一个携带 10 个状态变量的 `while(true)` 异步生成器，通过 `State` 对象在 7 个 `continue` 位点之间传递状态。

### Source
`query.ts:204-217`（State 类型定义）+ `query.ts:241-1729`（主循环体）

```typescript
type State = {
  messages: Message[]                              // 当前消息列表
  toolUseContext: ToolUseContext                    // 工具执行上下文
  autoCompactTracking: AutoCompactTrackingState     // 压缩追踪状态
  maxOutputTokensRecoveryCount: number             // max_output_tokens 恢复次数
  hasAttemptedReactiveCompact: boolean             // 是否已尝试响应式压缩
  maxOutputTokensOverride: number | undefined      // 提升后的 max_tokens
  pendingToolUseSummary: Promise<...> | undefined  // 待处理的工具摘要（异步）
  stopHookActive: boolean | undefined              // stop hook 是否激活
  turnCount: number                                // 有效轮次计数
  transition: Continue | undefined                 // 为什么上一轮 continue（用于测试断言）
}
```

### Evidence

**4.1 主循环的四个阶段**

每次循环迭代在 `query.ts:307-1728` 中执行：

| 阶段 | 行号范围 | 核心操作 |
|:---|:---|:---|
| Phase 1: 消息准备 | L365-548 | `applyToolResultBudget` → `snipCompact` → `microcompact` → `contextCollapse` → `autocompact` |
| Phase 2: 模型调用 | L559-863 | 构建 `StreamingToolExecutor` → `callModel()` 流式循环 → 收集 `toolUseBlocks` |
| Phase 3: 错误恢复 | L999-1357 | 处理 413/max_output/stop_hooks/token_budget |
| Phase 4: 工具执行 | L1360-1728 | `getRemainingResults()` 或 `runTools()` → 附件注入 → 消息队列消费 → 递归 |

**4.2 循环体中的 7 个 continue 位点**

每个 `continue` 对应一种状态转换，通过 `state.transition.reason` 标记：

| continue 位点 | reason | 行号 | 触发条件 |
|:---|:---|:---|:---|
| 1 | `collapse_drain_retry` | L1115 | Context Collapse 清空暂存队列后重试 |
| 2 | `reactive_compact_retry` | L1165 | 响应式压缩成功后重试 |
| 3 | `max_output_tokens_escalate` | L1220 | 首次 max_tokens 截断 → 提升到 64K 重试 |
| 4 | `max_output_tokens_recovery` | L1251 | 追加"请继续"消息重试（最多 3 次） |
| 5 | `stop_hook_blocking` | L1305 | Stop Hook 返回阻塞错误后重试 |
| 6 | `token_budget_continuation` | L1340 | Token Budget 未耗尽，追加 nudge 消息继续 |
| 7 | `next_turn` | L1727 | 正常工具执行完毕，进入下一轮 |

**4.3 `transition` 字段的测试价值（L214-216）**

```typescript
// Why the previous iteration continued. Undefined on first iteration.
// Lets tests assert recovery paths fired without inspecting message contents.
transition: Continue | undefined
```

这不是装饰——它让测试可以精确断言"这次循环是因为 reactive compact 而 continue 的"，而不需要检查消息内容。

---

## 5. 上下文存活：四层压缩防线

### Claim
Claude Code 不是只有一个 auto-compact。它实现了**四层独立但协作的压缩机制**，按顺序执行，每层解决不同粒度的问题。

### Source
`query.ts:396-447`（四层执行顺序）

```typescript
// 顺序：Snip → Microcompact → Context Collapse → Autocompact

// Layer 1: History Snip (L401-410)
if (feature('HISTORY_SNIP')) {
  const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages
  snipTokensFreed = snipResult.tokensFreed
}

// Layer 2: Microcompact (L413-426)
const microcompactResult = await deps.microcompact(...)
messagesForQuery = microcompactResult.messages

// Layer 3: Context Collapse (L440-447)
if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
  const collapseResult = await contextCollapse.applyCollapsesIfNeeded(...)
  messagesForQuery = collapseResult.messages
}

// Layer 4: Autocompact (L453-543)
const { compactionResult } = await deps.autocompact(...)
```

### Evidence

**5.1 History Snip（第一层）**

- **目的**：裁剪历史中已经"过时"的消息（如早期的文件读取结果）
- **特性**：纯本地计算，不调用 API
- **`snipTokensFreed` 传播**：freed 的 token 数会传递给 autocompact 的阈值计算，避免重复计算

**5.2 Microcompact（第二层）**

- **锚定源码**：`services/compact/microCompact.ts`（20KB）
- **目的**：对单个 tool_result 做细粒度压缩（如大文件读取结果裁剪到预算内）
- **关键设计**：`applyToolResultBudget`（L379-394）在 microcompact 之前运行，每个工具有 `maxResultSizeChars` 限制
- **缓存编辑模式**（`CACHED_MICROCOMPACT`）：利用 Anthropic API 的 `cache_deleted_input_tokens` 字段，在 API 响应后用真实的缓存删除数据替代客户端估算

**5.3 Context Collapse（第三层）**

- **目的**：将历史中的多组工具调用"折叠"为摘要，保持粒度而非全量摘要
- **关键特性**：这是一个**读时投影（read-time projection）**——摘要消息存在 collapse store 中，不在 REPL 数组中（L434-438）：

```typescript
// Nothing is yielded — the collapsed view is a read-time projection
// over the REPL's full history. Summary messages live in the collapse
// store, not the REPL array.
```

- **与 Autocompact 的关系**：Collapse 在 Autocompact 之前运行。如果 Collapse 能把 token 降到阈值以下，Autocompact 就不会触发，保留了更细粒度的上下文。

**5.4 Autocompact（第四层，终极手段）**

- **锚定源码**：`services/compact/autoCompact.ts`
- **阈值公式**（L72-91）：`threshold = effectiveContextWindow - 13,000`
- **`effectiveContextWindow`**（L33-49）：`contextWindow - min(maxOutputTokens, 20,000)`
- **熔断机制**（L70）：连续失败 3 次后停止重试（`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`），防止 API 调用浪费
- **Reactive Compact**（被动压缩）：当 API 返回 `prompt_too_long` 错误时触发，作为 Autocompact 的后备

### Extension
Python 版只实现了第四层（Autocompact），缺少前三层。这意味着在长会话场景中，Python 版会更早触发全量压缩，丢失更多上下文细节。

---

## 6. StreamingToolExecutor：流式工具执行引擎

### Claim
StreamingToolExecutor 不是简单的"边输出边执行"，而是一个具备**并发控制、错误传播、sibling abort、fallback discard** 的完整并发执行器。

### Source
`services/tools/StreamingToolExecutor.ts:40-62`

```typescript
export class StreamingToolExecutor {
  private tools: TrackedTool[] = []           // 工具队列
  private hasErrored = false                  // 是否有工具出错
  private erroredToolDescription = ''         // 出错工具的描述
  private siblingAbortController: AbortController  // sibling 级别的 abort
  private discarded = false                   // 是否被 discard（fallback 触发）
}
```

### Evidence

**6.1 并发控制模型（L129-151）**

```typescript
private canExecuteTool(isConcurrencySafe: boolean): boolean {
  const executingTools = this.tools.filter(t => t.status === 'executing')
  return (
    executingTools.length === 0 ||
    (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
  )
}
```

规则：
- 如果没有工具在执行 → 可以执行任何工具
- 如果有工具在执行且全是 concurrency-safe → 新的 concurrency-safe 工具可以并行
- 如果有非 concurrency-safe 工具在执行 → 必须等待

**典型场景**：多个 `GrepTool`（read-only, concurrency-safe）可以并行执行；但一个 `FileEditTool`（非 concurrency-safe）必须独占执行。

**6.2 Sibling Error Abort（L46-48）**

```typescript
// Child of toolUseContext.abortController. Fires when a Bash tool errors
// so sibling subprocesses die immediately instead of running to completion.
// Aborting this does NOT abort the parent — query.ts won't end the turn.
private siblingAbortController: AbortController
```

这是一个**子级 abort controller**：当一个 Bash 工具出错时，它会取消其他并行运行的兄弟工具，但不会取消整个 query turn。这避免了一个失败的 `grep` 命令导致整个对话轮次中断。

**6.3 Fallback Discard（L69-71 + query.ts:731-740）**

当模型的流式输出触发了 fallback（如主模型不可用切换到备用模型）时：

```typescript
// query.ts:733-739
if (streamingToolExecutor) {
  streamingToolExecutor.discard()  // 丢弃已执行的结果
  streamingToolExecutor = new StreamingToolExecutor(...)  // 创建新的执行器
}
```

已经执行的工具结果被丢弃，因为它们的 `tool_use_id` 属于旧的 assistant message，与 fallback 后的新响应不匹配。

**6.4 流式过程中的中间结果收集（query.ts:847-862）**

```typescript
// 在模型流式输出期间就收集已完成的工具结果
if (streamingToolExecutor) {
  for (const result of streamingToolExecutor.getCompletedResults()) {
    if (result.message) {
      yield result.message  // 立即向 UI 层传递结果
      toolResults.push(...)
    }
  }
}
```

这确保了在模型还在输出后续 token 时，已经完成的工具结果就能被 UI 显示，降低用户感知的延迟。

---

## 7. 工具系统：Tool 接口与 buildTool 工厂

### Claim
Tool 接口有 **19+ 个方法**，不只是"call + description"，而是覆盖了执行、权限、验证、并发控制、UI 渲染、搜索索引、分组显示等完整生命周期。

### Source
`Tool.ts:362-695`（Tool 类型定义）

### Evidence

**7.1 核心方法分类**

| 类别 | 方法 | 说明 |
|:---|:---|:---|
| **执行** | `call()` | 工具执行入口 |
| **描述** | `description()`, `prompt()` | 面向模型的描述和 system prompt |
| **输入** | `inputSchema`, `validateInput()` | Zod schema 验证 |
| **权限** | `checkPermissions()`, `preparePermissionMatcher()` | 工具级权限检查 |
| **并发** | `isConcurrencySafe()`, `isReadOnly()`, `isDestructive()` | 并发安全标记 |
| **中断** | `interruptBehavior()` | 用户中断时的行为（cancel / block） |
| **UI 渲染** | `renderToolUseMessage()`, `renderToolResultMessage()`, `renderToolUseProgressMessage()`, `renderToolUseRejectedMessage()`, `renderToolUseErrorMessage()`, `renderGroupedToolUse()` | Ink/React 渲染 |
| **搜索** | `searchHint`, `isSearchOrReadCommand()`, `extractSearchText()` | ToolSearch 和 transcript 搜索 |
| **分类器** | `toAutoClassifierInput()` | auto-mode 安全分类器输入 |
| **延迟加载** | `shouldDefer`, `alwaysLoad` | 工具延迟加载策略 |
| **结果映射** | `mapToolResultToToolResultBlockParam()` | 输出到 API 格式转换 |
| **结果预算** | `maxResultSizeChars` | 结果大小上限（超出后持久化到磁盘） |

**7.2 buildTool 工厂函数（L757-792）**

```typescript
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input?: unknown) => false,  // 默认不安全
  isReadOnly: (_input?: unknown) => false,          // 默认假设会写
  isDestructive: (_input?: unknown) => false,
  checkPermissions: (input) => Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: (_input?: unknown) => '',   // 默认跳过分类器
  userFacingName: (_input?: unknown) => '',
}

export function buildTool<D extends AnyToolDef>(def: D): BuiltTool<D> {
  return { ...TOOL_DEFAULTS, userFacingName: () => def.name, ...def } as BuiltTool<D>
}
```

关键设计：**fail-closed defaults**——`isConcurrencySafe` 默认 `false`，`isReadOnly` 默认 `false`。这意味着一个新工具如果忘记设置这些属性，会被当作"写操作且不安全"处理，不会被并行执行。

**7.3 backfillObservableInput（L474-481）**

```typescript
backfillObservableInput?(input: Record<string, unknown>): void
```

这个方法在工具的 `input` 被发送到观察者（SDK stream、transcript、hooks）之前调用，用于添加派生字段（如将 `file_path` 展开为绝对路径）。关键约束：**原始 API-bound input 永远不被修改**，以保持 prompt caching 的字节一致性。

---

## 8. 技能系统：从 SKILL.md 到运行时注入

### Claim
技能系统不是简单的"读取 Markdown 注入 prompt"。它支持 YAML Frontmatter 元数据解析、三种执行上下文、Shell 命令动态注入、参数替换、基于路径的过滤、以及技能发现的异步预取。

### Source
`skills/loadSkillsDir.ts`（1087 行）+ `skills/bundledSkills.ts`（221 行）

### Evidence

**8.1 SKILL.md Frontmatter 解析（loadSkillsDir.ts:185-265）**

技能的元数据通过 YAML Frontmatter 定义，解析后的字段包括：

```typescript
{
  displayName: string | undefined,
  description: string,
  allowedTools: string[],        // 允许的工具列表（如 'Bash(git:*)'）
  argumentHint: string,          // 参数提示
  argumentNames: string[],       // 参数名列表
  whenToUse: string,             // 何时触发（模型自动调用的判断依据）
  model: string | undefined,     // 指定模型（如 'opus'）
  disableModelInvocation: boolean, // 是否禁止模型自动调用
  executionContext: 'fork' | undefined,  // 执行上下文
  agent: string | undefined,     // 指定 agent 类型
  effort: EffortValue | undefined, // 努力程度
  shell: FrontmatterShell | undefined, // Shell 配置
}
```

**8.2 三种执行上下文**

| 模式 | frontmatter | 行为 |
|:---|:---|:---|
| **inline** | 默认 / `context: inline` | 将技能内容直接注入当前对话 transcript |
| **fork** | `context: fork` | 启动独立的 `runAgent` 子会话，完成后将结果回传 |
| **agent** | `agent: <name>` | 使用指定的 agent 定义（可以是自定义 agent） |

**8.3 Shell 命令动态注入（loadSkillsDir.ts:374-396）**

```typescript
// 安全约束：MCP 技能（远程、不可信）永远不执行 Shell 命令
if (loadedFrom !== 'mcp') {
  finalContent = await executeShellCommandsInPrompt(
    finalContent,
    { ...toolUseContext, ... },
    `/${skillName}`,
    shell,
  )
}
```

支持在 SKILL.md 中使用 `!{command}` 语法，在加载时实时执行本地命令并将结果注入 prompt。例如：

```markdown
当前 Git 分支：!{git branch --show-current}
```

**8.4 变量替换（loadSkillsDir.ts:349-369）**

```typescript
// 参数替换
finalContent = substituteArguments(finalContent, args, true, argumentNames)

// ${CLAUDE_SKILL_DIR} → 技能目录路径
finalContent = finalContent.replace(/\$\{CLAUDE_SKILL_DIR\}/g, skillDir)

// ${CLAUDE_SESSION_ID} → 当前会话 ID
finalContent = finalContent.replace(/\$\{CLAUDE_SESSION_ID\}/g, getSessionId())
```

**8.5 技能发现异步预取（query.ts:323-335 + 1617-1628）**

```typescript
// 在每轮循环开始时启动预取（L331-335）
const pendingSkillPrefetch = skillPrefetch?.startSkillDiscoveryPrefetch(
  null, messages, toolUseContext,
)

// 在工具执行完成后消费预取结果（L1620-1628）
const skillAttachments = await skillPrefetch.collectSkillDiscoveryPrefetch(...)
```

预取在模型流式输出和工具执行期间并行运行，不阻塞主循环。据注释，预取命中率 >98%（AKI@250ms / Haiku@573ms vs turn 时长 2-30s）。

**8.6 内置技能注册（bundledSkills.ts:53-100）**

内置技能通过 `registerBundledSkill()` 注册，支持附带参考文件（`files` 字段），文件会在首次调用时被懒加载提取到磁盘：

```typescript
if (files && Object.keys(files).length > 0) {
  skillRoot = getBundledSkillExtractDir(definition.name)
  let extractionPromise: Promise<string | null> | undefined
  getPromptForCommand = async (args, ctx) => {
    extractionPromise ??= extractBundledSkillFiles(definition.name, files) // 只提取一次
    // ...
  }
}
```

安全措施：提取文件时使用 `O_NOFOLLOW | O_EXCL` 标志防止符号链接攻击（L176-184）。

---

## 9. 多 Agent 协作：AgentTool 与 Coordinator

### Claim
Claude Code 支持两种多 Agent 模式：**AgentTool**（子代理模式）和 **Coordinator Mode**（协调者模式），两者在架构上是独立的。

### Source
- `tools/AgentTool/`（14 个文件，AgentTool.tsx 235KB）
- `coordinator/coordinatorMode.ts`（19KB）

### Evidence

**9.1 AgentTool 架构**

| 文件 | 大小 | 职责 |
|:---|:---|:---|
| `AgentTool.tsx` | 235KB | 主工具实现（子代理创建、生命周期管理） |
| `runAgent.ts` | 37KB | 子代理执行逻辑 |
| `forkSubagent.ts` | 9KB | Fork 模式子代理（共享 prompt cache） |
| `resumeAgent.ts` | 10KB | 子代理恢复（从 sidechain 恢复状态） |
| `agentMemory.ts` | 6KB | 子代理记忆管理 |
| `loadAgentsDir.ts` | 27KB | 从 `.claude/agents/` 加载自定义 agent 定义 |
| `prompt.ts` | 17KB | 子代理 system prompt 构建 |

**9.2 Coordinator Mode（query.ts:75-77）**

```typescript
const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js')
  : null
```

Coordinator Mode 通过 Feature Flag 控制，它向主 agent 的 `userContext` 中注入协调者专用的上下文（L112-117）。

**9.3 消息队列跨 agent 分发（query.ts:1559-1578）**

```typescript
const queuedCommandsSnapshot = getCommandsByMaxPriority(
  sleepRan ? 'later' : 'next',
).filter(cmd => {
  if (isSlashCommand(cmd)) return false
  if (isMainThread) return cmd.agentId === undefined       // 主线程只消费无 agentId 的命令
  return cmd.mode === 'task-notification' && cmd.agentId === currentAgentId // 子代理只消费自己的通知
})
```

消息队列是进程全局单例，主线程和子代理各自消费属于自己的消息。

---

## 10. 错误恢复：五条 continue 路径详解

### Source
`query.ts:1062-1357`

### Evidence

**10.1 Prompt Too Long 恢复链（L1062-1183）**

```
413 withheld → Context Collapse drain (L1089-1117)
                ↓ (失败)
              → Reactive Compact (L1119-1166)
                ↓ (失败)
              → yield error + return (L1173-1175)
```

**10.2 Max Output Tokens 恢复链（L1188-1256）**

```
max_output_tokens withheld → 提升到 64K 重试 (L1199-1221, 只一次)
                              ↓ (还是超限)
                            → 追加"请继续"消息，最多 3 次 (L1223-1252)
                              ↓ (3 次都超限)
                            → yield withheld error (L1255)
```

恢复消息的文本（L1225-1227）：
```typescript
content: `Output token limit hit. Resume directly — no apology, no recap of what you were doing. ` +
         `Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces.`
```

**10.3 Stop Hook 阻塞重试（L1267-1306）**

Stop Hook 可以在模型完成输出后检查结果，如果不满意可以返回 blocking error，强制模型重新生成。

**10.4 Token Budget 续航（L1308-1355）**

```typescript
const decision = checkTokenBudget(
  budgetTracker!, toolUseContext.agentId,
  getCurrentTurnTokenBudget(), getTurnOutputTokens(),
)
if (decision.action === 'continue') {
  // 追加 nudge 消息继续生成
}
```

当 turn 的输出 token 接近预算但尚未耗尽时，系统会追加一个轻量级消息让模型继续，而不是终止。

---

## 11. Python 复刻版逐模块对照

| 机制 | TS 原版实现 | Python 复刻版 | 差距评估 |
|:---|:---|:---|:---|
| **状态机** | 10 变量 State + 7 continue + AsyncGenerator | 8 变量 + 3 continue + AsyncIterator | **基本对齐**，少了 stop_hook 和 token_budget 路径 |
| **上下文压缩** | 四层：Snip → Micro → Collapse → Auto | 一层：Auto-compact | **重大差距**，需要补前三层 |
| **流式执行** | StreamingToolExecutor（并发控制 + sibling abort + discard） | StreamingToolExecutor（基础并发） | **中等差距**，缺少 sibling abort 和 discard |
| **工具接口** | 19+ 方法 + buildTool 工厂 | 基础 Tool class（call + description + schema） | **重大差距**，缺少 UI 渲染、搜索索引、并发标记 |
| **技能系统** | Frontmatter 解析 + fork/agent 模式 + Shell 注入 + 异步预取 | 基础 Markdown 加载 + 变量替换 | **重大差距**，缺少 fork 和 Shell 注入 |
| **多 Agent** | AgentTool(235KB) + Coordinator + 消息队列分发 | AgentTool（基础版） + BackgroundAgentManager | **中等差距**，Coordinator 模式缺失 |
| **权限系统** | ToolPermissionContext(8 字段) + auto-mode 分类器 + denial tracking | PermissionContext（基础版） | **中等差距** |
| **transcript 持久化** | 先写再调 API + bare 模式异步写 | 基础 save_session | **小差距** |
| **Feature Flags** | `bun:bundle` feature() 编译时消除 | 无 | **架构差异**，Python 可用环境变量替代 |
| **UI 渲染** | Ink/React（Terminal UI 组件化） | Rich（过程式渲染） | **技术栈差异**，不需要完全对齐 |

---

## 12. 结论与下一步

### 核心结论

1. **Claude Code 的核心竞争力不在模型，在 Harness**。query.ts 的 1730 行代码构建了一个完整的错误恢复、上下文存活、并发控制系统，这是让"模型能力"转化为"稳定可用的 Agent"的关键。

2. **四层压缩是最值得复刻的机制**。Python 版目前只有 Autocompact，在长会话中会过早丢失上下文细节。补全 Snip 和 Microcompact 是优先级最高的工作。

3. **StreamingToolExecutor 的 sibling abort 机制体现了生产级思维**——一个失败的工具不应该拖累整个 turn，但也不应该让有问题的兄弟工具继续消耗资源。

4. **技能系统的 fork 模式是多 Agent 协作的关键入口**。它让用户可以通过一个简单的 YAML frontmatter（`context: fork`）就启动一个独立的子代理会话。

### 优先补全路径

1. `cc/compact/` → 补全 Snip 和 Microcompact 层
2. `cc/tools/streaming_executor.py` → 补全 sibling abort 和 discard 机制
3. `cc/skills/loader.py` → 补全 fork 模式和 Shell 注入
4. `cc/core/query_loop.py` → 补全 stop_hook 和 token_budget continue 路径

---

> 本报告所有代码引用均来自 ` code analysis\src` 目录下的 TypeScript 源码。行号基于截至 2026-05-20 的文件版本。
