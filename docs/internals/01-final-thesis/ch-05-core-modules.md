# 第 5 章 核心模块

> 本章对 Claude ​Code 的核心模块逐一深入解析。每个模块的论述结构为：定位 → 接口 → 内部机制 → 关键源码引用 → 设计权衡。

---

## 5.1 QueryEngine：会话生命周期容器

### 5.1.1 定位

`QueryEngine`（`src/QueryEngine.ts`）是 **会话级生命周期容器**。源码类级注释明确写为 "One QueryEngine per conversation"。每次 `submitMessage()` 是同一 conversation 内的新 turn。

它的重点不是"调用模型"，而是维护一个可恢复、可统计、可中断、可被 SDK 消费的会话容器。`query()` 才是真正的执行内核，QueryEngine 是它的会话级包装器。

### 5.1.2 跨 turn 状态

QueryEngine 维护以下跨 turn 状态：

| 字段 | 类型 | 用途 |
|------|------|------|
| `mutableMessages` | `Message[]` | 会话完整 transcript |
| `abortController` | `AbortController` | 用户中断 / 进程退出时使用 |
| `permissionDenials` | `PermissionDenial[]` | 累积所有被拒绝的权限请求 |
| `totalUsage` | `Usage` | 累计 token 使用（input/output/cache） |
| `readFileState` | `ReadFileState` | 文件读取缓存（避免同一文件被多次读取） |
| `discoveredSkillNames` | `Set<string>` | 已发现的 skill |
| `loadedNestedMemoryPaths` | `Set<string>` | 已加载的嵌套记忆路径（防止重复加载） |

这些状态不能放在 query_loop 内部，因为 query_loop 是单 turn 的。会话级状态必须由更高层的容器持有。

### 5.1.3 submitMessage() 调用链

一次 SDK / headless 请求进入 `QueryEngine.submitMessage()` 后，先经过会话层处理，再进入执行内核：

```text
QueryEngine.submitMessage(userInput)
  ├─ 1. 构造 ProcessUserInputContext
  │     包含 tools、MCP clients、agent definitions、app state、
  │     abort controller、file cache 等
  │
  ├─ 2. processUserInput()
  │     处理 prompt、slash command、attachments、
  │     UserPromptSubmit hooks
  │     输出 messagesFromUserInput + shouldQuery
  │
  ├─ 3. messagesFromUserInput 推入 this.mutableMessages
  │     拷贝一份 messages 交给 query()
  │
  ├─ 4. 进入 query 前写 transcript
  │     ✱ 关键设计：用户输入一旦被接受就必须先写 transcript
  │       否则进程在 API 返回前崩溃时，系统无法判断这次输入是否已经
  │       进入会话
  │
  ├─ 5. 根据用户输入更新 permission context
  │     如 alwaysAllowRules.command 被修改
  │
  ├─ 6. 调用 query({
  │       messages, systemPrompt, userContext, systemContext,
  │       canUseTool, toolUseContext
  │     })
  │
  ├─ 7. for await 消费 query() 产出的内部事件
  │     转换为 SDK-facing message / event
  │
  └─ 8. 产出最终 SDK result：
       { type, subtype, duration_ms, duration_api_ms, num_turns,
         total_cost_usd, usage, modelUsage, permission_denials,
         structured_output }
```

**关键源码引用**：

- `QueryEngine.ts:180-207` — 类级注释和核心字段定义
- `QueryEngine.ts:209-236` — `submitMessage()` 输入与配置解包
- `QueryEngine.ts:335-395` — 构造 `ProcessUserInputContext`
- `QueryEngine.ts:410-428` — 调用 `processUserInput()`
- `QueryEngine.ts:430-486` — 推入 `mutableMessages`、写 transcript、更新 permission context
- `QueryEngine.ts:675-686` — 调用 `query()`
- `QueryEngine.ts:760-969` — 消费 `query()` 产出的 message/event
- `QueryEngine.ts:1082-1155` — 产出最终 SDK result

### 5.1.4 SDK 与 REPL 的关系

SDK 路径不是单独实现了一套模型调用。它与 REPL **共用 `query.ts`**。差别主要在：

- 输入来源不同（程序化调用 vs 用户打字）
- 输出转换不同（SDKMessage vs UI 事件）
- 权限 UI 不同（编程接口回调 vs 终端弹窗）
- transcript persistence 策略不同
- SDK 需要把内部事件转成 `SDKMessage`

这是"统一执行内核"原则的直接体现——`QueryEngine` 是 `query()` 的包装器，不是替代实现。

### 5.1.5 关键设计权衡

**为什么不直接让 REPL 也用 QueryEngine**？

理论上 REPL 完全可以创建一个 QueryEngine 实例，让 SDK 与 REPL 通过同一对象通信。但 REPL 有以下特殊需求：

- UI 状态（messagesRef、AppState）需要立即响应消息变化，不能等 `for await`
- 权限弹窗需要 React 组件交互，与 SDK 的纯回调风格不同
- 流式渲染需要逐 delta 更新，而非等完整 message

因此 REPL 直接使用 `query()` 而非 `QueryEngine`，把 transcript 持久化、权限处理等放在 REPL.tsx 自己的状态管理中。但 query() 接口对二者一致，避免了双套实现。

## 5.2 query()：单 turn AsyncGenerator 状态机

### 5.2.1 定位

`query()`（`src/query.ts:219-238`）是 Claude ​Code Runtime 的**执行心脏**。它把一次用户输入推进为一个可持续运行的 agentic execution loop：调用模型、接收流式输出、识别 tool_use 块、执行工具、把 tool_result 包装成下一轮 UserMessage，再回到模型调用。

它的定位是 **context compiler + agentic execution loop**。模型看到的 messages 不是原始 transcript 的原样转发，而是经过 compact、snip、collapse、tool result budget、attachment bubbling、API normalization 等处理后的 API view。

### 5.2.2 QueryParams 与 State 的拆分

`queryLoop()` 开始时把入参拆成两类：

**QueryParams**（不可变参数）：

```typescript
interface QueryParams {
  messages: Message[]
  systemPrompt: SystemPrompt
  userContext: UserContext
  systemContext: SystemContext
  canUseTool: CanUseToolFn
  toolUseContext: ToolUseContext
  fallbackModel: ModelConfig
  querySource: QuerySource
  maxTurns: number
  taskBudget: TokenBudget
  deps: QueryDeps  // callModel, microcompact, autocompact, uuid
}
```

**State**（loop 内部跨 iteration 可变状态）：

```typescript
interface State {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTracking
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | null
  pendingToolUseSummary: Promise<...> | null
  stopHookActive: boolean
  turnCount: number
  transition: { reason: string }
}
```

**transition 字段的工程意义**：源码注释明确说它记录上一轮为什么继续，方便测试断言 recovery paths（`query.ts:213-216`）。真正的 continuation path 都遵循同一种模板：

```typescript
const next: State = {
  messages: nextMessages,
  toolUseContext: nextToolUseContext,
  autoCompactTracking: nextTracking,
  // ... 完整替换所有字段
  transition: { reason: '...' },
}

state = next
continue
```

这个模板是 `query_loop` 可读性的核心：不要把它理解成很多零散的 if/else，而要理解成一组明确命名的状态迁移边。最有效的源码读法是搜索 `transition: { reason:`，每个命中点就是状态机的一条边。

### 5.2.3 Phase 1：准备

每次 `while(true)` iteration 顶部，query_loop 不会直接把 transcript 原样发给模型，而是先构造 `messagesForQuery`。这一阶段体现了 Claude ​Code 的核心定位：context compiler。

准备阶段步骤（`query.ts:365-535`）：

1. **`getMessagesAfterCompactBoundary(messages)`**（`query.ts:365`）：只取 compact boundary 之后的消息，避免已压缩旧历史重复进入模型
2. **`applyToolResultBudget(...)`**（`query.ts:369-394`）：限制工具结果占用，防止大输出挤爆上下文
3. **history snip**（`query.ts:396-410`）：feature-gated 的历史裁剪
4. **microcompact**（`query.ts:412-426`）：更细粒度的轻量压缩
5. **context collapse projection**（`query.ts:428-447`）：读时投影 collapsed view
6. **autocompact**（`query.ts:453-468`）：自动压缩判定；成功后 yield compact messages，并把本轮 `messagesForQuery` 替换成压缩后的消息
7. **API boundary normalization**：`normalizeMessagesForAPI(messages, filteredTools)` 过滤 progress、display-only virtual messages、非 local_command 的 system messages、synthetic API errors，合并连续 user messages，处理 tool reference / unavailable tools

设计原因是协议隔离：内部 transcript 可以包含 UI 进度、附件、compact boundary、虚拟消息、hook 输出；API messages 只能包含模型协议允许的 user / assistant 内容。`normalizeMessagesForAPI()` 不是可有可无的 helper，而是 runtime 必需模块。

### 5.2.4 Phase 2：调用

query_loop 通过 `deps.callModel()` 进行流式模型调用。它用 `for await` 消费模型事件，并把事件继续 yield 给上层 UI/SDK：

```text
for await (const message of deps.callModel(...)):
  yield message unless withheld
  if assistant message contains tool_use:
    collect toolUseBlock
    needsFollowUp = true
    maybe streamingToolExecutor.addTool(...)
```

**关键细节：不信任 `stop_reason === 'tool_use'`**（`query.ts:552-557`）。模型 stop reason 并不总是可靠，所以是否继续下一轮不以 stop_reason 为准，而以 streaming 中是否真实捕获到 tool_use block 为准。这是"协议正确性优先于输出漂亮"的典型体现。

在流式输出期间，query_loop 累积两类临时状态：

- `assistantMessages`：本轮完整的 assistant 输出
- `toolUseBlocks`：从 assistant content block 中提取出来的工具调用

### 5.2.5 Phase 3：恢复

模型流结束后，如果本轮没有需要执行的工具，状态机仍不会立刻终止，而是先检查恢复路径：

| 策略 | 触发条件 | 处理方式 | transition.reason |
|------|---------|---------|------------------|
| context collapse overflow recovery | API 返回 `prompt_too_long`，且 context collapse 有 staged collapses 可 drain | drain collapsed context 后重试，不增加 turnCount | `collapse_drain_retry` |
| reactive compact retry | `prompt_too_long` 或 media-size error | 执行 reactive compact，每轮限制一次，成功后用 post compact messages 重试 | `reactive_compact_retry` |
| max output token escalate | 输出因 `max_output_tokens` 截断，且还能提升上限 | 提升 `maxOutputTokensOverride` 后重试 | `max_output_tokens_escalate` |
| max output token recovery | 输出仍被截断且未超过恢复次数 | 追加 meta user message，要求模型继续 | `max_output_tokens_recovery` |
| 瞬时 API 故障 | 网络/API 临时错误 | 线性退避重试；不消耗 agent turn budget，且避免触发 stop hooks 形成 death spiral | 无固定 transition |

需要特别注意三层恢复的精确语义：

1. **context collapse drain**（`query.ts:1085-1116`）：prompt-too-long 被 withheld 时，先尝试 drain staged collapses。成功则 `messages = drained.messages`，不增加 turnCount。这是第一层恢复
2. **reactive compact**（`query.ts:1119-1165`）：如果 drain 失败，尝试 reactive compact。每轮限制一次（通过 `hasAttemptedReactiveCompact` 标记），成功后 `messages = postCompactMessages`。这是第二层恢复
3. **max output tokens** 的两步恢复（`query.ts:1185-1251`）：第一步先提升 `maxOutputTokensOverride` 到更高值；如果仍被截断，追加 meta user message "Please continue from where you left off." 要求模型继续

API 错误（429/529 等）的恢复策略也很关键：被标记为 recoverable 的错误会进入重试+sleep 退避路径，且**不消耗真实 turn budget**（`query.ts:1258-1264`）。否则底层只是连接错误，上层最后看到的是 "Max turns reached"，形成非常糟糕的调试体验。

### 5.2.6 Phase 4：累积

在模型流式输出期间，query_loop 不立即构造 AssistantMessage，而是先把本轮状态归并到临时变量里：

- `accumulated_text`
- `usage`
- `stop_reason`
- `toolUseBlocks`
- `error_event`

因为一轮模型响应在流式模式下是分片到达的：文本是 delta、thinking 是 delta、tool_use 是先有 start 再有完整输入、stop_reason 和 usage 往往最后才知道。所以运行时必须经历两个阶段：事件归并阶段，然后才是 transcript materialization 阶段。

如果启用了 `config.gates.streamingToolExecution`，状态机会创建 `StreamingToolExecutor`。它在工具输入块完整出现时就启动工具，而不是等整个 assistant 响应结束（详见 5.3 节）。

### 5.2.7 Phase 5：物化

物化阶段把临时收集的 assistant stream 变成稳定的 `AssistantMessage`。这一步是 transcript 成为唯一 Source of Truth 的关键。到这里为止，这一轮 assistant 的"意图"才算被正式写进系统状态。

AssistantMessage 包含两类信息：文本回答 和 `tool_use_blocks`。它不是纯文本，而是"文本 + 行动意图"的组合。

### 5.2.8 Phase 6：执行

如果本轮捕获到 tool_use，状态机进入工具执行阶段。工具执行不是直接 `tool.call()`，而是经过完整的协议栈：

```text
tool_use block
  ├─ findToolByName() / alias fallback
  ├─ schema safeParse
  ├─ tool.validateInput()
  ├─ PreToolUse hooks
  ├─ permission decision
  ├─ tool.call()
  ├─ PostToolUse hooks
  ├─ mapToolResultToToolResultBlockParam()
  └─ createUserMessage({ content: [tool_result] })
```

工具结果收集完成后，query_loop 构造下一轮状态：

```typescript
messages: [
  ...messagesForQuery,
  ...assistantMessages,
  ...toolResults,
],
transition: { reason: 'next_turn' },
turnCount: state.turnCount + 1,
```

这就是 agent loop 的核心闭环：模型不是"调用工具"本身，而是生成 tool_use；Runtime 执行工具；工具结果以 tool_result 的形式变成新的 user message；模型在下一轮读取这个结果并继续推理。

### 5.2.9 为什么必须是 AsyncGenerator

`query()` 选择 AsyncGenerator 而不是普通 Promise、回调或 EventEmitter，是因为它同时要满足四类需求：

1. **流式 UI**：TUI 需要逐 token、逐 message、逐 tool progress 渲染
2. **协议事件**：SDK/headless 需要消费结构化 message/event
3. **可中断**：AbortController、用户新输入、权限弹窗都可能打断当前流程
4. **可组合**：subagent、background agent、QueryEngine 可以用同一种 `for await` 消费方式复用执行内核

普通 Promise 只能返回最终结果；EventEmitter 容易失去 backpressure 和 return terminal value；AsyncGenerator 刚好能表达"持续 yield 事件，最终 return terminal"。

代价是内部控制流很长，`query.ts` 成为巨型状态机；yield 的类型很宽，调用方必须 switch message type；测试需要关心事件序列。源码用 `query/deps.ts` 收窄外部依赖，是对这种复杂性的补偿。

## 5.3 StreamingToolExecutor：边收流边执行

### 5.3.1 定位

`StreamingToolExecutor`（`src/services/tools/StreamingToolExecutor.ts`）是 Claude ​Code 的**延迟优化关键模块**。它的核心创新是：在 ToolUseBlock 解析完成时立即启动工具，不等完整 assistant 响应结束。

**性能数据**（源码注释提及）：可减少端到端延迟 30-50%。

### 5.3.2 工作流程

当 `config.gates.streamingToolExecution` 开启时，`query.ts` 在 Phase 2 创建 `StreamingToolExecutor`（`query.ts:560-568`）。每当流式接收到一个完整的 tool_use block，就调用 `addTool()`：

```text
StreamingToolExecutor.addTool():
  1. 找到工具定义（findToolByName）
  2. 对 input 做 schema safeParse
  3. 判断 isConcurrencySafe(parsedInput)
  4. 加入执行队列
  5. 立即 processQueue() 尝试启动
```

并发规则是保守的（`StreamingToolExecutor.ts:126-150`）：

- 没有正在执行的工具时可以启动
- 当前工具并发安全且所有正在执行的工具也并发安全时可以并发
- 否则等待

`query.ts` 在 streaming 期间会反复调用 `getCompletedResults()`，把已经完成的工具结果提前 yield 给上层（`query.ts:847-862`）。模型流结束后再调用 `getRemainingResults()` 等待剩余结果（`StreamingToolExecutor.ts:453-490`）。

### 5.3.3 协议正确性的维护

物理执行可以提前，但**协议语义必须等价**：最终仍然要把 assistant tool_use 和 user tool_result 以正确顺序放回 messages。

streaming tool execution 引入了几个复杂问题：

1. **streaming fallback discard**（`query.ts:730-740`、`query.ts:909-919`）：当流到一半发生 prompt_too_long 触发 reactive compact 时，已启动工具的结果必须丢弃，避免旧 `tool_use_id` 的结果泄露到新尝试

2. **abort synthetic result**（`query.ts:1011-1029`）：工具 abort 时必须生成 synthetic `tool_result`，避免 tool_use 没有对应结果——否则下一次重试会因孤儿 tool_use 失败

3. **sibling tool abort**（`StreamingToolExecutor.ts:45-48`）：sibling tool 出错可能需要 abort 其他 sibling subprocess

4. **结果顺序保证**：`getCompletedResults()` 必须按 tool_use 在 stream 中的顺序 yield，避免下游 UI 渲染乱序

### 5.3.4 设计权衡

**Why**：开发者 CLI 对"工具开始跑了没有"的等待感非常敏感。如果模型说 1000 字然后在最后一个 block 里调一个 30 秒命令，用户在看完 1000 字后还要再等 30 秒。

**Trade-off**：

- 引入 streaming fallback discard 复杂度
- 引入 abort synthetic result 复杂度
- 并发安全判定必须保守
- streaming UI 的工具状态显示更复杂（要追踪每个 tool 的 streaming / executing / completed 状态）

但这种"性能换复杂性"的设计 Claude ​Code 选择接受，因为它的目标用户对延迟敏感度高。

## 5.4 工具系统

### 5.4.1 Tool 抽象协议（9 个职责组）

Tool 类型定义在 `src/services/tools/Tool.ts:362-695`，按职责分九组（详见 4.3.5）。这里重点解释为什么需要这么多字段。

**对照其它 Agent 框架的"function calling"实现**：

```python
# LangChain 风格
@tool
def bash(command: str) -> str:
    """Execute a shell command"""
    return subprocess.run(command, shell=True).stdout
```

这种"装饰器自动注册"的风格简单，但表达力有限——一个工具只是一个函数。它无法表达：

- 这个工具是只读还是写入？
- 这个工具可以并发吗？
- 这个工具是否需要用户确认？
- 这个工具的进度怎么展示在 UI？
- 工具失败时怎么提示用户？
- 工具结果太大时怎么处理？
- 同名工具如何去重？

Claude ​Code 的 Tool 抽象把这些"非业务但工程必需"的属性显式建模成接口字段。这让 Tool 不再只是函数，而是一个**带运行时元信息的能力对象**。

### 5.4.2 30+ 内置工具完整清单

源码 `src/tools.ts:193-250` 的 `getAllBaseTools()` 返回内置工具全集：

**Agent / 任务类**：
- `AgentTool` — 创建子 agent
- `TaskOutputTool` — 任务输出
- `TaskStopTool` — 停止任务
- `TeamCreateTool` / `TeamDeleteTool` — 团队管理
- `SendMessageTool` — 给 teammate 发消息

**Shell / 文件类**：
- `BashTool` — 执行 shell 命令
- `FileReadTool` — 读取文件（支持文本、图片、PDF、notebook）
- `FileEditTool` — 原地编辑文件内容
- `FileWriteTool` — 写入新文件
- `PowerShellTool` — PowerShell 命令（Windows）

**搜索类**：
- `GlobTool` — 文件路径 glob 匹配
- `GrepTool` — 文本搜索（如果有 embedded search tools 则替代）

**计划类**：
- `ExitPlanModeV2Tool` — 退出计划模式
- `EnterPlanModeTool` — 进入计划模式

**Notebook / Web / Todo 类**：
- `NotebookEditTool` — 编辑 Jupyter notebook
- `WebFetchTool` — 抓取网页内容
- `WebSearchTool` — 网页搜索
- `TodoWriteTool` — 待办事项管理

**Skill / 用户交互类**：
- `SkillTool` — 调用 skill
- `AskUserQuestionTool` — 向用户提问

**MCP 资源类**：
- `ListMcpResourcesTool` — 列出 MCP 资源
- `ReadMcpResourceTool` — 读取 MCP 资源

**其它**：
- `ToolSearch` — 工具搜索（optimistic enabled 时加入）
- `SyntheticOutputTool` — 合成输出
- ant-only、feature-gated、environment-gated 工具

### 5.4.3 工具池装配三层流水线

工具池装配在 `src/tools.ts` 中分三层：

**Layer 1：`getAllBaseTools()`**（`tools.ts:193-250`）

注释："source of truth for ALL tools"。返回内置工具全集，要求和 global system caching 配置保持同步。

**Layer 2：`getTools(permissionContext)`**（`tools.ts:271-327`）

做五类过滤：

1. `CLAUDE_CODE_SIMPLE` simple mode 只保留 Bash/Read/Edit 或 REPL
2. coordinator mode 加入 Agent/TaskStop/SendMessage
3. 排除 special tools（MCP resource tools、synthetic output）
4. `filterToolsByDenyRules()` 根据 deny 规则提前移除工具
5. REPL mode 下隐藏 primitive tools

最后调用每个工具的 `isEnabled()`。

**Layer 3：`assembleToolPool()`**（`tools.ts:345-367`）

合并内置工具和 MCP 工具：

```typescript
const builtInTools = getTools(permissionContext)
const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext)
return uniqBy(
  [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
  'name'
)
```

三个设计点：

- 内置工具和 MCP 工具先分开处理
- 两个 partition 各自按 name 排序
- concat 后再 `uniqBy('name')`，内置工具在同名冲突时优先

**为什么不能 flat sort**：`tools.ts:354-359` 注释明确：server 的 `claude_code_system_cache_policy` 会在 prefix-matched built-in tool 后放全局 cache breakpoint。如果把 MCP 工具混入内置工具排序，新 MCP 工具可能按字母序插到已有内置工具中间，导致后续工具 schema 字节位置变化，prompt cache key 失效。

这是"prompt cache 影响代码结构"的典型例子。

### 5.4.4 工具执行四层栈

`toolExecution.ts:337-490` 的 `runToolUse()` 函数实现：

```text
tool_use block
  ├─ Layer 1: 查找 + 校验
  │    ├─ findToolByName(options.tools, toolName) / alias fallback
  │    ├─ schema safeParse（Zod，失败返回 InputValidationError tool_result）
  │    ├─ tool.validateInput() （tool 自定义校验）
  │    └─ speculative classifier / internal field defense
  │
  ├─ Layer 2: Pre-execution hooks
  │    ├─ backfillObservableInput() （只修改 observer 看到的 copy）
  │    └─ runPreToolUseHooks()
  │
  ├─ Layer 3: Permission
  │    ├─ resolveHookPermissionDecision()
  │    └─ canUseTool() 调用权限引擎
  │
  └─ Layer 4: Actual call
       ├─ tool.call()
       ├─ mapToolResultToToolResultBlockParam()
       ├─ processToolResultBlock() （空结果补齐、大结果落盘、preview）
       ├─ runPostToolUseHooks()
       └─ createUserMessage({ content: [tool_result], sourceToolAssistantUUID })
```

每层都可以**短路返回 `ToolResult(is_error=true)`**：

- Layer 1 schema 失败 → `InputValidationError` tool_result
- Layer 2 hook 返回 blocking error → tool_result with hook's error
- Layer 3 权限拒绝 → tool_result "Permission denied"
- Layer 4 call 抛异常 → tool_result with error message

短路返回的 tool_result 都是合法 tool_result block，能正确配对 tool_use_id，让协议继续。这种**层层短路 + 总是返回 tool_result** 的模式是 Tool Use 协议的"防御性实现"。

### 5.4.5 partitionToolCalls：并发与串行分组

`runTools()` 使用 `partitionToolCalls()` 决定并发与串行分组（`toolOrchestration.ts:91-116`）：

```typescript
function partitionToolCalls(toolUseMessages, ctx): Batch[] {
  return toolUseMessages.reduce((acc, toolUse) => {
    const tool = findToolByName(ctx.options.tools, toolUse.name)
    const parsed = tool.inputSchema.safeParse(toolUse.input)
    const isSafe = parsed.success
      ? tool.isConcurrencySafe(parsed.data)
      : false

    // 连续的 concurrency-safe tools 合并成并发批次
    if (isSafe && acc[acc.length - 1]?.isConcurrencySafe) {
      acc[acc.length - 1].blocks.push(toolUse)
    } else {
      acc.push({ isConcurrencySafe: isSafe, blocks: [toolUse] })
    }
    return acc
  }, [])
}
```

并发批次通过 `all(..., getMaxToolUseConcurrency())` 执行，默认上限 10。

**BashTool 的 input-aware 并发安全**：

```typescript
isConcurrencySafe(input) {
  return this.isReadOnly?.(input) ?? false
}
isReadOnly(input) {
  const compoundCommandHasCd = commandHasAnyCd(input.command)
  const result = checkReadOnlyConstraints(input, compoundCommandHasCd)
  return result.behavior === 'allow'
}
```

`ls`、`grep`、`cat` 这类只读命令可以并发；但 `npm install`、`git checkout`、`sed -i`、`cd && ...` 这类可能改变环境或文件的命令不应并发。这种"输入相关"的并发判定是 BashTool 特有的复杂性，反映了 shell 命令本身的多样性。

### 5.4.6 关键设计权衡

**Why 9 个职责组**：
- 把工具的"业务能力"与"运行时元信息"分离
- 让权限、并发、UI 等关注点显式可见
- 让框架可以做通用决策（如自动并发、自动 cache、自动渲染）

**Trade-off**：
- 工具开发者需要理解 9 组字段含义
- 每个工具的代码量增加
- 但换来了运行时的可控性

## 5.5 权限系统

### 5.5.1 三级 PermissionMode

权限模式（`src/permissions/`）定义三个等级：

| 模式 | 读操作 | 编辑操作 | 命令执行 | 场景 |
|------|--------|----------|----------|------|
| `BYPASS` | 自动 | 自动 | 自动 | CI/CD，明确知道在受控环境 |
| `ACCEPT_EDITS` | 自动 | 自动 | 需确认 | 推荐交互模式 |
| `DEFAULT` | 自动 | 需确认 | 需确认 | 最安全模式 |

不同模式下 Tool 的处理：

- **READ_ONLY_TOOLS**（Read、Glob、Grep 等）：所有模式自动允许
- **EDIT_TOOLS**（Edit、Write、NotebookEdit 等）：ACCEPT_EDITS 自动允许，DEFAULT 需确认
- **COMMAND_TOOLS**（Bash、PowerShell）：所有非 BYPASS 模式需确认
- **未知工具**：默认 ASK（fail-closed）

### 5.5.2 规则引擎

除 PermissionMode 外，还有规则引擎：

**Always Allow Rules**：

```jsonc
{
  "permissions": {
    "allow": [
      "Bash(npm test:*)",     // 允许所有 npm test * 命令
      "Read(/etc/hosts)",      // 允许读取特定文件
      "WebFetch(domain:github.com)"  // 允许 fetch 特定域名
    ]
  }
}
```

**Always Deny Rules**：

```jsonc
{
  "permissions": {
    "deny": [
      "Bash(rm -rf:*)",        // 拒绝 rm -rf
      "Write(/etc/*)",         // 拒绝写系统目录
      "WebFetch(*)"            // 拒绝所有 web fetch
    ]
  }
}
```

规则匹配通过工具的 `preparePermissionMatcher()` 与 `checkPermissions()` 实现。

### 5.5.3 工具协议内建 checkPermissions

每个 Tool 都有 `checkPermissions(input, permissionContext)` 方法。它的实现可以非常复杂，例如 BashTool 的 checkPermissions：

```typescript
checkPermissions(input, ctx) {
  if (ctx.alwaysAllowRules.some(rule => matchBashRule(rule, input.command))) {
    return PermissionDecision.AllowOnce
  }
  if (ctx.alwaysDenyRules.some(rule => matchBashRule(rule, input.command))) {
    return PermissionDecision.Deny
  }
  if (ctx.permissionMode === 'BYPASS') {
    return PermissionDecision.AllowOnce
  }
  // 其他情况返回 Ask，由 UI 弹窗决定
  return PermissionDecision.Ask
}
```

这种"工具自身决定权限"的设计让权限策略可以精确到工具的语义，而不是泛泛地"所有 Bash 都问"。

### 5.5.4 非交互 fail-fast 语义

Headless / SDK / CI 环境没有 UI 弹窗，权限请求无法等待用户响应。这种场景下：

- 默认 `--print` 模式不会弹窗，遇到需要确认的工具直接失败
- 必须通过 `--dangerously-skip-permissions` 或预设 `permissions.allow` 显式批准
- 失败 tool_result 中包含明确的 "Permission required" 提示

这种"非交互 fail-fast"避免了在 CI 中静默通过未授权操作。

### 5.5.5 关键设计权衡

**Fail-Closed 默认**：未知工具默认 ASK。这意味着接入新 MCP server 或自定义工具时，第一次调用必然需要用户确认。这是"安全"与"流畅"之间的取舍：宁可让用户多按一次 Enter，也不让未授权工具静默执行。

**权限决策内嵌于执行流**：权限不是 `query_loop` 之外的过滤层，而是 Tool 协议的一部分（`checkPermissions` 是 Tool 接口的方法）。这让权限策略可以利用工具的所有领域知识（如 BashTool 知道哪些命令是 read-only）。

## 5.6 上下文压缩（Compact）

### 5.6.1 四种压缩策略

| 策略 | 触发条件 | 核心机制 | 文件 |
|------|---------|---------|------|
| 手动 compact | 用户执行 `/compact` | 立即生成摘要 + 复灌状态 | `src/services/compact/compact.ts` |
| 自动 compact | Token 接近阈值（context window - 13K buffer） | 主动压缩，保留最近 4 轮 | `src/services/compact/autoCompact.ts` |
| Session Memory compact | 有 Session Memory 文件时 | 把 Session Memory 注入摘要 | `src/services/compact/sessionMemoryCompact.ts` |
| Micro compact / Reactive compact | 轻量缩减 / API 返回 prompt_too_long | 应急压缩，每轮限 1 次 | `src/services/compact/reactiveCompact.ts` |

### 5.6.2 Token 预算与 13K Buffer

`autoCompact.ts` 的核心常量：

```typescript
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
POST_COMPACT_KEEP_TURNS = 4
```

`getEffectiveContextWindowSize(model)` 的逻辑：

1. 获取模型 context window（如 Sonnet 200K，Opus 4.7 1M）
2. 获取 max output tokens
3. 最多为 summary 输出预留 20k
4. 返回 `contextWindow - reservedTokensForSummary`

这说明 compact 不是最后一刻救火，而是从预算阶段就被考虑：留出 13K 作为缓冲，超出后主动触发 compact，避免触发到 API 的 `prompt_too_long`。

### 5.6.3 双层防御

**事后防御：`COMPACT_SYSTEM_PROMPT`**

这是压缩摘要请求的 system prompt。核心保留规则：

- 保留关键决策和结果
- 保留重要的文件路径、函数名和代码变更
- 保留任务的当前状态
- 保留任何未解决的问题或后续步骤

约束：
- 保持客观具体
- 包含精确文件路径、行号和代码标识符
- 不要加入主观评论
- 只输出摘要文本本身

这段 prompt 的质量直接决定了 query_loop 能否在长对话中保持稳定。压缩是一个有损操作——原始的工具调用细节、错误信息原文、代码片段都会被丢弃，只剩下摘要。如果摘要质量差，模型在压缩后的循环中就会表现异常。

**事前防御：`SUMMARIZE_TOOL_RESULTS`**

这是一条单行 prompt，嵌入到 system prompt 中：

```text
When working with tool results, write down any important information
you might need later in your response, as the original tool result may
be cleared later.
```

这条 prompt 的存在是因为 auto-compact 机制。模型调用工具后会收到工具的执行结果。这些结果以 `ToolResultBlock` 的形式存在于消息列表中。问题是：当 auto-compact 触发时，这些原始的工具结果会被压缩成摘要文本。如果模型在收到工具结果的那一轮回复中，只是说了"好的，我已经读取了文件"而没有把关键信息写下来，那么压缩之后，文件的具体内容就永远丢失了。

这条 prompt 指导模型在生成回复时主动提取和记录工具结果中的关键信息。从 prompt 工程的角度看，这是一条 meta-prompt——它不是在教模型如何完成用户的任务，而是在教模型如何管理自己的上下文。

**双层防御的关系**：`COMPACT_SYSTEM_PROMPT` 是事后防线（压缩发生时尽量保留），`SUMMARIZE_TOOL_RESULTS` 是事前防线（在压缩发生之前让模型主动把关键信息搬到自己的回复中）。最理想的情况是两道防线都生效，形成冗余保护。

### 5.6.4 Compact 后复灌

`compact.ts:520-585` 在 summary 完成后重建以下内容：

- read file attachments
- async agent attachments
- plan attachment
- plan mode attachment
- invoked skills attachment
- deferred tools delta
- agent listing delta
- MCP instructions delta
- session start hook messages

这就是为什么说 compact 不是"保留摘要"，而是：

```
summary + 当前文件 + 当前计划 + 当前模式 + 已调用 skills
       + 当前工具能力 + agent/MCP 状态
```

compact 后恢复的目标是：让模型压缩后仍然"坐在同一张工作台前"。

### 5.6.5 关键设计权衡

**为什么 13K buffer**：
- 给摘要生成留余地（摘要本身需要消耗几千 token）
- 给最近 4 轮对话留余地（用户可能立刻继续提问）
- 给 normalize / cache header 等 overhead 留余地

**为什么保留最近 4 轮**：
- 4 轮足以让模型记得"刚才在做什么"
- 4 轮不会让摘要后的上下文太长
- 4 是工程经验值，可调

**为什么双层防御**：
- 单一防御不可靠：模型可能没把关键信息搬到回复（事前防御失败）；摘要器可能丢失重要细节（事后防御失败）
- 双层冗余把"压缩有损"的影响降到最低

## 5.7 记忆系统（Memory）

### 5.7.1 四层 Memory 体系

| 层级 | 时间范围 | 内容 | 持久性 |
|------|---------|------|--------|
| Auto Memory | 跨会话（天/周） | 用户/项目长期协作信息 | 写入文件系统 |
| Session Memory | 会话级（分钟/小时） | 当前会话摘要 | 随会话结束丢失 |
| Agent Memory | 跨会话 | 某类 agent 的专属长期记忆 | 写入文件系统 |
| Team Memory | 跨会话 | 团队共享知识 | 写入文件系统 |

### 5.7.2 文件系统 + MEMORY.md 索引

`src/memdir/memdir.ts` 定义存储模型：

```typescript
export const ENTRYPOINT_NAME    = 'MEMORY.md'
export const MAX_ENTRYPOINT_LINES = 200
export const MAX_ENTRYPOINT_BYTES = 25_000

export function buildMemoryPrompt(params): string {
  const entrypoint = params.memoryDir + ENTRYPOINT_NAME
  const raw = fs.readFileSync(entrypoint, { encoding: 'utf-8' })
  const t = truncateEntrypointContent(raw)  // 硬截断保护
  const lines = buildMemoryLines(params.displayName, params.memoryDir, ...)
  lines.push(`## ${ENTRYPOINT_NAME}`, '', t.content)
  return lines.join('\n')
}
```

**存储模型**：

- 每条 memory 是一个独立的 Markdown 文件
- `MEMORY.md` 只维护索引链接和一行描述
- MEMORY.md 硬截断：200 行 / 25KB

**示例目录结构**：

```
~/.claude/memory/
├── MEMORY.md                          ← 索引
├── user_preferences.md                ← 单条 memory
├── feedback_no_mocks.md               ← 单条 memory
├── project_compliance_rewrite.md      ← 单条 memory
└── ref_observability_dashboard.md     ← 单条 memory
```

MEMORY.md 示例：

```markdown
- [User Preferences](user_preferences.md) — 用户喜欢简短回答，避免过度解释
- [No Mocks Rule](feedback_no_mocks.md) — 集成测试不能用 mock 数据库（来自 2025-Q4 教训）
- [Compliance Rewrite](project_compliance_rewrite.md) — auth 重写由合规驱动，倾向 compliance 优先
- [Observability Dashboard](ref_observability_dashboard.md) — 触碰 request handling 时查 grafana
```

### 5.7.3 isAutoMemoryEnabled 的优先级

```text
1. CLAUDE_CODE_DISABLE_AUTO_MEMORY 环境变量
2. CLAUDE_CODE_SIMPLE (--bare 模式) → 关闭
3. 远程模式无持久存储时 → 关闭
4. settings.json 中的 autoMemoryEnabled 字段
5. 默认：开启
```

这种多级开关让 Memory 在合适场景下生效（个人交互），在不合适场景下关闭（CI、临时容器）。

### 5.7.4 Coalescing 提取策略

传统 Debounce 可能丢弃中间状态：

```text
事件: a -> b -> c -> d
Debounce: 只处理 d（前面 a/b/c 被丢弃）
```

Claude ​Code 使用 Coalescing：

```text
事件: a -> b -> c -> d
Coalescing:
  - 收到 a：开始处理 a
  - 收到 b：设 dirty 标记
  - 处理 a 完成，发现 dirty=true：开始处理最新状态（d）
  - 处理 d 完成：清 dirty
```

差别在于：
- Debounce 假设"中间状态不重要，只要最终状态"
- Coalescing 保证"最终状态一定被处理，即使中间有竞争"

在 Memory 提取场景下，Coalescing 更可靠——会话事件不会丢失。

### 5.7.5 Session Memory 触发判定

`shouldExtractMemory(messages)`（`sessionMemory.ts:134-181`）：

- 如果 session memory 未初始化，需要先达到**初始化 token 阈值**
- 后续需要**token 增长阈值**
- **tool call 次数**也参与判断
- 如果最后一轮没有 tool call，视为**自然断点**

文件权限：
- 目录 `mode: 0o700`
- 文件 `mode: 0o600`
- 文件创建 `flag: 'wx'`（避免覆盖）

### 5.7.6 关键设计权衡

**为什么不用向量数据库**：
- 增加部署复杂度
- 跨进程同步问题
- 不可手动审计
- 不可手动编辑

**为什么用 Markdown 文件**：
- 可手动审计、编辑、diff
- 可用 git 跟踪变化
- 可被用户直接 grep
- 跨平台无依赖

**为什么 200 行 / 25KB 硬截断**：
- MEMORY.md 是被注入到 system prompt 的，太大会浪费 token
- 截断时优先保留最新内容
- 用户可以手动整理超出的内容到独立 memory 文件

**为什么 Coalescing 而非 Debounce**：
- 正确性优先于效率
- 多花一次提取换最终状态保证

## 5.8 多 Agent 协作（Agent Teams / Swarm）

### 5.8.1 触发机制

Agent Teams 模式有两种触发方式：

1. **模型主动调用 TeamCreate 工具**：模型识别任务需要拆分时
2. **环境变量 `CLAUDE_CODE_COORDINATOR_MODE=1`**：注入 coordinator prompt，让 Lead 默认按编排者方式工作

### 5.8.2 三种角色

| 角色 | System Prompt | 工具集 |
|------|---------------|--------|
| Team Lead | 完整版（11 段拼装） | 全部工具 |
| Teammate | DEFAULT_AGENT_PROMPT + TEAMMATE_ADDENDUM | 排除 Agent/TeamCreate/TeamDelete/AskUserQuestion |
| Coordinator | COORDINATOR_SYSTEM_PROMPT + 完整版 | 全部（但 prompt 指导只用 Agent/SendMessage/TaskStop） |

**Teammate 工具排除的设计原因**：
- 排除 `AgentTool / TeamCreate / TeamDelete`：避免 teammate 无限递归 spawn 子 agent
- 排除 `AskUserQuestion`：teammate 不应直接与用户交互

### 5.8.3 团队生命周期 5 步

```text
1. TeamCreate
   - 创建团队，激活 TeamContext
   - 在 ~/.claude/teams/{team_id}/ 创建团队目录
   - 在 TeamFile 中注册 leader

2. Spawn Teammates
   - Lead 调用 AgentTool(name="researcher", run_in_background=true)
   - spawn_teammate -> 选择 backend（in-process / tmux / iterm2）
   - InProcessTeammate 启动独立 query_loop
   - 在 TeamFile 中注册新 teammate

3. Teammate 执行
   - Teammate 使用独立 query_loop（非交互权限、工具过滤）
   - 通过 contextvars 隔离身份
   - 完成后通过 Mailbox 发送结果给 leader

4. Lead 收结果
   - 下一轮 turn 前，inbox polling 检查收件箱
   - <task-notification> 注入 transcript
   - Lead 根据结果决策下一步

5. TeamDelete
   - 清理团队目录
   - 退出 TeamContext
```

### 5.8.4 文件系统邮箱

通信机制：`~/.claude/teams/{team}/inboxes/{agent}.json`

每个 agent 有一个 JSON 文件作为收件箱。SendMessageTool 把消息追加到目标 agent 的 inbox。Lead 在每轮 turn 前轮询自己的 inbox，把新消息以 `<task-notification>` 形式注入 transcript。

**为什么用文件系统邮箱**：

- **零外部依赖**：不需要 Redis / RabbitMQ / DB
- **跨进程持久化**：tmux backend 下不同进程也能通信
- **低频率场景可接受无锁设计**：每秒最多几次写入，文件系统能扛住
- **可调试**：用户可以直接 `cat inbox.json` 看消息
- **可恢复**：进程崩溃后消息仍在磁盘上

### 5.8.5 身份隔离：contextvars

同进程内多个 asyncio.Task 并发运行时，需要区分"当前是哪个 agent 在执行工具"。Python 的 `contextvars`（Claude ​Code TS 中对应的是 AsyncLocalStorage）提供协程级身份隔离：

```python
# Python 复刻示例（Claude Code TS 对应 AsyncLocalStorage）
current_agent: ContextVar[AgentIdentity] = ContextVar('current_agent')

async def teammate_query_loop():
    current_agent.set(my_identity)
    # 在这个 task 内，所有 await chain 都能读到 my_identity
    await some_tool_call()  # tool 内部读 current_agent.get() 拿到 my_identity
```

这避免了"显式传递 agent_id 给每个函数"的繁琐，也避免了"多进程"的资源开销。

### 5.8.6 后端注册表

```typescript
const BACKEND_REGISTRY: Record<string, TeammateBackend> = {
  'in-process': InProcessBackend,
  'tmux':       TmuxBackend,
  'iterm2':     ITerm2PaneBackend,
}

export async function detectAndGetBackend(): Promise<BackendDetectionResult> {
  const insideTmux = await isInsideTmux()
  const inITerm2 = isInITerm2()
  if (insideTmux) return createTmuxBackend()
  if (inITerm2) {
    if (!check_it2_installed()) return needsIt2Setup
    return createITermBackend()
  }
  return createInProcessBackend()
}
```

后端选择优先级：tmux > iTerm2 native pane > in-process。这允许 teammate 在独立 tmux 窗格中运行，支持并行可视化。

### 5.8.7 Coordinator 四阶段工作流

```text
Research（并行工作者调研）
    → Synthesis（协调器汇总分析）
        → Implementation（工作者执行修改）
            → Verification（验证结果）
```

Coordinator 不直接调工具，而是只用 `AgentTool / SendMessage / TaskStop` 协调其他 teammate。

### 5.8.8 关键设计权衡

**为什么用文件系统邮箱而不是 IPC / Redis**：见 5.8.4。

**为什么不用多进程**：
- contextvars 已经能解决身份隔离
- 多进程的资源开销在本地 Agent 场景过大
- 跨进程通信反而引入新的复杂度

**为什么三种 backend**：
- in-process 适合大多数场景
- tmux 适合需要可视化的复杂任务
- iterm2 适合 macOS 用户的原生体验

## 5.9 MCP 集成

### 5.9.1 connectToServer 多传输

`src/services/mcp/client.ts` 是 MCP 连接入口。`connectToServer` 是 memoize 的，根据 serverRef.type 选择传输：

| 传输 | 用途 |
|------|------|
| sse | 旧版 HTTP+SSE |
| sse-ide | IDE 专用 SSE |
| ws-ide | IDE 专用 WebSocket |
| ws | WebSocket |
| http | HTTP polling |
| streamable-http | HTTP/2 长连接双向流 |
| stdio | 本地子进程通过 stdin/stdout |
| sdk | MCP SDK 直接调用 |

不同传输适应不同部署形态：

- **stdio**：最常见，本地 npm 包 / cargo binary 直接启动子进程
- **http / sse**：远程 MCP server，云端服务
- **ws / ws-ide**：实时双向通信，IDE 集成
- **streamable-http**：HTTP/2 长连接，省去 SSE 的限制

### 5.9.2 工具命名约定

`buildMcpToolName(serverName, toolName)` 返回 `mcp__{serverName}__{toolName}`。

例如 GitHub MCP Server 的 `create_issue` 工具，在 Claude ​Code 中的工具名是 `mcp__github__create_issue`。

**为什么用 `mcp__server__tool` 命名**：
- 避免与内建工具冲突（如果某 server 也叫 `Bash`，不会覆盖内建 BashTool）
- 一眼看出工具来源
- 权限规则可以按 server 粒度匹配（如 `mcp__github__*`）

### 5.9.3 与内建工具的融合

MCP 工具与内建工具共用：

- schema（虽然 MCP 用 JSON Schema 而非 Zod）
- permission（权限检查使用 fully qualified name）
- tool result（最终都变成 user message 的 tool_result block）
- hook（PreToolUse / PostToolUse 也对 MCP 工具生效）
- compact 后工具声明复灌

但 MCP 有几个差异：

- **schema 来自 JSON Schema 不来自 Zod**：服务端声明的格式
- **read-only / destructive / open-world 是 server annotations**：服务端在 tool 声明中标注
- **permission 默认 passthrough**：MCP 工具的 checkPermissions 默认不主动决策，依赖外层
- **调用可能涉及连接恢复、URL elicitation、progress event**：远程协议复杂性

### 5.9.4 Resources 与 Tools

MCP 还提供 Resources 概念——结构化的数据资源（如数据库行、文件内容、API 响应）。Claude ​Code 通过 `ListMcpResourcesTool` / `ReadMcpResourceTool` 暴露给模型，让模型可以查询和读取这些资源。

Resources 与 Tools 的区别：
- Tools：执行操作（可能有副作用）
- Resources：读取数据（无副作用）

这种区分让 LLM 可以更清晰地规划"先看 resource 了解情况，再用 tool 执行"。

### 5.9.5 关键设计权衡

**为什么 Claude ​Code 既做 Client 又做 Server**：
- Client 让用户能用社区 MCP 生态
- Server 让 Claude ​Code 能被其它 AI 应用复用
- 两个角色共享同一工具实现，不增加维护负担

## 5.10 Hooks 系统

### 5.10.1 Hook 类型

Hooks 通过 shell 命令实现，事件类型包括：

| Hook | 触发时机 |
|------|---------|
| `PreToolUse` | 工具调用前 |
| `PostToolUse` | 工具调用后 |
| `UserPromptSubmit` | 用户输入提交后 |
| `Stop` | Agent 准备结束时 |
| `Session.Start` | 新会话开始 |
| `Session.End` | 会话结束 |

### 5.10.2 配置与热更新

Hook 配置在 settings.json 中声明：

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "/path/to/check.sh" }
        ]
      }
    ]
  }
}
```

`startHooksWatcher()`（在 setup.ts 中启动）监听 settings 变化，hook 配置可以热更新。

### 5.10.3 拦截语义

Hook 的 stdout 是结构化输出（JSON），可以包含：

- `blocking_error`：阻断当前操作，附带错误消息
- `prevent_continuation`：阻止 agent 继续（用于 Stop hook）
- `additional_context`：追加上下文到 message
- `hook_message`：追加 message 到 transcript

通过这些字段，hook 可以：
- 在 PreToolUse 阻止危险命令
- 在 PostToolUse 注入额外信息
- 在 UserPromptSubmit 重写用户输入
- 在 Stop 强制 Agent 继续

### 5.10.4 关键设计权衡

**为什么用 shell 命令而不是 JS 插件**：
- 任何编程语言都能写 hook（不限 JS/TS）
- 可以直接 `curl` 内部 API
- 不需要打包成 Claude ​Code 插件
- 易于审计（hooks 就是普通脚本）

**Trade-off**：每次触发都要 spawn 子进程，有性能成本。但 hook 通常低频，可以接受。

## 5.11 Skills 系统

### 5.11.1 frontmatter 字段

Skill 是带 frontmatter 的 Markdown 文件。frontmatter 字段包括：

```yaml
---
skillName: my-skill
description: One-line description shown to model
allowedTools: [Read, Edit, Bash]
argumentNames: [arg1, arg2]
whenToUse: Trigger conditions
version: 1.0
model: claude-sonnet-4-6
disableModelInvocation: false
userInvocable: true
executionContext: this-session | new-session
agent: explorer
paths: [/path/to/match]
effort: low | medium | high
shell: bash
---

# Skill body (Markdown)

Instructions to the model when this skill is invoked.
```

### 5.11.2 三层 skill 目录

`getSkillDirCommands(cwd)` 读取以下目录：

- **managed skills dir**（Anthropic 内置）
- **user skills dir**（`~/.claude/skills/`）
- **project skills dirs**（`.claude/skills/` in cwd）
- **additional dirs**（`--add-dir`）
- **legacy commands dir**（向后兼容）

支持 `--bare`：跳过自动发现，只加载显式 `--add-dir`。

### 5.11.3 Slash command 注册

每个 skill 自动注册为 slash command。例如 `~/.claude/skills/review.md` 注册为 `/review` 命令。

调用方式：
- 用户输入 `/review` 触发
- 模型调用 `SkillTool(name="review")` 触发
- 外部 hook / SDK 调用触发

### 5.11.4 内嵌 shell 与变量替换

`createSkillCommand().getPromptForCommand()` 在生成 prompt 时：

- 添加 base directory
- 替换参数（`{arg1}` → 实际值）
- 替换 `${CLAUDE_SKILL_DIR}` / `${CLAUDE_SESSION_ID}`
- 执行内嵌 shell（code block ```` ```! ... ``` ```` 或 inline `!command`）
- 返回 text block

**安全约束**：MCP skills 不允许执行内嵌 shell。shell 命令仍走 tool permission。

### 5.11.5 关键设计权衡

**为什么用 Markdown + frontmatter**：
- 可手动编辑、审计、版本控制
- 不需要专门的 IDE 支持
- frontmatter 提供结构化元信息
- body 是自由文本，易写易读

**为什么三层目录**：
- managed：官方维护
- user：用户跨项目复用
- project：项目专属
- 优先级清晰：project > user > managed

## 5.12 Session 持久化

### 5.12.1 JSONL 格式

每个会话以 JSONL（JSON Lines）格式持久化：

```text
~/.claude/projects/{project-hash}/{session-id}.jsonl
{"type": "user", "content": "...", "timestamp": "..."}
{"type": "assistant", "content": [...], "timestamp": "..."}
{"type": "user", "content": [{"type": "tool_result", "..."}], "timestamp": "..."}
...
```

**为什么 JSONL**：
- **流式写入友好**：每条消息一行，写入立即可读
- **部分损坏可恢复**：异常退出可能让最后一行不完整，但前面的行都能解析
- **可被 grep / awk 处理**：标准 Unix 工具直接处理
- **可截断而不损坏**：用户手动 `head -n 100` 截取也是合法 JSONL

### 5.12.2 Transcript Validation

`validate_transcript()` 在加载时修复以下异常：

- 最后一行不完整（异常退出导致）：丢弃
- tool_use 没有对应 tool_result（崩溃在工具执行中间）：补 synthetic error tool_result
- tool_result 找不到对应 tool_use：丢弃
- 连续两个 user message：合并

修复后的 transcript 满足 API 协议，可以继续会话。

### 5.12.3 TaskRegistry

`src/session/task_registry.py`（对应 TS 实现）维护后台任务的状态快照：

```text
~/.claude/tasks/
├── task_abc123.json   ← 后台任务状态
├── task_def456.json
└── ...
```

每个后台任务（Subagent、Background Agent）的状态被快照保存，CLI 重启后可以列出、查看、attach。

### 5.12.4 Session Resume 流程

```text
claude -c <session-id>
  ├─ 读取 ~/.claude/projects/{hash}/{session-id}.jsonl
  ├─ validate_transcript() 修复异常
  ├─ 加载关联的 task snapshots
  ├─ 重建 QueryEngine.mutableMessages
  ├─ 重建 permissionContext / totalUsage / readFileState
  ├─ 进入 REPL，显示历史 transcript
  └─ 等待新用户输入
```

### 5.12.5 关键设计权衡

**为什么 transcript 必须是唯一 SoT**：
- 多份状态会有同步问题
- 单一 SoT 简化故障恢复
- transcript 本身就是审计日志

**为什么 JSONL 而非二进制格式**：
- 可读性 > 紧凑性（开发者 CLI 场景）
- 兼容性 > 性能
- 易于工具链处理

---

## 本章小结

本章逐一深入解析了 Claude ​Code 的核心模块：

- **QueryEngine**（5.1）：会话生命周期容器，跨 turn 状态持有者
- **query()**（5.2）：单 turn AsyncGenerator 状态机，六阶段循环 + 七种 transition
- **StreamingToolExecutor**（5.3）：边收流边执行，延迟降低 30-50%
- **工具系统**（5.4）：Tool 能力对象（9 个职责组）+ 三层装配 + 四层执行栈
- **权限系统**（5.5）：三级 PermissionMode + 规则引擎 + 工具内建 checkPermissions
- **上下文压缩**（5.6）：四种策略 + 双层防御 + 状态复灌
- **记忆系统**（5.7）：四层 Memory + Markdown 文件 + Coalescing
- **多 Agent**（5.8）：Agent Teams + 文件系统邮箱 + contextvars
- **MCP**（5.9）：多传输 + 双向角色 + 与内建工具融合
- **Hooks**（5.10）：shell 命令 + 四种拦截语义
- **Skills**（5.11）：frontmatter Markdown + 三层目录 + slash command
- **Session 持久化**（5.12）：JSONL + transcript validation + TaskRegistry

每个模块的设计都体现了"协议正确性优先于输出漂亮"的核心哲学。

第 6 章将从稳定性视角横切这些模块，看 Claude ​Code 如何保证 Runtime 在各种异常下不崩。
