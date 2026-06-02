# Claude Code 内核机制深度分析

> 基于 TypeScript 源码反编译还原，覆盖 query 主循环、工具系统、流式执行、API 通信、错误恢复与权限六大核心子系统。

---

## 1. query.ts -- Agent Loop 状态机

### 1.1 while(true) 无限循环架构

query.ts 是整个 Claude Code 的心脏。核心入口函数 `query()` 返回 `AsyncGenerator`，内部通过 `queryLoop()` 运行一个 `while (true)` 四阶段循环，每次迭代对应一次完整的"模型调用 -> 工具执行"周转（turn）。

```typescript
// query.ts:241-250
async function* queryLoop(
  params: QueryParams,
  consumedCommandUuids: string[],
): AsyncGenerator<
  | StreamEvent | RequestStartEvent | Message
  | TombstoneMessage | ToolUseSummaryMessage,
  Terminal
> {
  // ...
  while (true) {
```

循环状态被封装在 `State` 类型中（query.ts:204-217），包含 `messages`、`toolUseContext`、`turnCount`、`autoCompactTracking`、`maxOutputTokensRecoveryCount` 等可变字段。每次 `continue`（即流转到下一轮）通过重新赋值 `state = { ... }` 实现不可变更新：

```typescript
// query.ts:204-217
type State = {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTrackingState | undefined
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | undefined
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined
  stopHookActive: boolean | undefined
  turnCount: number
  transition: Continue | undefined
}
```

### 1.2 四阶段拆解

每轮迭代按时间线分为四个阶段：

**阶段一 -- 消息预处理（Pre-flight）**：在调用模型前，依次执行 snip 截断、microcompact 微压缩、context collapse 折叠、以及 autocompact 自动压缩，确保消息列表适合 API 上下文窗口。每一步都可能修改 `messagesForQuery`。

```typescript
// query.ts:396-447
let snipTokensFreed = 0
if (feature('HISTORY_SNIP')) {
  const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages
  snipTokensFreed = snipResult.tokensFreed
}

// microcompact
const microcompactResult = await deps.microcompact(messagesForQuery, ...)

// autocompact
const { compactionResult, consecutiveFailures } = await deps.autocompact(...)
```

**阶段二 -- 流式模型调用（Streaming）**：通过 `deps.callModel()` 向 Claude API 发起流式请求（for-await-of 消费 SSE 事件）。同时启动 `StreamingToolExecutor`，在模型输出尚未结束时就开始执行已到达的 tool_use 块。

```typescript
// query.ts:659-863
for await (const message of deps.callModel({
  messages: prependUserContext(messagesForQuery, userContext),
  systemPrompt: fullSystemPrompt,
  thinkingConfig: ...,
  tools: ...,
  signal: toolUseContext.abortController.signal,
  options: { ... }
})) {
  if (message.type === 'assistant') {
    assistantMessages.push(message)
    // 收集 tool_use blocks
    const msgToolUseBlocks = message.message.content.filter(
      content => content.type === 'tool_use',
    ) as ToolUseBlock[]
    if (msgToolUseBlocks.length > 0) {
      toolUseBlocks.push(...msgToolUseBlocks)
      needsFollowUp = true
    }
    // 流式执行器提前启动工具
    if (streamingToolExecutor && !aborted) {
      for (const toolBlock of msgToolUseBlocks) {
        streamingToolExecutor.addTool(toolBlock, message)
      }
    }
  }
  // 边流式接收边产出已完成工具结果
  if (streamingToolExecutor && !aborted) {
    for (const result of streamingToolExecutor.getCompletedResults()) {
      if (result.message) yield result.message
    }
  }
}
```

**阶段三 -- 工具执行（Tool Execution）**：流式结束后收集剩余工具结果。若 `streamingToolExecutor` 存在，调用 `getRemainingResults()` 消费未完成的异步工具；否则回归传统的 `runTools()` 批处理路径。

```typescript
// query.ts:1380-1408
const toolUpdates = streamingToolExecutor
  ? streamingToolExecutor.getRemainingResults()
  : runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)

for await (const update of toolUpdates) {
  if (update.message) {
    yield update.message
    toolResults.push(
      ...normalizeMessagesForAPI([update.message], toolUseContext.options.tools)
        .filter(_ => _.type === 'user'),
    )
  }
  if (update.newContext) {
    updatedToolUseContext = { ...update.newContext, queryTracking }
  }
}
```

**阶段四 -- 后处理与决策（Post-processing）**：判断是否需要继续。若 `needsFollowUp` 为 true（存在未执行的 tool_use），则构造下一轮 state 并 `continue`。否则进入 stop hooks 检查、token budget 检查，最终 `return { reason: 'completed' }`。

### 1.3 AsyncGenerator 模式

为什么使用 `yield` 而不是 `return`？`query()` 返回 `AsyncGenerator<...StreamEvent | Message, Terminal>`。外层消费者（`print.ts`、SDK、REPL）可以逐个消费 yield 出的消息，实现"边生产、边渲染"的流式体验。每个 `yield` 都对应一条需要实时展示的 UI 消息（思考块、文本增量、工具调用进度等）。Generator 的 `return` 值 `Terminal` 仅在循环正常结束时返回给调用方，携带退出原因（`reason: 'completed' | 'prompt_too_long' | 'max_turns'` 等）。

### 1.4 normalizeMessagesForAPI() 规范化逻辑

该函数（messages.ts:1989-2039）在工具结果进入下一轮 API 调用前执行，主要负责：

1. **附件重排序**：将 attachment 消息向上冒泡，直到遇到 tool_result 或 assistant 消息
2. **虚拟消息剥离**：标记为 `isVirtual` 的消息（如 REPL 内部工具调用）被过滤，绝不允许到达 API
3. **错误关联处理**：识别 PDF/Image/RequestTooLarge 等 API 合成错误消息，向前回溯找到对应的 isMeta 用户消息并剥离问题内容块，防止重复提交导致二次失败

```typescript
// messages.ts:1993-2001
const availableToolNames = new Set(tools.map(t => t.name))
const reorderedMessages = reorderAttachmentsForAPI(messages).filter(
  m => !((m.type === 'user' || m.type === 'assistant') && m.isVirtual),
)
```

---

## 2. 工具系统架构

### 2.1 Tool 接口设计

`Tool` 是泛型接口 `<Input extends AnyObject, Output, P extends ToolProgressData>`（Tool.ts:362-695），包含接近 40 个方法/属性。核心职责分层如下：

| 层 | 关键方法 | 用途 |
|---|---|---|
| 执行 | `call()` | 实际执行工具逻辑，返回 `ToolResult<Output>` |
| 描述 | `description()` / `prompt()` | 为模型生成工具描述文本 |
| 安全 | `isConcurrencySafe()` / `isReadOnly()` / `isDestructive()` | 并发控制与权限判断的判定输入 |
| 权限 | `checkPermissions()` / `validateInput()` | 工具级权限校验 |
| 渲染 | `renderToolUseMessage()` / `renderToolResultMessage()` | React 组件渲染 |
| 分类 | `isSearchOrReadCommand()` / `isOpenWorld()` / `toAutoClassifierInput()` | UI 折叠与安全分类 |
| 中断 | `interruptBehavior()` | 用户新消息到达时行为：`cancel` 或 `block` |
| 搜索 | `searchHint` / `shouldDefer` / `alwaysLoad` | ToolSearch 延迟加载机制 |
| 结果 | `maxResultSizeChars` / `mapToolResultToToolResultBlockParam()` | 结果截断与序列化 |

```typescript
// Tool.ts:362-414
export type Tool<Input extends AnyObject = AnyObject, Output = unknown, P extends ToolProgressData = ToolProgressData> = {
  aliases?: string[]
  searchHint?: string
  call(args: z.infer<Input>, context: ToolUseContext, canUseTool: CanUseToolFn,
       parentMessage: AssistantMessage, onProgress?: ToolCallProgress<P>): Promise<ToolResult<Output>>
  isConcurrencySafe(input: z.infer<Input>): boolean
  isEnabled(): boolean
  isReadOnly(input: z.infer<Input>): boolean
  isDestructive?(input: z.infer<Input>): boolean
  interruptBehavior?(): 'cancel' | 'block'
  checkPermissions(input: z.infer<Input>, context: ToolUseContext): Promise<PermissionResult>
  // ...
}
```

### 2.2 ToolRegistry 的注册与查找机制

`findToolByName(tools, name)` 通过 `toolMatchesName()` 同时匹配主名称和 `aliases`（Tool.ts:348-360）：

```typescript
export function toolMatchesName(tool: { name: string; aliases?: string[] }, name: string): boolean {
  return tool.name === name || (tool.aliases?.includes(name) ?? false)
}

export function findToolByName(tools: Tools, name: string): Tool | undefined {
  return tools.find(t => toolMatchesName(t, name))
}
```

工具列表中包含主工具（Bash、Read、Write、Edit、Grep、Glob、Task 等）、MCP 工具（前缀 `mcp__`）、Agent 工具（子智能体）、以及通过 ToolSearch 延迟加载的工具。

### 2.3 partitionToolCalls 的并发安全分批算法

`partitionToolCalls()`（toolOrchestration.ts:91-116）将当前批次的 tool_use 请求划分为串行/并行两组：

```typescript
function partitionToolCalls(toolUseMessages: ToolUseBlock[], toolUseContext: ToolUseContext): Batch[] {
  return toolUseMessages.reduce((acc: Batch[], toolUse) => {
    const tool = findToolByName(toolUseContext.options.tools, toolUse.name)
    const parsedInput = tool?.inputSchema.safeParse(toolUse.input)
    const isConcurrencySafe = parsedInput?.success
      ? (() => {
          try { return Boolean(tool?.isConcurrencySafe(parsedInput.data)) }
          catch { return false }
        })()
      : false
    if (isConcurrencySafe && acc[acc.length - 1]?.isConcurrencySafe) {
      acc[acc.length - 1]!.blocks.push(toolUse)
    } else {
      acc.push({ isConcurrencySafe, blocks: [toolUse] })
    }
    return acc
  }, [])
}
```

算法要点：
- 对每个 tool_use 解析输入并调用 `tool.isConcurrencySafe()` 判定
- **相邻的并发安全工具合并为一个批次**，使用 `reduce` 追加到 `acc[acc.length-1]`
- 非并发安全工具（如写操作）独占一个批次，打断连续合并
- `isConcurrencySafe` 抛出异常时保守处理为 `false`
- 默认最大并发数为环境变量 `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` 或 10

### 2.4 runToolsConcurrently vs runToolsSerially

**runToolsConcurrently**（toolOrchestration.ts:152-177）：使用 `all()` 工具函数批量启动所有并发安全工具的执行，通过 `getMaxToolUseConcurrency()` 限制同时运行数。每个工具的 `contextModifier` 被延迟到批次全部完成后顺序应用（避免竞态）。

**runToolsSerially**（toolOrchestration.ts:118-150）：逐工具执行，每次 `runToolUse` 的 `contextModifier` 立即生效到下一次迭代的 `currentContext`。这保证了文件写入（Write/Edit）后，后续读取能看见最新内容。

两者统一通过 `runTools()` 入口函数（toolOrchestration.ts:19-82）协同工作，对上层 query.ts 只暴露一个 `AsyncGenerator<MessageUpdate>`。

---

## 3. 流式工具执行器 (StreamingToolExecutor)

### 3.1 提前启动机制

`StreamingToolExecutor`（StreamingToolExecutor.ts:40-531）是 Claude Code 的关键性能优化。在传统的 batch 模式下，工具执行必须等待模型响应完全结束；而流式执行器在模型仍在输出 token 时就启动第一个 tool_use 块的执行。

```typescript
// query.ts:837-844 -- 在 for-await-of 流式循环体内
if (streamingToolExecutor && !toolUseContext.abortController.signal.aborted) {
  for (const toolBlock of msgToolUseBlocks) {
    streamingToolExecutor.addTool(toolBlock, message)  // 立即排队执行
  }
}
```

每个工具被追踪为 `TrackedTool` 对象，携带状态机生命周期：

```typescript
// StreamingToolExecutor.ts:21-32
type TrackedTool = {
  id: string; block: ToolUseBlock; assistantMessage: AssistantMessage
  status: ToolStatus          // 'queued' | 'executing' | 'completed' | 'yielded'
  isConcurrencySafe: boolean
  promise?: Promise<void>
  results?: Message[]; pendingProgress: Message[]
  contextModifiers?: Array<(context: ToolUseContext) => ToolUseContext>
}
```

### 3.2 并发控制模型

`processQueue()` 遍历 `queued` 状态的工具，调用 `canExecuteTool()` 检查是否满足并发条件：

```typescript
// StreamingToolExecutor.ts:129-135
private canExecuteTool(isConcurrencySafe: boolean): boolean {
  const executingTools = this.tools.filter(t => t.status === 'executing')
  return (
    executingTools.length === 0 ||
    (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
  )
}
```

规则：一个非并发安全的工具可以独占执行；多个并发安全的工具可以并行执行。非并发安全工具会阻塞队列。

### 3.3 中断级联与 sibling abort

当 Bash 工具执行出错时，`hasErrored` 置为 true，触发 `siblingAbortController.abort('sibling_error')`，其他正在执行的工具收到信号后生成合成错误消息。这是为了避免并行命令间的依赖链浪费（如 `mkdir` 失败后再执行后续命令没有意义）。

```typescript
// StreamingToolExecutor.ts:347-363
if (isErrorResult) {
  thisToolErrored = true
  if (tool.block.name === BASH_TOOL_NAME) {
    this.hasErrored = true
    this.erroredToolDescription = this.getToolDescription(tool)
    this.siblingAbortController.abort('sibling_error')
  }
}
```

### 3.4 与 batch 模式 runTools() 的区别

| 维度 | batch (runTools) | streaming (StreamingToolExecutor) |
|---|---|---|
| 启动时机 | 模型输出完全结束后 | 在模型输出过程中异步启动 |
| 并发模型 | 静态分批（partitionToolCalls）后并行 | 动态队列（addTool 即时入队） |
| 进度消息 | 不支持 | 通过 `pendingProgress` + `progressAvailableResolve` 即时产出 |
| fallback/重试 | 自然丢弃上一轮结果 | 需显式 `discard()` 并重建 executor |

---

## 4. API 流式处理 (claude.ts)

### 4.1 SSE 事件解析

`claude.ts` 封装了对 Claude API Beta Messages 端点的流式调用。流式响应通过 `@anthropic-ai/sdk` 的 `Stream` 类型消费，核心事件类型包括：

- `message_start` -- 包含消息 ID 和模型信息
- `content_block_start` -- 新内容块的开始，`content_block.type` 可以是 `text`、`tool_use`、`thinking` 等
- `content_block_delta` -- 内容块的增量数据
- `content_block_stop` -- 内容块完成
- `message_delta` -- 包含 `stop_reason` 和 `usage`
- `message_stop` -- 消息结束

### 4.2 流式 JSON 累积（partial_json）

对于 `tool_use` 类型的 content_block，输入参数通过多次 `content_block_delta` 事件以 JSON 片段（partial_json）形式渐进拼合。例如：

```
content_block_start: { type: "tool_use", id: "toolu_xxx", name: "Bash" }
content_block_delta: { type: "input_json_delta", partial_json: "{\"com" }
content_block_delta: { type: "input_json_delta", partial_json: "mand\":\"ls -la\"}" }
content_block_stop: ...
```

这在 query.ts 的 `backfillObservableInput` 阶段收敛为完整的 `input` 对象，供工具执行和 UI 渲染使用。

### 4.3 Beta Headers 与实验特性

`claude.ts` 管理大量 Beta Header（claude.ts:134-143），通过 `getMergedBetas()` 合并模型级、API 级、以及动态配置的 betas：

```typescript
// claude.ts:134-143
const AFK_MODE_BETA_HEADER = '...'
const CONTEXT_1M_BETA_HEADER = '...'
const FAST_MODE_BETA_HEADER = '...'
const PROMPT_CACHING_SCOPE_BETA_HEADER = '...'
const STRUCTURED_OUTPUTS_BETA_HEADER = '...'
const TASK_BUDGETS_BETA_HEADER = '...'
```

关键特性包括：prompt caching（1h TTL）、thinking/redacted_thinking、effort control、task_budgets 等。

### 4.4 Prompt Caching 策略

`getPromptCachingEnabled()` 支持按模型粒度禁用缓存；`should1hCacheTTL()` 通过 GrowthBook 的 allowlist 控制哪些 querySource 可以享受 1 小时 TTL（ant 员工和付费用户专属）：

```typescript
// claude.ts:419-434
let allowlist = getPromptCache1hAllowlist()
if (allowlist === null) {
  const config = getFeatureValue_CACHED_MAY_BE_STALE<{ allowlist?: string[] }>(
    'tengu_prompt_cache_1h_config', {})
  allowlist = config.allowlist ?? []
  setPromptCache1hAllowlist(allowlist)
}
return (
  querySource !== undefined &&
  allowlist.some(pattern =>
    pattern.endsWith('*')
      ? querySource.startsWith(pattern.slice(0, -1))
      : querySource === pattern,
  )
)
```

---

## 5. 错误恢复策略

### 5.1 prompt_too_long -- 多级压缩重试

当 API 返回 413 / prompt_too_long 时，query.ts 的 withold-then-recover 模式依次尝试（query.ts:1070-1183）：

1. **Context Collapse 疏放**（`collapse_drain_retry`）：调用 `contextCollapse.recoverFromOverflow()` 消耗已暂存的折叠队列。这是最廉价的恢复手段，保留细粒度上下文。
2. **Reactive Compact**（`reactive_compact_retry`）：执行完整的上下文摘要压缩，将历史对话转为结构化摘要。
3. **表面错误**：若上述手段都不可用或已尝试，则 yield 被暂扣的错误信息并退出。

暂扣机制（withholding）是此处的关键设计：流式循环内的 `prompt_too_long` 错误首先被 `isWithheldPromptTooLong()` 捕获暂不 yield，等需要时才释放，避免 SDK 消费者提前终止会话。

### 5.2 max_output_tokens -- 两阶段恢复

（query.ts:1188-1256）首先尝试 `escalate`（在内部把默认 8K max_tokens 提升到 64K `ESCALATED_MAX_TOKENS`）并重试同一次请求（无额外消息注入）。若仍然超限，则在下一轮注入恢复消息 `"Output token limit hit. Resume directly..."`，最多重试 `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`（3 次）。超出限制后表面错误并退出。

### 5.3 429/529 -- 指数退避重试

`withRetry.ts` 提供了统一的 HTTP 重试包装器。`FallbackTriggeredError` 是特殊异常：当原始模型过载时，自动切换到 `fallbackModel` 并重试（query.ts:893-953）：

```typescript
// query.ts:893-953
if (innerError instanceof FallbackTriggeredError && fallbackModel) {
  currentModel = fallbackModel
  attemptWithFallback = true
  // 清理 orphan 消息，重建 executor
  yield* yieldMissingToolResultBlocks(assistantMessages, 'Model fallback triggered')
  assistantMessages.length = 0
  // ...
  if (streamingToolExecutor) {
    streamingToolExecutor.discard()
    streamingToolExecutor = new StreamingToolExecutor(...)
  }
  // 剥离签名块（thinking signatures 是模型绑定的）
  if (process.env.USER_TYPE === 'ant') {
    messagesForQuery = stripSignatureBlocks(messagesForQuery)
  }
  continue
}
```

### 5.4 max_turns 保护

`turnCount` 在每轮迭代结束时递增。若设置了 `maxTurns` 参数并且在 `needsFollowUp` 循环中超出限制，query 返回 `{ reason: 'max_turns' }`。`TOKEN_BUDGET` 特性（query.ts:1308-1355）提供额外的基于 token 消耗量的渐进式退出：首先注入"可以继续"的 nudge message，然后在检测到边际收益递减时提前结束。

### 5.5 blocking_limit 硬限制

在 auto-compact 关闭时，`calculateTokenWarningState()` 检查 token 使用量是否超过硬上限（query.ts:628-648）。若超过且所有自动恢复都不可用，则直接返回 `{ reason: 'blocking_limit' }` 并建议用户手动执行 `/compact`。

---

## 6. 权限系统

### 6.1 PermissionMode 三种模式

（permissionSetup.ts, permissions.ts）系统内置三种权限模式：

- **default**：标准模式。危险操作（写入、执行、删除）需要用户交互确认。Bash 命令会经过安全分类器（classifier）评估。
- **acceptEdits**（或称 autoMode / "YOLO mode"）：自动批准文件编辑操作，但代码执行仍需确认。由 `TRANSCRIPT_CLASSIFIER` 特性开关控制。
- **bypassPermissions**（或称 plan mode）：完全跳过权限检查，所有操作自动执行。`isBypassPermissionsModeAvailable` 控制此模式的可用性。

### 6.2 checkPermissions 的调用时机

每个工具的 `checkPermissions()` 方法在执行前被调用（toolExecution.ts 中的 `runToolUse`）。权限决策链为：

```
validateInput() → hook (PreToolUse) → checkPermissions() → 用户交互/规则匹配 → hook (PostToolUse) → call()
```

权限规则依次按优先级检查：
1. `alwaysDenyRules` -- 来自设置的显式拒绝
2. `alwaysAllowRules` -- 来自设置的显式允许
3. Hook 决策 -- 来自 PreToolUse hook 的返回值
4. 安全分类器 -- Bash 命令的安全评估（classifier）
5. 用户交互 -- 弹出权限对话框

### 6.3 非交互模式下的 fail-fast 语义

当 `toolUseContext.options.isNonInteractiveSession` 为 true（SDK/headless 模式）且 `shouldAvoidPermissionPrompts` 为 true（如后台子智能体）时，权限授予失败不会弹出对话框，而是直接返回拒绝结果（permissions.ts）。`DENIAL_LIMITS` 跟踪连续拒绝次数，超过阈值后 `shouldFallbackToPrompting()` 返回 true，将子智能体降级为向用户请求指导。

### 6.4 危险权限检测

`isDangerousBashPermission()`（permissionSetup.ts:94-147）识别允许任意代码执行的权限规则，如 Bash 工具级允许（无 ruleContent）、脚本解释器前缀（`python:*`、`node:*`）、通配符（`*`）等。这些规则在 autoMode 下被标记为 `strippedDangerousRules`，触发手动确认。

---

## 7. 与 Python 复刻版的内核对应

在将 TypeScript 源码迁移到 Python 时，各模块的对应关系如下：

| TypeScript 源文件 | Python 目标模块 | 核心职责 |
|---|---|---|
| `src/query.ts` | `cc/core/query_loop.py` | Agent Loop 主循环，while(true) 四阶段状态机 |
| `src/services/tools/toolOrchestration.ts` | `cc/tools/orchestration.py` | 工具分批调度（partitionToolCalls）+ 串并行协调 |
| `src/services/tools/StreamingToolExecutor.ts` | `cc/tools/streaming_executor.py` | 流式工具提前启动执行器 |
| `src/services/tools/toolExecution.ts` | `cc/tools/execution.py` | 单工具执行流程（runToolUse + 权限/计费等横切关注点） |
| `src/Tool.ts` | `cc/tools/base.py` | Tool 抽象基类，定义接口规范 |
| `src/services/api/claude.ts` | `cc/api/claude.py` | Claude API 流式调用 + Beta Headers + Prompt Caching |
| `src/utils/permissions/permissionSetup.ts` | `cc/permissions/setup.py` | 权限模式初始化与规则加载 |
| `src/utils/permissions/permissions.ts` | `cc/permissions/gate.py` | 权限决策门控，规则匹配与用户交互 |
| `src/services/api/withRetry.ts` | `cc/api/retry.py` | 指数退避重试 + FallbackTriggeredError 处理 |
| `src/utils/messages.ts` (normalizeMessagesForAPI) | `cc/core/message_normalizer.py` | 消息规范化：附件排序、虚拟消息剥离、错误关联处理 |

### 移植关键注意事项

1. **AsyncGenerator 等价物**：TypeScript 的 `async function*` / `yield` 对应 Python 的 `async def` + `yield`（Python 3.6+ async generator），消费端用 `async for`。

2. **不可变状态更新**：query.ts 中大量的 `state = { ...state, field: newValue }` 模式在 Python 中可以用 `dataclasses.replace()` 或显式字典解包模拟。

3. **流式工具执行器**：`StreamingToolExecutor` 的核心是 `asyncio.create_task()` 启动异步工具执行 + `Promise.race()` 对应 `asyncio.wait(tasks, return_when=FIRST_COMPLETED)` 的进度唤醒模式。

4. **工具接口**：`Tool` 泛型接口中的 `z.infer<Input>` 对应 Python 的 `Pydantic v2` schema 校验，`inputSchema.safeParse()` 对应 `model_validate()`。

5. **Feature Gates**：`import { feature } from 'bun:bundle'` 的编译期树摇（tree-shaking）在 Python 中需要用运行时配置开关代替（如 `if config.get("HISTORY_SNIP"):`）。

6. **Prompt Caching**：`cache_control` 的 `{ type: 'ephemeral', ttl: '1h', scope: 'global' }` 结构在各语言 SDK 中一致，直接映射为字典字面量。

---

*本报告基于 Claude Code TypeScript 源码反编译文件分析完成，覆盖了 query.ts（70412 bytes）、Tool.ts（30308 bytes）、StreamingToolExecutor.ts、toolOrchestration.ts、toolExecution.ts、claude.ts、permissions.ts、permissionSetup.ts 共 8 个核心源文件。*
