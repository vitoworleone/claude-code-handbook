# 第 4 章 详细设计

> 本章给出 Claude ​Code 的整体架构与详细设计。设计的目标不是把代码"翻译"成中文，而是抽离出系统的骨架与不变量，让读者从更高视角理解第 5 章每个模块为什么这样实现。

---

## 4.1 整体架构概览

### 4.1.1 一句话定位

Claude ​Code 的整体架构可以用一句话概括：

> **Claude ​Code = Context Compiler + Tool Runtime + Permission Gate + Memory System + UI Layer**

这五个组件的关系是：UI Layer 接收用户输入；Context Compiler 把内部 transcript、CLAUDE.md、IDE 选区、附件、hooks、skills 等编译成模型可见的 API messages；Tool Runtime 执行模型决策的工具调用；Permission Gate 在 Tool Runtime 之前拦截；Memory System 在会话内 / 跨会话维护长期状态。

这个定位与"chatbot wrapper"形成对比：

| 维度 | Chatbot Wrapper | Claude ​Code |
|------|----------------|-------------|
| 输入 | 用户字符串直接发模型 | 经过 Context Compiler 多阶段处理 |
| 工具调用 | 函数映射 | 带权限/并发/UI 语义的能力对象 |
| 上下文 | 聊天历史 | 工作台状态（含 file cache / plan / skill） |
| 记忆 | 模型自带（如果有） | 可审计的 Markdown 文件系统 |
| 错误 | 用户重试 | Runtime 层三层恢复 |

### 4.1.2 六层职责架构

Claude ​Code 的运行时架构可以分为六层。每层有明确职责，且层之间的依赖方向是单向的（上层依赖下层，下层不依赖上层）：

```text
┌──────────────────────────────────────────────────────────┐
│ Layer 1: CLI 引导层                                       │
│   src/entrypoints/cli.tsx                                │
│   职责：fast path 分流 / dynamic import main             │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│ Layer 2: 初始化层                                         │
│   src/entrypoints/init.ts （trust 前）                   │
│   src/setup.ts             （trust 后）                  │
│   职责：env var / cert / HTTP agent / cwd / hooks / mem  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│ Layer 3: 控制面 / TUI 层                                  │
│   src/main.tsx        总控编排                            │
│   src/screens/REPL.tsx   交互 UI 中枢                    │
│   src/replLauncher.tsx   REPL 启动边界                    │
│   src/state/store.ts     外部状态总线                     │
│   src/ink/ink.tsx        自定义 Ink renderer              │
│   职责：用户输入接受 / UI 渲染 / 队列调度 / 权限弹窗      │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│ Layer 4: 执行内核                                         │
│   src/QueryEngine.ts   会话生命周期容器                   │
│   src/query.ts         单 turn AsyncGenerator 状态机     │
│   src/query/deps.ts    依赖注入对象                       │
│   src/services/api/claude.ts   模型 API 适配             │
│   职责：调用模型 / 流式归并 / 状态机迁移 / 错误恢复       │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│ Layer 5a: Tool / Permission 层                            │
│   src/services/tools/toolOrchestration.ts   并发分组      │
│   src/services/tools/toolExecution.ts       执行四层栈    │
│   src/services/tools/StreamingToolExecutor.ts            │
│   src/services/tools/Tool.ts                能力对象协议  │
│   src/permissions/                          权限引擎       │
│                                                            │
│ Layer 5b: Memory / Persistence 层                         │
│   src/memdir/memdir.ts          Memory 文件系统           │
│   src/services/compact/         Compact 四策略             │
│   src/session/                  Session 持久化             │
│                                                            │
│ 职责：工具执行 / 权限决策 / 上下文压缩 / 记忆提取         │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│ Layer 6: 扩展层                                           │
│   src/services/mcp/             MCP 协议客户端             │
│   src/hooks/                    Hook 系统                  │
│   src/skills/                   Skills 系统                │
│   src/tools/AgentTool/          Subagent                   │
│   src/utils/swarm/              Agent Teams                │
│   src/bridge/                   Remote / Bridge            │
│   职责：外部能力接入 / 跨进程协调 / 多 Agent              │
└──────────────────────────────────────────────────────────┘
```

**层间约束**：

1. **Layer 4（执行内核）是所有入口的汇聚点**：REPL、SDK、MCP Server、Bridge、Subagent 都最终调用 `query.ts`
2. **Layer 5 / Layer 6 由 Layer 4 通过依赖注入接入**：query_loop 不直接 require 工具/权限/MCP 模块，而是通过 `QueryParams` / `toolUseContext` / `deps` 接受这些依赖
3. **Layer 1 / Layer 2 是一次性的**：进入 Layer 3 后不应该再回到 Layer 1 / Layer 2
4. **Layer 3 通过 Layer 4 间接驱动 Layer 5 / Layer 6**：UI 不直接调工具，而是通过 query() 触发

### 4.1.3 三种架构表述的对照

源码分析过程中存在三种架构表述，它们指向同一系统但视角不同：

| 表述 | 视角 | 核心切分 |
|------|------|---------|
| 六层职责架构（本节） | 功能聚合 | 按 "做什么事" 分层 |
| 七层目录架构 | 源码物理结构 | 按 `src/` 子目录分层 |
| 六层运行时数据流架构 | 数据流动 | 按 messages 在 Runtime 中的处理阶段分层 |

**七层目录架构**：

1. Entry 入口层（`src/entrypoints/`）
2. Orchestration 编排层（`src/main.tsx`、`src/setup.ts`）
3. UI 渲染层（`src/screens/`、`src/components/`、`src/ink/`）
4. Tools 工具层（`src/services/tools/`、`src/tools/`）
5. Commands 命令层（`src/commands.ts`、`src/commands/`）
6. Services 服务层（`src/services/`）
7. Infrastructure 基础设施层（`src/utils/`、`src/state/`）

**六层运行时数据流架构**：

1. 入口层（接收外部触发）
2. 运行形态层（决定 REPL / Headless / Bridge / Subagent）
3. 输入编排层（合并 text / image / IDE selection / pasted / slash command / hook context / attachment）
4. Query 执行内核（while(true) AsyncGenerator）
5. Tool / Permission + Context / Compact + Memory 层
6. 扩展层

本论文以"六层职责架构"为主线，因为它最接近设计者的思维方式；其它两种表述会在需要时作为补充视角引入。

## 4.2 各层详细职责

### 4.2.1 Layer 1: CLI 引导层

**核心文件**：`src/entrypoints/cli.tsx`

**职责**：进程启动后的第一道闸门。识别 fast path 并尽早退出，避免常见命令付出加载完整应用的成本。

**实现策略**：所有 import 都是动态的，普通命令不应该付出加载 React/Ink/MCP/skills/plugins 的成本。`main()` 函数的逻辑骨架：

```typescript
async function main() {
  const argv = parseArgs(process.argv)

  // 快路径分流 —— 命中则执行并退出
  if (argv['--version'] || argv['-v'] || argv['-V']) {
    console.log(`${MACRO.VERSION} (Claude ​Code)`)
    return  // 零额外 imports
  }

  if (argv['--dump-system-prompt']) {
    await dumpSystemPrompt()
    return
  }

  if (argv['remote-control']) return runRemoteControl(argv)
  if (argv['daemon'] || argv['bg'] || argv['runner']) return runDaemonOrBackground(argv)
  if (argv['--bare']) process.env.CLAUDE_CODE_SIMPLE = '1'

  // 正常路径：启动 early input capture，动态 import main.js
  startCapturingEarlyInput()
  await import('../main.js').then(m => m.main(argv))
}
```

**Fast Path 完整清单**（源码 `cli.tsx:28-298`）：

| Fast Path | 触发条件 | 加载深度 |
|-----------|---------|---------|
| `--version` | `-v/-V/--version` | 零额外 imports |
| system prompt dump | `--dump-system-prompt` | ant-only，动态 import dump handler |
| Chrome / MCP / computer-use | 特定 flag | import MCP/native host runner |
| daemon worker | `daemon` 子命令 | 只 import worker registry |
| remote / bridge | `remote-control/bridge` | enable configs + auth check + GrowthBook + policy limits |
| daemon supervisor | `daemon` supervisor | enable configs + sinks + daemon main |
| background sessions | `ps/logs/attach/kill/--bg` | import `../cli/bg.js` |
| template jobs | `template` 子命令 | import handler，结束后 `process.exit(0)` |
| runners | self-hosted runner | import runner main |
| tmux worktree | `--worktree --tmux` | enable configs，可能 exec into tmux |

**设计决策**：dynamic import 作为启动边界。`await import('./main.js')` 是 fast path 与完整运行时的分界线。在此之前，内存中只有 argument parser 和少量 util。

### 4.2.2 Layer 2: 初始化层

**核心文件**：`src/entrypoints/init.ts`（trust 前）、`src/setup.ts`（trust 后）

**职责**：把进程从"空壳"推进到"可运行状态"。被 Trust Dialog 分割为前后两段。

**init.ts（trust 前）**：

```typescript
export async function init(argv) {
  applySafeEnvironmentVariables()    // 只应用安全 env var
  initializeCertificates()           // 证书与 HTTPS 代理
  initializeHttpAgent()              // HTTP agent 配置
  initTelemetrySkeleton()            // 注册 telemetry sink，不发事件
}

export async function initializeTelemetryAfterTrust() {
  applyFullEnvironmentVariables()    // trust 通过后才应用全部 env var
  attachAnalyticsSink()              // 开始处理 telemetry 事件
}
```

**setup.ts（trust 后）**：

```typescript
export async function setup(argv, permissionContext) {
  setCwd(resolvedWorkingDir)         // 设置工作目录
  startHooksWatcher()                // 监听 hooks 配置变化
  initWorktreeSnapshot()             // tmux/worktree 快照
  initSessionMemory()                // 初始化 session memory
  startTeamMemoryWatcher()           // team memory 文件监听
}
```

**Trust Boundary 的设计意图**：trust 建立前只应用安全的环境变量，防止"配置文件 / includes 本身是攻击面"的风险。详见第 6.5 节。

### 4.2.3 Layer 3: 控制面 / TUI 层

**核心文件**：

- `src/main.tsx`：4683 行的总控编排，包含 Commander 设置、preAction、default action handler、Phase 0-3 启动流程
- `src/replLauncher.tsx`：REPL 启动边界，动态加载 App+REPL 组件后启动 Ink 渲染循环
- `src/screens/REPL.tsx`：agent runtime shell，维护完整 AppState（消息、输入框、权限弹窗、任务、远程状态）
- `src/state/store.ts`：外部 store + useSyncExternalStore
- `src/ink/ink.tsx`：自定义 Ink renderer
- `src/components/PromptInput/PromptInput.tsx`：输入编排器

**关键机制**：

1. **AppState 与外部 store**：避免 React Context 全树重渲染
2. **useDeferredValue(messages)**：让昂贵的消息处理在较低优先级运行
3. **QueryGuard 三状态机**：`idle -> dispatching -> running -> idle`，避免重复启动
4. **useQueueProcessor**：订阅命令队列与 QueryGuard，在合适时机触发处理
5. **Permission Overlay**：`useCanUseTool` 把权限决策与 `toolUseConfirmQueue` 连接，REPL 渲染 `<PermissionRequest>` overlay

### 4.2.4 Layer 4: 执行内核

**核心文件**：

- `src/QueryEngine.ts`：会话生命周期容器
- `src/query.ts`：while(true) AsyncGenerator 状态机
- `src/query/deps.ts`：依赖注入对象（callModel、microcompact、autocompact、uuid）
- `src/services/api/claude.ts`：模型 API 适配

**双层边界**：

| 层级 | 文件 | 职责 |
|------|------|------|
| 会话生命周期层 | `QueryEngine.ts` | 一个 conversation 一个 engine，持有跨 turn 的 mutableMessages、abortController、permissionDenials、totalUsage、readFileState |
| 单 turn 执行内核 | `query.ts` | 单个用户 turn 内的 agent loop，负责上下文准备、模型流、工具执行、恢复策略、状态迁移 |

详见第 5.1 节（QueryEngine）与第 5.2 节（query_loop）。

### 4.2.5 Layer 5: Tool / Permission + Memory / Persistence 层

**Tool / Permission 部分**：

- `src/services/tools/Tool.ts`：能力对象协议（9 个职责组）
- `src/services/tools/toolOrchestration.ts`：runTools、partitionToolCalls
- `src/services/tools/toolExecution.ts`：runToolUse 执行四层栈
- `src/services/tools/StreamingToolExecutor.ts`：边收流边执行
- `src/permissions/`：权限引擎

**Memory / Persistence 部分**：

- `src/memdir/memdir.ts`：Memory 文件系统入口
- `src/services/compact/`：Compact 四策略
- `src/session/`：Session 持久化

详见第 5.4 - 5.7 节。

### 4.2.6 Layer 6: 扩展层

**MCP**：`src/services/mcp/client.ts`、`src/entrypoints/mcp.ts`
**Hooks**：`src/hooks/`、`src/utils/hooks/`
**Skills**：`src/skills/`、`src/services/skills/`
**Subagent**：`src/tools/AgentTool/`
**Agent Teams**：`src/utils/swarm/`
**Bridge / Remote**：`src/bridge/`

详见第 5.8 - 5.11 节。

## 4.3 数据模型

### 4.3.1 Message 类型层级

Claude ​Code 内部 transcript 中存在多种 message 类型，远超 API 协议要求：

```text
Message（抽象基类）
├── UserMessage          ← API 协议
│   ├── 普通用户输入
│   ├── 工具结果（content 含 tool_result block）
│   ├── meta message（如 max_output_tokens recovery 注入的 "Please continue"）
│   └── attachment-only message
│
├── AssistantMessage     ← API 协议
│   └── content：text / thinking / tool_use blocks
│
├── SystemMessage        ← 仅内部
│   ├── local_command 输出（如 /help 结果）
│   ├── slash command 输出
│   └── notification message
│
├── TombstoneMessage     ← 仅内部
│   └── 被压缩或删除的消息占位
│
└── ToolUseSummaryMessage ← 仅内部
    └── 工具调用的摘要（用于压缩时保留）
```

**内部 messages vs API messages 的差异**：

- API 只允许 UserMessage / AssistantMessage
- API messages 中不能有 SystemMessage / TombstoneMessage / display-only virtual message
- API messages 中连续两个 user message 必须合并
- API messages 中必须保证 tool_use ↔ tool_result 配对

`normalizeMessagesForAPI()`（详见 4.5.2）负责把内部 transcript 投影成 API 兼容的 messages。

### 4.3.2 ContentBlock 类型

| Block Type | 出现位置 | 字段 |
|------------|---------|------|
| `text` | user / assistant | `text: string` |
| `image` | user | `source: { type, media_type, data \| url }` |
| `document` | user | PDF base64 |
| `tool_use` | assistant | `id, name, input` |
| `tool_result` | user | `tool_use_id, content, is_error` |
| `thinking` | assistant | `thinking: string, signature` |
| `redacted_thinking` | assistant | `data: string` |

**内部扩展 block**（非 API 协议）：

- progress block：流式进度提示
- display-only block：仅用于 UI 显示
- hook_additional_context block：UserPromptSubmit hook 注入的附加上下文

这些扩展 block 在 normalizeMessagesForAPI 时被过滤或转换。

### 4.3.3 ToolUseContext、QueryParams、State 三大上下文容器

**ToolUseContext**（贯穿工具执行的运行时上下文）：

```typescript
interface ToolUseContext {
  tools: Tool[]                    // 当前可用工具池
  mcpClients: Map<string, MCPClient>
  agentDefinitions: AgentDefinition[]
  abortController: AbortController
  readFileState: ReadFileState      // 文件读取缓存
  permissionContext: PermissionContext
  appState: AppStateGetter
  fileCache: FileCache
  memoryTracking: MemoryTracking
  skillTracking: SkillTracking
  // ... 等等
}
```

**QueryParams**（query() 入参，调用方显式传入）：

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

**State**（query_loop 内部跨 iteration 的可变状态）：

```typescript
interface State {
  messages: Message[]                     // 本 iteration 用来发模型的消息
  toolUseContext: ToolUseContext          // 工具上下文（可被 contextModifier 更新）
  autoCompactTracking: AutoCompactTracking
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | null
  pendingToolUseSummary: Promise<...> | null
  stopHookActive: boolean
  turnCount: number
  transition: { reason: string }          // 上一轮继续的原因
}
```

**为什么要分 QueryParams / State**：

- QueryParams 是"调用一次 query() 需要的所有外部上下文"，由调用方提供
- State 是"loop 内部跨 iteration 的演化状态"，由 loop 自己维护
- 这种分离让 loop 的状态迁移更清晰：所有真正的"继续下一轮"都体现为构造完整的 next State 然后 `state = next; continue`

### 4.3.4 ToolResult\<T\>

```typescript
interface ToolResult<T> {
  data: T                                // 工具实际返回的数据
  newMessages?: Message[]                // 工具可追加的额外消息
  contextModifier?: ContextModifier      // 工具可修改后续工具看到的 context
  mcpMeta?: MCPMeta                      // MCP 工具的额外元信息
}
```

**关键设计点**：

- `data` 才是会被回灌给模型的内容（通过 `mapToolResultToToolResultBlockParam` 转换为 `tool_result` block）
- `newMessages` 用于工具想要追加额外 user/assistant message（如 AgentTool 完成时追加 `<task-notification>`）
- `contextModifier` 用于工具修改后续工具看到的上下文（如修改文件后更新 readFileState）
- `contextModifier` 只对非 concurrency-safe 工具有意义。并发场景中按 tool_use id 排队，批次完成后按 block 顺序应用

### 4.3.5 Tool 能力对象（9 个职责组）

Tool 类型定义（`src/Tool.ts:362-695`），按职责分九组：

| 职责组 | 字段 | 运行时意义 |
|--------|------|-----------|
| 身份和发现 | `name, aliases, searchHint` | 模型调用名、旧名兼容、ToolSearch 关键词 |
| 模型契约 | `prompt(), description(), inputSchema, inputJSONSchema, outputSchema, strict` | 告诉模型怎么调用，告诉 runtime 怎么校验 |
| 执行核心 | `call()` | 工具真正的副作用或读取逻辑 |
| 输入等价与路径 | `inputsEquivalent(), getPath(), backfillObservableInput()` | 去重、路径权限、hook 可观察输入 |
| 并发与中断 | `isConcurrencySafe(), interruptBehavior()` | 多工具调度和用户新消息打断语义 |
| 安全性质 | `isReadOnly(), isDestructive(), isOpenWorld(), requiresUserInteraction()` | UI、权限、auto classifier、风险判断 |
| 权限决策 | `validateInput(), checkPermissions(), preparePermissionMatcher()` | 工具校验、是否需要批准、permission pattern 匹配 |
| UI 渲染 | `userFacingName(), getToolUseSummary(), getActivityDescription(), 各类 render 函数` | transcript、progress、rejected/error、grouped rendering |
| 模型结果 | `toAutoClassifierInput(), mapToolResultToToolResultBlockParam(), maxResultSizeChars, extractSearchText()` | 安全分类器输入、模型可见结果、结果落盘、transcript 搜索 |

**`buildTool()` 默认值（Fail-Closed 取向）**：

```typescript
isEnabled            -> true
isConcurrencySafe    -> false   // 默认不并发
isReadOnly           -> false   // 默认非只读
isDestructive        -> false   // 默认非破坏性
checkPermissions     -> defaultAllow  // 默认允许（由外层权限系统控制）
```

最重要的默认值是 `isConcurrencySafe: false` 和 `isReadOnly: false`：未覆盖的写工具不会被错误并发执行，权限系统也不会低估其风险。

## 4.4 控制流

### 4.4.1 启动六阶段流转

```text
Phase 0: process starts
  entrypoints/cli.tsx top-level env side effects

Phase 1: CLI bootstrap fast path dispatch
  parse argv
  --version zero import
  其他 fast paths 只 import target module
  normal path starts early input capture -> dynamic import main.js

Phase 2: main.tsx top-level prefetch + module evaluation
  profileCheckpoint(main_tsx_entry)
  startMdmRawRead()
  startKeychainPrefetch()
  load full runtime imports
  feature-gated requires
  profileCheckpoint(main_tsx_imports_loaded)

Phase 3: Commander setup + preAction
  create Commander program
  build options
  preAction awaits MDM/keychain promises
  init()
  sinks/migrations/remote managed settings

Phase 4: Default action preparation
  parse input prompt
  toolPermissionContext
  getTools()
  import setup.js
  setup() in parallel with getCommands/getAgentDefinitions
  resolve model/settings/hooks/MCP/files

Phase 5A: interactive branch
  getRenderContext
  import ./ink.js
  createRoot
  showSetupScreens
  launchRepl
  renderAndRun -> first render -> deferred prefetches

Phase 5B: headless/print branch
  build headless store
  connect MCP with bounded waits
  start selected prefetches
  dynamic import src/cli/print.js
  runHeadless
```

每个阶段失败有不同含义，错误上下文精确（详见第 7.5 节）。

### 4.4.2 query_loop 的 while(true) AsyncGenerator

`query()` 函数的签名：

```typescript
export async function* query(
  userMessages: Message[],
  systemPrompt: SystemPrompt,
  toolUseContext: ToolUseContext,
  deps: QueryDeps,
): AsyncGenerator<
  StreamEvent | RequestStartEvent | Message | TombstoneMessage | ToolUseSummaryMessage,
  Terminal
>
```

为什么选 AsyncGenerator：

1. **流式 UI**：TUI 需要逐 token、逐 message、逐 tool progress 渲染
2. **协议事件**：SDK/headless 需要消费结构化 message/event
3. **可中断**：AbortController、用户新输入、权限弹窗都可能打断
4. **可组合**：subagent、background agent、QueryEngine 可以用同一种 `for await` 消费方式复用执行内核

普通 Promise 只能返回最终结果，无法表达中间事件；EventEmitter 容易失去 backpressure 和 return terminal value；AsyncGenerator 刚好能表达"持续 yield 事件，最终 return terminal"。

代价是内部控制流很长，`query.ts` 成为巨型状态机；yield 的类型很宽，调用方必须 switch message type；测试需要关心事件序列。源码用 `query/deps.ts` 收窄外部依赖，是对这种复杂性的补偿。

### 4.4.3 六阶段循环（每次 iteration）

```text
Phase 1: 准备
  getMessagesAfterCompactBoundary()
  applyToolResultBudget()
  history snip
  microcompact
  context collapse projection
  autocompact
  normalizeMessagesForAPI()

Phase 2: 调用
  for await (const event of deps.callModel(...)):
    yield event unless withheld
    if tool_use block ready:
      collect toolUseBlock
      maybe streamingToolExecutor.addTool(...)

Phase 3: 恢复
  if no tool_use and prompt_too_long withheld:
    if collapse drain possible: collapse_drain_retry
    elif reactive compact: reactive_compact_retry
  if max_output_tokens truncation:
    if can escalate: max_output_tokens_escalate
    elif within recovery limit: max_output_tokens_recovery
  if API recoverable error: retry with linear backoff

Phase 4: 累积
  归并 streaming events -> assistantMessages + toolUseBlocks

Phase 5: 物化
  build AssistantMessage from accumulated stream
  write to transcript

Phase 6: 执行
  if no tool_use:
    run stop hooks
    if blocking errors: stop_hook_blocking continue
    elif token budget continuation: token_budget_continuation
    else: terminal return
  else:
    runTools() / collect streaming results
    construct UserMessage with tool_result blocks
    next_turn continue
```

详见第 5.2 节。

### 4.4.4 七种 transition.reason

| transition.reason | 触发条件 | 状态变化 |
|------------------|----------|----------|
| `next_turn` | 本轮有 tool_use，结果已收集完 | `messages = messagesForQuery + assistantMessages + toolResults`，`turnCount + 1` |
| `collapse_drain_retry` | prompt-too-long withheld，context collapse 可 drain | `messages = drained.messages`，不增加 turnCount |
| `reactive_compact_retry` | prompt-too-long / media-size error 可通过 reactive compact 恢复 | `messages = postCompactMessages`，`hasAttemptedReactiveCompact = true` |
| `max_output_tokens_escalate` | 输出截断且可提升 max output tokens 上限 | `maxOutputTokensOverride = ESCALATED_MAX_TOKENS` |
| `max_output_tokens_recovery` | 输出截断且仍在恢复次数限制内 | 追加 continuation meta user message |
| `stop_hook_blocking` | Stop hook 返回 blocking errors | 把 blocking error 作为 meta user message 注入 |
| `token_budget_continuation` | TOKEN_BUDGET feature 要求继续产出 | 追加 budget nudge meta message |

**注意**：history snip、microcompact、autocompact、context collapse projection、API message normalization 都会改变模型看到的上下文，但它们不是同一类 `transition.reason`。它们发生在同一 iteration 的 pre-call transformation 或 API boundary transformation 中，不一定触发 `state = next; continue`。

### 4.4.5 六个 transcript 写入点

| # | 写入点 | 位置 | 写入内容 | 设计原因 |
|---|--------|------|---------|---------|
| 1 | 用户输入追加 | `REPL.tsx → handlePromptSubmit()` / `QueryEngine.submitMessage()` | UserMessage、attachments、slash command 输出 | 输入一旦接受就进入会话，支持 resume 与审计 |
| 2 | compact 替换 | `queryLoop() → deps.autocompact() → buildPostCompactMessages()` | compact summary、boundary、复灌附件 | 压缩旧历史，同时保留工作状态 |
| 3 | max_output_tokens 恢复注入 | `queryLoop()` 截断恢复分支 | meta UserMessage："Please continue" | 输出被截断时保持协议内 continuation |
| 4 | API 返回 assistant | streaming loop 累积后构建 | AssistantMessage，含 text/thinking/tool_use | 将流式输出物化为 transcript 事实 |
| 5 | 工具结果回流 | `runTools()` / `StreamingToolExecutor` / `toolExecution.ts` | content 为 tool_result block 的 UserMessage | Anthropic 协议要求工具结果作为 user message 回到下一轮 |
| 6 | 系统注入与 hook 输出 | `createSystemMessage()` / hook 系统 / `normalizeMessagesForAPI()` | system/local_command/notification/hook additional context | 内部系统事件可记录，但 API 只保留模型需要看到的部分 |

这六个写入点共同定义了 **transcript 作为 Runtime 状态账本**的边界。所有可恢复性、压缩、工具回流和下一轮模型调用都依赖它。

## 4.5 Transcript 协议

### 4.5.1 内部 transcript vs API messages

Claude ​Code 内部存在两种"消息"概念：

- **内部 transcript**（`mutableMessages`）：包含 UI 进度、附件、compact boundary、虚拟消息、hook 输出、TombstoneMessage 等
- **API messages**：仅包含 Anthropic 协议允许的 user / assistant content

二者的关系：内部 transcript 是"全量记录"，API messages 是"投影"。每次调用模型前都要把内部 transcript 投影成 API messages。投影过程必须满足：

1. 只保留 user / assistant role
2. 过滤 progress / display-only / TombstoneMessage 等内部消息
3. 合并连续 user messages（如 hook context message 与下一个用户输入合并）
4. 处理 tool reference 与 unavailable tools
5. 保证 tool_use ↔ tool_result 配对（如有孤儿则补 synthetic）

### 4.5.2 normalizeMessagesForAPI 的职责

`normalizeMessagesForAPI(messages, filteredTools)`（位于 `src/utils/messages.ts:1989-2103`）是 Runtime 必需模块。如果内部历史包含连续两个 user message、孤儿 tool_result 或 synthetic API errors，不经过规范化直接发 API 会导致协议错误。

主要职责：

1. **重排 attachments**：把 attachments 移到合适的位置（通常是新一轮 user message 的开头）
2. **过滤内部消息**：progress、display-only virtual messages、非 local_command 的 system messages、synthetic API errors
3. **合并连续 user messages**：把相邻的 user message 合并成一条
4. **处理 tool reference**：把 internal tool reference 转换为 API 可识别的形式
5. **处理 unavailable tools**：模型引用了已被移除的工具时，转化为合适的 fallback

### 4.5.3 Transcript 作为唯一 Source of Truth

Claude ​Code 选择 transcript 作为 Runtime 的唯一 Source of Truth，体现在：

1. **Resume 依赖 transcript**：CLI 崩溃重启后，从 transcript 恢复会话状态
2. **Compact 依赖 transcript**：压缩时基于 transcript 构造摘要 + 状态复灌
3. **Permission 依赖 transcript**：权限决策可能参考历史工具调用（如"上次用户允许了什么"）
4. **Memory 依赖 transcript**：Memory 提取从 transcript 中抽取关键信息
5. **Audit 依赖 transcript**：每次工具调用、权限决策、错误恢复都在 transcript 中留痕

这种"单一 SoT"的设计避免了"多份状态如何同步"的问题。代价是 transcript 越长，写入与读取的成本越高——但通过 JSONL 流式格式和分段加载缓解。

## 4.6 关键设计决策

### 4.6.1 决策 1：多入口共用同一执行内核

**What**：REPL、SDK、MCP Server、Bridge、Subagent 都调用同一个 `query()`。

**Why**：

- 工具行为、权限判定、上下文压缩、记忆注入的一致性
- 避免维护多套实现导致 bug 修复不同步
- 让"SDK 用户"和"REPL 用户"得到一致体验

**Trade-off**：

- `query()` 的参数列表变得复杂（`QueryParams` 包含 10+ 依赖），但依赖注入模式缓解了这个问题
- 不同入口的 transcript 持久化策略不同，需要在外层（如 QueryEngine）处理差异
- 不同入口的 UI 表现不同，需要把 UI 事件抽象成 yield message

### 4.6.2 决策 2：纯函数式 query() 与依赖注入

**What**：`query()` 不直接 import API 客户端、UI、文件系统，而是通过 `QueryParams.deps` 接受所有外部依赖。

**Why**：

- 可测试性：传入 mock callModel 即可单元测试，不需要启动真实 API
- 可复用性：QueryEngine、AgentTool、测试共用同一个 query_loop
- 可组合性：子 Agent 可独立运行自己的 query_loop 实例

**Trade-off**：

- 依赖注入对象的接口很宽，新增依赖时所有调用点都要更新
- 失去了"模块直接 import 函数"的方便性，必须通过 `deps.callModel(...)` 这样的间接调用

### 4.6.3 决策 3：流式期间启动工具

**What**：`StreamingToolExecutor` 在 tool_use block 完整出现时就启动工具，不等整个 assistant 响应结束。

**Why**：

- 延迟降低 30-50%
- 工具与文本输出并行进行，CPU/IO 资源利用率更高
- 用户感知 Agent "在做事" 而不是 "在想"

**Trade-off**：

- 引入 streaming fallback discard 复杂度（reactive compact 时已启动工具要丢弃）
- 引入 abort synthetic result 复杂度
- 并发安全判定必须保守（详见第 6.3 节）

### 4.6.4 决策 4：Fail-Closed 安全默认值

**What**：所有 Tool 默认 `isConcurrencySafe: false`、`isReadOnly: false`、`isDestructive: false`、`checkPermissions: defaultAllow`（但外层权限系统按 mode 决定是否需要确认）。未知工具默认 ASK。

**Why**：

- 未覆盖即视为不安全
- 一个未覆盖 `isConcurrencySafe` 的写工具如果默认并发安全，可能和其它工具并发写同一文件
- 未知 MCP 工具如果默认允许，可能绕过权限策略

**Trade-off**：

- 每个工具开发者必须显式声明这些属性，文档负担增加
- 部分安全工具因为没显式声明被串行执行，浪费并发机会

### 4.6.5 决策 5：Prompt Cache 稳定性作为一等工程约束

**What**：工具池排序、system prompt 段落顺序、cache breakpoint 位置都必须保持稳定，否则 prompt cache key 失效。

**具体表现**：

`tools.ts:354-359` 的注释说明：MCP 工具与内建工具不能 flat sort。如果把 MCP 工具混入内置工具排序，新 MCP 工具按字母序插入可能让后续工具 schema 字节位置变化，prompt cache key 失效。

实际实现：

```typescript
const builtInTools = getTools(permissionContext)
const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext)
return uniqBy(
  [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
  'name'
)
```

三个设计点：内置工具和 MCP 工具先分开处理；两个 partition 各自按 name 排序；concat 后再 `uniqBy('name')`，内置工具在同名冲突时优先。

**Why**：Prompt cache 命中可节省 90% 的成本与 latency。

**Trade-off**：代码结构受 cache key 稳定性约束，部分自然的"按字母排序"做法行不通。

### 4.6.6 决策 6：六阶段循环 + 命名 transition

**What**：query_loop 内部把"为什么继续"显式建模为 `transition.reason`，所有 continuation path 遵循统一模板（构造完整 next state，设置 transition reason，state = next; continue）。

**Why**：

- 状态机迁移可测试
- 状态机迁移可追溯（每条 transition 都有 reason 留在 state 中）
- 阅读代码时可以搜 `transition: { reason:` 快速定位所有迁移边

**Trade-off**：代码冗余度增加（每条 continuation 都要构造完整 next state），但换来了可读性。

## 4.7 模块依赖图

```text
                          ┌─────────────┐
                          │   main.tsx  │
                          │  (Layer 3)  │
                          └──────┬──────┘
                                 │
                ┌────────────────┼────────────────┐
                │                │                │
        ┌───────▼──────┐  ┌─────▼─────┐  ┌──────▼──────┐
        │ replLauncher │  │  setup.ts │  │   getTools  │
        │   REPL.tsx   │  └─────┬─────┘  └──────┬──────┘
        └───────┬──────┘        │               │
                │               │               │
        ┌───────▼──────────────▼───────────────▼──────┐
        │            QueryEngine.ts (Layer 4)         │
        │   持有 mutableMessages, abortController,     │
        │   permissionDenials, totalUsage, ...        │
        └───────────────────┬─────────────────────────┘
                            │ submitMessage()
                            ▼
                  ┌──────────────────┐
                  │   processUserInput│
                  │   合并 text/image/│
                  │   IDE selection/  │
                  │   pasted/slash/   │
                  │   hook context    │
                  └──────────┬───────┘
                             │ messagesFromUserInput
                             ▼
                  ┌──────────────────┐
                  │   query.ts       │   ← 单 turn AsyncGenerator
                  │   (Layer 4)      │
                  │                  │
                  │   while(true):   │
                  │     Phase 1-6    │
                  └─┬──┬──┬──┬──┬───┘
                    │  │  │  │  │
       ┌────────────┘  │  │  │  └────────────────┐
       │               │  │  │                    │
       ▼               ▼  ▼  ▼                    ▼
  ┌──────────┐   ┌──────────┐ ┌──────────┐  ┌──────────┐
  │ Compact  │   │  callModel │ │ runTools │  │ stopHooks│
  │ (Layer 5)│   │ (services/ │ │(services/│  │  Layer 5 │
  └──────────┘   │  api/      │ │  tools/) │  └──────────┘
                 │  claude.ts)│ └─────┬────┘
                 └────────────┘       │
                                      │
                            ┌─────────┴──────────┐
                            │                    │
                            ▼                    ▼
                    ┌───────────────┐   ┌──────────────┐
                    │ Streaming     │   │  toolOrches- │
                    │ ToolExecutor  │   │  tration     │
                    └───────┬───────┘   └──────┬───────┘
                            │                  │
                            └────────┬─────────┘
                                     ▼
                            ┌─────────────────┐
                            │ toolExecution.ts │
                            │ 四层栈：          │
                            │ schema → hooks → │
                            │ permission → call│
                            └─────────┬───────┘
                                      │
                            ┌─────────┴──────────┐
                            │                    │
                            ▼                    ▼
                      ┌──────────┐         ┌──────────┐
                      │ Permission│         │   Tool   │
                      │  Engine   │         │   .call()│
                      └──────────┘         └──────────┘
                                                 │
                                       ┌─────────┴──────────┐
                                       │                    │
                                       ▼                    ▼
                                 ┌──────────┐         ┌──────────┐
                                 │  内建    │         │   MCP    │
                                 │  工具     │         │  工具     │
                                 └──────────┘         └──────────┘
```

## 4.8 关键约束与不变量

整个 Runtime 在所有时刻都必须维护以下不变量：

1. **Transcript 完整性**：每个 tool_use 必须有对应 tool_result（即使是 synthetic error）
2. **API 协议合规**：发给 API 的 messages 不能有连续 user message、孤儿 tool_use/tool_result
3. **权限决策在副作用之前**：`tool.call()` 之前必须完成 permission check
4. **transcript 持久化优先于模型调用**：用户输入必须先写 transcript，再调 API
5. **Trust 前不应用敏感配置**：env var 白名单 + telemetry skeleton + 不读 hooks
6. **流式执行不破坏顺序**：物理提前不能改变最终 transcript 中 tool_use ↔ tool_result 的相对顺序
7. **未知工具默认 ASK**：白名单遗漏不能等于自动允许
8. **Prompt cache 稳定**：影响 prompt 前缀位置的修改必须深思熟虑

这八条不变量是后续每章解析具体机制时反复回归的基本面。

---

## 本章小结

本章给出 Claude ​Code 的整体架构与详细设计。核心要点：

- 整体架构 = Context Compiler + Tool Runtime + Permission Gate + Memory System + UI Layer
- 六层职责架构是设计主线，七层目录架构和六层数据流架构是补充视角
- 数据模型核心是 Message 层级、ContentBlock、ToolUseContext / QueryParams / State 三大上下文容器、ToolResult\<T\>、Tool 能力对象（9 个职责组）
- 控制流：启动六阶段 → query_loop 六阶段循环 → 七种 transition.reason → 六个 transcript 写入点
- Transcript 是 Runtime 的唯一 Source of Truth；内部 transcript 与 API messages 通过 normalizeMessagesForAPI 投影
- 六大关键设计决策：多入口共用内核 / 纯函数 query / 流式工具执行 / Fail-Closed 默认 / Prompt Cache 稳定性 / 命名 transition
- 八条不变量贯穿整个 Runtime

第 5 章将基于这个整体设计，深入解析每个核心模块的实现细节。
