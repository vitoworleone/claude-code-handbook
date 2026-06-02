# 第 6 章 稳定性保障

> 本章从稳定性视角横切第 5 章解析的核心模块。Agent Runtime 的稳定性挑战不是单点的——它来自模型行为不可预测、API 偶发故障、上下文必然增长、工具可能并发冲突、用户输入可能不可信等多个维度的叠加。Claude ​Code 通过一组互补的机制（错误恢复、协议守护、并发隔离、Fail-Closed 默认、Trust Boundary、Coalescing 策略、可观测性、提前持久化）共同维护 Runtime 的稳定。

---

## 6.1 三层错误恢复

### 6.1.1 错误分类

Claude ​Code 把 Runtime 可能遇到的错误分成几类，每类有不同的恢复策略：

| 错误类型 | 典型来源 | 恢复策略 |
|---------|---------|---------|
| 上下文溢出 | API 返回 `prompt_too_long` (413) | 两步：collapse drain → reactive compact |
| 输出截断 | 模型 `stop_reason === 'max_tokens'` | 两步：max_output_tokens_escalate → max_output_tokens_recovery |
| 瞬时 API 故障 | 网络问题、429 rate limit、529 overload | 线性退避重试，不消耗 turn budget |
| 工具执行错误 | 工具 call 抛异常 | 返回 `is_error: true` 的 tool_result，让模型决定下一步 |
| 权限拒绝 | 用户拒绝或规则匹配 | 返回 "Permission denied" tool_result |
| 输入校验失败 | Zod schema 不通过 | 返回 `InputValidationError` tool_result |
| 致命错误 | 模块加载失败、未捕获异常 | 优雅退出，留下 transcript |

这种分类的意义是：**不是所有错误都让 Agent 失败**。多数错误可以通过 Runtime 的恢复机制保持会话继续。

### 6.1.2 上下文溢出恢复

API 返回 `prompt_too_long` (413) 表示当前 messages 超过模型上下文窗口。query_loop 不直接放弃，而是尝试两层恢复：

**第一层：context collapse drain**（`query.ts:1085-1116`）

Context collapse 是 Claude ​Code 的一种压缩策略——把多个相关的工具调用 + 结果折叠成更紧凑的表示（如把 10 次 Read 操作折叠成一个"已读取了以下文件"摘要）。这些折叠是"staged"的，即暂存但尚未真正应用。

当 prompt_too_long 触发时，第一层恢复尝试 drain 这些 staged collapses：

```typescript
if (collapseDrainPossible(state)) {
  const drained = drainStagedCollapses(messages)
  state = {
    ...state,
    messages: drained.messages,
    transition: { reason: 'collapse_drain_retry' }
  }
  continue  // 不增加 turnCount
}
```

这种恢复**不增加 turnCount**——因为这是 Runtime 内部的优化重试，不是新的模型 turn。

**第二层：reactive compact**（`query.ts:1119-1165`）

如果 collapse drain 失败或不可用，尝试 reactive compact：

```typescript
if (!state.hasAttemptedReactiveCompact) {
  const postCompactMessages = await reactiveCompact(messages, ...)
  state = {
    ...state,
    messages: postCompactMessages,
    hasAttemptedReactiveCompact: true,
    transition: { reason: 'reactive_compact_retry' }
  }
  continue
}
```

**每轮限制一次**（通过 `hasAttemptedReactiveCompact` 标记）：避免 compact 自身又溢出导致死循环。如果 reactive compact 后仍然 prompt_too_long，让用户看到错误而不是无限重试。

### 6.1.3 输出截断恢复

模型 `stop_reason === 'max_tokens'` 表示输出因达到 max_output_tokens 上限被截断。query_loop 同样尝试两层恢复：

**第一层：max_output_tokens_escalate**（`query.ts:1185-1220`）

如果当前模型有更高的 max_output_tokens 上限（如 Sonnet 默认 8K，可提升到 16K；Opus extended 模式可到 64K），先尝试提升：

```typescript
if (canEscalateMaxOutputTokens(state)) {
  state = {
    ...state,
    maxOutputTokensOverride: ESCALATED_MAX_TOKENS,
    transition: { reason: 'max_output_tokens_escalate' }
  }
  continue
}
```

下一轮 callModel 会用更高的 max_output_tokens 重试。

**第二层：max_output_tokens_recovery**（`query.ts:1223-1251`）

如果提升上限仍然不够，或已经在最高上限，追加 meta user message 让模型继续：

```typescript
const continuationMessage = createUserMessage({
  content: [{ type: 'text', text: 'Please continue from where you left off.' }],
  isMeta: true
})

state = {
  ...state,
  messages: [...messages, continuationMessage],
  maxOutputTokensRecoveryCount: state.maxOutputTokensRecoveryCount + 1,
  transition: { reason: 'max_output_tokens_recovery' }
}
continue
```

这是把"继续输出"显式建模成下一轮对话的一部分，而不是简单重试同一次请求。

**recoveryCount 限制**：超过 N 次后不再恢复，避免无限继续。

### 6.1.4 瞬时 API 故障恢复

网络错误、429 rate limit、529 overload 等瞬时故障的恢复策略（`query.ts:1258-1264`）：

```typescript
if (isRecoverableError(error)) {
  await linearBackoffSleep(attemptCount)
  // 不增加 turnCount
  // 不跑 stop hooks
  // 仅重试 API 调用
  continue
}
```

**关键约束**：

1. **不消耗 turn budget**：底层只是连接错误，不应该让上层看到 "Max turns reached"
2. **不跑 stop hooks**：避免触发 stop hook 后又因网络问题反复触发，形成 death spiral
3. **线性退避**：避免雷霆退避（thundering herd），但也不让恢复时间过长

### 6.1.5 错误恢复的优先级

```text
prompt_too_long withheld
  → 优先尝试 collapse_drain_retry
  → 失败则 reactive_compact_retry
  → 仍失败则终止

max_output_tokens truncation
  → 优先尝试 max_output_tokens_escalate
  → 失败则 max_output_tokens_recovery
  → 超过 recoveryCount 则终止

瞬时 API 故障
  → 线性退避重试
  → 超过重试上限则向上抛出
```

这种优先级是"先尝试便宜的恢复，再尝试昂贵的"。collapse drain 是纯内存操作，reactive compact 需要一次额外模型调用，max_output_tokens_escalate 只是改参数，recovery 需要追加 message 再 turn。

## 6.2 协议正确性守护

### 6.2.1 不信任 stop_reason

API 文档说 `stop_reason === 'tool_use'` 表示模型需要工具调用，但 Claude ​Code 不信任这个字段。源码（`query.ts:552-557`）的判定逻辑是：**只看 streaming 中是否真的出现过 tool_use block**。

理由是模型 stop reason 并不总是可靠：
- 可能 `stop_reason` 是 `tool_use` 但实际没有发出 tool_use block
- 可能 `stop_reason` 是 `end_turn` 但 content 中确实有 tool_use block
- 长输出被中间 max_tokens 截断时 stop_reason 给出 `max_tokens`，但本意是继续 tool_use

```typescript
const hasToolUse = toolUseBlocks.length > 0
if (hasToolUse) {
  // 进入 Phase 6 执行工具，无论 stop_reason 是什么
} else {
  // 进入 Phase 3 错误恢复或 stop hooks
}
```

这条规则体现了**协议正确性优先于输出漂亮**：宁可代码看起来不那么"信任 API"，也要保证不会因为模型偶然抽风导致 Runtime 行为崩坏。

### 6.2.2 tool_use ↔ tool_result 严格配对

Anthropic API 要求 tool_use 与 tool_result 严格配对：每个 tool_use 必须有对应 tool_use_id 的 tool_result，而且必须在下一个 user message 中。

Claude ​Code 维护这个不变量的方式：

**工具失败时仍然生成 tool_result**：

```typescript
try {
  result = await tool.call(input, ...)
  return mapToolResultToToolResultBlockParam(result, tool_use_id)
} catch (err) {
  return {
    type: 'tool_result',
    tool_use_id,
    content: err.message,
    is_error: true
  }
}
```

**streaming fallback discard 时清理已启动工具**（`query.ts:730-740`、`query.ts:909-919`）：

当 reactive compact 触发，需要重新调用 API 时，已经启动的 streaming tools 必须被 abort 和 discard。否则它们的结果会带着旧的 tool_use_id 出现，与新的 model turn 不配对。

**abort 时生成 synthetic tool_result**（`query.ts:1011-1029`）：

如果用户在工具执行中按 Ctrl+C，每个已发出 tool_use_id 必须生成一个 synthetic tool_result：

```typescript
for (const toolUseId of pendingToolUseIds) {
  syntheticResults.push({
    type: 'tool_result',
    tool_use_id: toolUseId,
    content: 'Aborted by user',
    is_error: true
  })
}
```

如果不生成这些 synthetic results，下一轮如果重新尝试，model 看到的 messages 里会有孤儿 tool_use，API 直接拒绝。

### 6.2.3 normalizeMessagesForAPI 边界

内部 transcript 与 API messages 之间的边界由 `normalizeMessagesForAPI()`（`src/utils/messages.ts:1989-2103`）维护。这个函数不是可有可无的 helper，而是 runtime 必需模块。

主要职责：
- 过滤 progress / display-only / synthetic API errors / 非 local_command 的 system messages
- 合并连续 user messages
- 处理 tool reference / unavailable tools
- 重排 attachments

**为什么必须有这一层**：

内部 transcript 包含 UI 进度、附件、compact boundary、虚拟消息、hook 输出、TombstoneMessage 等结构。这些都是 Runtime 需要记录的内部状态，但模型不能也不应该看到。如果不规范化直接发 API：

- 连续 user message 会让 API 拒绝
- TombstoneMessage 会让模型困惑
- progress message 会污染上下文
- 孤儿 tool_use 会让 API 报错

normalizeMessagesForAPI 把这些内部细节"屏蔽"在 Runtime 层，让 API 看到的永远是合法、干净、协议合规的 messages。

### 6.2.4 6 个 transcript 写入点的约束

第 4.4.5 节列出了 6 个 transcript 写入点。它们共同的约束是：**写入必须保持 transcript 的不变量**。

- 写用户输入时：必须先写再调 API（保证崩溃后能 resume）
- 写 compact 时：必须替换 boundary 之前的内容，不是追加
- 写 max_output_tokens recovery message 时：必须标记 isMeta=true，让 UI 知道这不是用户真实输入
- 写 assistant message 时：必须包含完整 content blocks（text + tool_use）
- 写 tool result 时：必须是 user role 的 tool_result block，必须配对 tool_use_id
- 写 system / hook 注入时：必须用合适的 type（local_command 等），避免直接进 API

## 6.3 并发安全

### 6.3.1 isConcurrencySafe 默认 false

Tool 协议中 `isConcurrencySafe` 默认值是 `false`（`Tool.ts:757-769`），是 Fail-Closed 取向。

**为什么默认 false**：

一个未覆盖 `isConcurrencySafe` 的写工具如果默认并发安全，可能和其它工具并发写同一文件，导致竞态。例如：

```text
Tool A: 写文件 a.txt（默认并发安全 = true）
Tool B: 写文件 a.txt（默认并发安全 = true）
并发执行 → 最终 a.txt 内容是哪个 Tool 的输出？undefined
```

默认 false 让所有未明确声明的工具都串行执行，避免这种竞态。开发者必须显式标注 `isConcurrencySafe: () => true` 才能允许并发。

### 6.3.2 partitionToolCalls 分组策略

`partitionToolCalls()`（`toolOrchestration.ts:91-116`）的分组规则：

```text
连续的 concurrency-safe tools → 一个并发批次
任何 unsafe tool → 自己一个串行批次
```

例如：

```text
工具序列: [Read, Read, Edit, Read, Bash, Read, Glob]
分组: [(Read+Read), (Edit), (Read), (Bash), (Read+Glob)]
       concurrent  serial  serial  serial  concurrent
```

每个并发批次内的工具用 `Promise.all` 执行；批次之间串行。

这种分组策略尽量利用并发机会（连续的 read 工具可以并行），但**绝不允许 unsafe 工具与其它工具并发**。

### 6.3.3 BashTool 的 input-aware 并发安全

BashTool 的并发安全判断是动态的，依赖输入：

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

`checkReadOnlyConstraints` 通过命令模式匹配判断：

- `ls`、`grep`、`cat`、`find` → 只读 → 并发安全
- `echo`、`pwd`、`whoami` → 只读 → 并发安全
- `git status`、`git log` → 只读 → 并发安全
- `npm install` → 修改 → 不安全
- `git checkout` → 修改 → 不安全
- `sed -i` → 修改 → 不安全
- `cd && something` → 改变 cwd → 不安全（compoundCommandHasCd 检测）

这种"输入相关"的并发判定让 Read-only Bash 命令可以并发，但写命令必须串行。

### 6.3.4 contextModifier 串行应用

`ToolResult<T>.contextModifier` 让工具修改后续工具看到的上下文（如 FileEdit 修改 file cache）。在并发场景下，多个 contextModifier 可能冲突。

解决方案（`toolOrchestration.ts:31-63`）：

```text
并发批次执行：
  ├─ 同时启动多个工具
  ├─ 收集每个工具的 contextModifier
  ├─ 等待全部完成
  └─ 按 tool_use_id 排队，串行应用 contextModifier
```

**关键设计点**：并发的是 `tool.call()`，串行的是 `contextModifier`。这样既得到并发的性能收益，又避免了 context 的并发冲突。

### 6.3.5 StreamingToolExecutor 的并发规则

`StreamingToolExecutor.processQueue()`（`StreamingToolExecutor.ts:126-150`）的规则更保守：

```text
- 没有正在执行的工具时 → 可以启动新工具
- 当前工具 concurrency-safe 且所有正在执行的工具都 concurrency-safe → 可以并发
- 否则 → 等待
```

这种规则比批次内的 `Promise.all` 更保守，因为 streaming 模式下工具是逐个到达的，无法预知后续会出现什么工具。如果当前已在执行一个 unsafe Bash，即使后面到了一个 safe Read，也不能并发。

### 6.3.6 Semaphore 上限

`getMaxToolUseConcurrency()` 默认返回 10，限制并发工具数量。这是为了：

- 避免系统资源耗尽（子进程数、文件句柄数）
- 避免对外部服务（如 API、文件系统）的 burst
- 给用户感知留出余地（同时 100 个工具的进度无法在 TUI 中展示）

10 是工程经验值，可通过 settings 调整。

## 6.4 Fail-Closed 默认值

### 6.4.1 Tool 默认值

`buildTool()` 的默认值（`Tool.ts:757-769`）：

```typescript
isEnabled            -> true
isConcurrencySafe    -> false   // 默认不并发
isReadOnly           -> false   // 默认非只读
isDestructive        -> false   // 默认非破坏性
checkPermissions     -> defaultAllow  // 默认允许（由外层权限系统控制）
```

最重要的默认值是 `isConcurrencySafe: false` 和 `isReadOnly: false`：

- 未覆盖 `isConcurrencySafe`：当作不安全，避免错误并发
- 未覆盖 `isReadOnly`：当作写操作，UI 显示更谨慎
- 未覆盖 `isDestructive`：当作非破坏性（但 UI 仍按写操作处理）
- 未覆盖 `checkPermissions`：依赖外层权限系统，不主动允许

`isReadOnly: false` 的默认值也影响 PermissionMode 的判定——一个未覆盖的工具不会被错误归类为 READ_ONLY_TOOLS，因此在 DEFAULT 模式下仍然需要确认。

### 6.4.2 未知工具默认 ASK

如果模型调用了一个 Runtime 中不存在的工具（如 MCP server 离线导致工具消失），权限决策默认 ASK 而非 ALLOW。

```typescript
function defaultPermissionDecision(tool, input, ctx) {
  if (!tool) return PermissionDecision.Ask  // 未知工具
  return tool.checkPermissions(input, ctx)
}
```

这避免了"白名单遗漏 = 自动允许"的安全漏洞。

### 6.4.3 非交互 fail-fast

Headless / SDK / CI 环境没有 UI 弹窗，权限请求无法等待用户响应：

```typescript
async function nonInteractiveCanUseTool(tool, input, ctx) {
  const decision = await tool.checkPermissions(input, ctx)
  if (decision === PermissionDecision.Ask) {
    // 非交互环境下，Ask = Deny
    return {
      allowed: false,
      error: 'Permission required but no UI available'
    }
  }
  return decision
}
```

这种"非交互 = fail-fast"避免了在 CI 中静默通过未授权操作。

### 6.4.4 MCP 工具的特殊处理

MCP 工具的 `checkPermissions` 默认 passthrough（不主动决策），依赖外层。但外层规则会显式处理：

- 未在 allow 列表的 MCP 工具 → Ask
- 在 deny 列表的 MCP 工具 → Deny

新接入的 MCP server 第一次调用工具必然需要用户确认，符合 Fail-Closed 原则。

## 6.5 Trust Boundary

### 6.5.1 为什么需要 Trust Boundary

Claude ​Code 会读取用户工作目录中的：

- `CLAUDE.md` 项目级 prompt
- `.claude/skills/` 自定义 skills
- `.claude/hooks/` 自定义 hooks
- `.claude/settings.local.json` 本地配置
- `.env` 环境变量

这些文件可能包含恶意指令——例如一个被篡改的 `CLAUDE.md` 可能通过 `@include` 引入外部攻击脚本；一个恶意 hook 可能在 PreToolUse 时执行任意命令。

如果信任对话框太晚弹出，以下操作可能已经发生：

- 读取并执行了 `.claude/hooks/` 中的 PreToolUse hooks
- 通过 `@include` 加载了外部恶意文件
- 把敏感文件路径发送到了 telemetry
- 把环境变量（可能包含 secrets）注入到了 system prompt

### 6.5.2 初始化分阶段：init.ts vs setup.ts

Claude ​Code 把初始化拆为两个阶段，由 Trust Dialog 分割：

**init.ts（trust 前）** — 只做安全操作：

```typescript
export async function init(argv) {
  applySafeEnvironmentVariables()    // 只应用白名单内的安全 env var
  initializeCertificates()           // 证书与 HTTPS 代理
  initializeHttpAgent()              // HTTP agent 配置
  initTelemetrySkeleton()            // 注册 telemetry sink，但不发送事件
}
```

**Trust Dialog**：弹出确认 "Do you trust the files in this directory?"

**setup.ts（trust 后）** — 启用所有功能：

```typescript
export async function initializeTelemetryAfterTrust() {
  applyFullEnvironmentVariables()    // 应用全部 env var
  attachAnalyticsSink()              // 开始处理 telemetry 事件队列
}

export async function setup(argv, permissionContext) {
  setCwd(resolvedWorkingDir)
  startHooksWatcher()                // 监听 hooks 配置变化
  initWorktreeSnapshot()
  initSessionMemory()
  startTeamMemoryWatcher()
}
```

### 6.5.3 trust 前后的环境变量差异

| 阶段 | 策略 | 原因 |
|------|------|------|
| trust 前 | `applySafeEnvironmentVariables()` 只应用白名单 | 排除 `CLAUDE_API_KEY` 等敏感变量，防止 `.env` 中的恶意值被加载 |
| trust 后 | `applyFullEnvironmentVariables()` 应用全部 | 信任已建立，可以加载所有变量 |

这是防止"恶意 `.claude/env` 文件在用户确认信任前就被加载"的深度防御设计。

### 6.5.4 Telemetry 的延迟启动

`initTelemetrySkeleton()` 在 trust 前只注册事件 sink 的骨架结构，但不发送任何事件。`attachAnalyticsSink()` 在 trust 后才真正开始处理 telemetry 事件队列。

这意味着：
- 用户在 Trust Dialog 选择"不信任"并退出时，整个会话期间没有任何 telemetry 事件被发送
- 敏感文件路径、代码片段不会被发送到分析系统
- 但性能打点（startup checkpoint）仍然被记录，因为它们不携带敏感内容

## 6.6 安全防护

### 6.6.1 Windows PATH hijacking 防护

`main.tsx:588-591` 在 main() 函数的第一行实际逻辑：

```typescript
if (process.platform === 'win32') {
  process.env.NoDefaultCurrentDirectoryInExePath = '1'
}
```

这消除了 Windows 的一个安全漏洞：默认情况下，当前目录优先于 PATH 中的同名可执行文件。如果用户在某个项目目录下执行 `claude`，而项目目录中有个恶意的 `node.exe`，Claude ​Code 在 spawn 子进程时可能错误地用这个恶意 `node.exe` 而非系统的 `node.exe`。

设置 `NoDefaultCurrentDirectoryInExePath=1` 强制 Windows 不再优先查找当前目录。

### 6.6.2 sandbox / worktree 隔离

对于高风险操作（如执行未知 shell 命令），Claude ​Code 支持：

- **worktree 隔离**：用 git worktree 创建临时工作目录，让 Agent 在其中操作，避免污染主工作目录
- **tmux 隔离**：teammate 在独立 tmux 窗格中运行，避免与主会话冲突

这些隔离机制由 settings 控制，不是默认开启。但提供这些选项让用户可以根据信任程度选择隔离级别。

### 6.6.3 命令注入与 OWASP Top 10

System prompt 的 `doing_tasks` 第 7 条规则明确：

> Be careful not to introduce security vulnerabilities such as command injection, XSS, SQL injection, and other OWASP top 10 vulnerabilities. If you notice that you wrote insecure code, immediately fix it.

这条 prompt 不仅是告诉模型"写安全代码"，更是在所有工具调用中提醒 Runtime 与模型：

- BashTool 的 input 必须 escape，避免命令注入
- 写文件时不要混入未 escape 的用户输入
- Web fetch 的 URL 必须验证

### 6.6.4 API key 与 OAuth 通过 Keychain

Anthropic API key / OAuth token 通过系统 Keychain 存储：

- macOS：`security` 命令访问 Keychain
- Windows：通过 Credential Manager API
- Linux：通过 libsecret / GNOME Keyring

`keychainPrefetch.ts` 在启动时并行预取这些 token，避免后续 sync spawn 的延迟。但更重要的安全意义是：**API key 不存储在 plain text 文件中**，避免被普通文件读取或意外提交到 git。

### 6.6.5 settings 文件的多级优先级

settings.json 的优先级（高到低）：

```text
MDM Enterprise > Global User > Project > Local (.gitignore'd)
```

MDM 配置由企业 IT 推送，可以强制锁定某些 settings（如禁用某些工具、强制使用代理）。Local settings 可以包含用户私有配置但不会进入 git。

这种多级体系让企业可以集中管理而不破坏用户的个性化配置。

## 6.7 故障恢复与可观测性

### 6.7.1 提前写 transcript 保证 resume 一致性

关键设计：**用户输入一旦被接受，就必须先写入 transcript，再进入模型调用**（`QueryEngine.ts:436-463`）。

```typescript
async submitMessage(userInput) {
  const messages = processUserInput(userInput, ...)
  this.mutableMessages.push(...messages)

  // 关键：先写 transcript
  await sessionStorage.write(this.sessionId, this.mutableMessages)

  // 再调用 query()
  const generator = query(this.mutableMessages, ...)
  for await (const event of generator) {
    // ...
  }
}
```

否则进程在 API 返回前崩溃时，系统无法判断这次输入是否已经进入会话。提前写 transcript 让 resume、故障恢复和审计都有唯一事实来源。

### 6.7.2 transcript validation 与修复

Resume 时的修复流程：

```text
读 ~/.claude/projects/{hash}/{session-id}.jsonl
  ├─ 逐行 JSON.parse，跳过最后不完整的行
  ├─ validate_transcript()
  │    ├─ 检查 tool_use ↔ tool_result 配对
  │    ├─ 缺失 tool_result 时补 synthetic error
  │    ├─ 多余 tool_result 时丢弃
  │    └─ 合并连续 user messages
  └─ 加载 task snapshots（后台任务状态）
```

这种"宽容加载"让异常退出后的会话仍能继续，而不是因为协议错误直接拒绝。

### 6.7.3 startupProfiler 与 OTel span

可观测性体系（`src/utils/startupProfiler.ts`）：

**Phase 定义**：

```typescript
const PHASES = {
  import_time:  ['cli_entry',              'main_tsx_imports_loaded'],
  init_time:    ['init_function_start',    'init_function_end'],
  settings_time:['eagerLoadSettings_start','eagerLoadSettings_end'],
  total_time:   ['cli_entry',              'main_after_run']
}
```

**Checkpoint 打点**：

```typescript
function profileCheckpoint(name: string): void {
  performance.mark(name)
  if (DETAILED_PROFILING) {
    const memory = process.memoryUsage()
    detailedMarks.push({ name, time: performance.now(), memory })
  }
}
```

**环境变量控制**：

| 环境变量 | 行为 |
|---------|------|
| `CLAUDE_CODE_PROFILE_STARTUP=1` | 启用详细启动性能分析 |
| 无 | 按 Statsig sampling 决定是否采样 |

**OTel span 覆盖**：

- `user_prompt` interaction span（用户输入处理）
- `query_turn` span（每一轮 model + tool）
- `tool_call` span（每个工具调用）
- `permission_decision` span（每个权限决策）

这些 span 可以发到 OTel collector 做生产环境的可观测性分析。

### 6.7.4 permissionDenials 与 totalUsage

QueryEngine 跨 turn 累积：

```typescript
class QueryEngine {
  permissionDenials: PermissionDenial[] = []
  totalUsage: Usage = { input: 0, output: 0, cache_creation: 0, cache_read: 0 }
}
```

每次工具调用被拒绝时，添加一条记录到 `permissionDenials`，包含：

- 工具名
- 输入参数
- 拒绝原因
- 时间戳

每次 API 调用后，把 usage 累加到 `totalUsage`。

这些数据在会话结束时输出到 SDK result，让调用方可以：
- 审计哪些权限被拒绝（用于优化 allow 规则）
- 计算 token 成本
- 优化 prompt 减少 token

## 6.8 Coalescing vs Debounce

### 6.8.1 Debounce 的缺陷

Debounce 是"事件流频繁触发时只处理最后一次"的经典策略。但在 Agent Runtime 中它有一个致命缺陷：

```text
事件: a -> b -> c -> d
Debounce wait 500ms:
  - 收到 a：启动定时器
  - 500ms 内收到 b：重置定时器
  - 500ms 内收到 c：重置定时器
  - 500ms 内收到 d：重置定时器
  - 500ms 后：处理 d
```

但如果事件流持续到达（如 Memory 提取需要扫描的 messages 不停增长），Debounce 永远不会触发——直到事件停止 500ms。

更糟的是，如果某个事件触发后，处理逻辑还没完成又有新事件到达：

```text
- 收到 a：开始处理 a（耗时 2s）
- 处理 a 期间收到 b、c、d
- a 处理完成
- 后续 b/c/d 怎么办？
```

Debounce 没有定义这种情况的语义。常见实现要么丢失 b/c/d，要么重复处理。

### 6.8.2 Coalescing 策略

Coalescing（合并）策略保证最终状态一定被处理：

```python
class CoalescingExtractor:
    def __init__(self):
        self.dirty = False
        self.running = False

    def trigger(self):
        self.dirty = True
        if not self.running:
            asyncio.create_task(self.run())

    async def run(self):
        self.running = True
        while self.dirty:
            self.dirty = False
            await self.extract()  # 处理最新状态
        self.running = False
```

特点：
- **保证最终状态被处理**：dirty 标记确保循环到来时一定处理最新状态
- **避免并发执行**：running 标记防止多个 extract 并发
- **不丢失事件**：每次 trigger 都至少触发一次 extract
- **不重复处理**：连续的 trigger 会被合并为一次 extract

### 6.8.3 在 Memory 提取中的应用

Memory 提取场景下：

```text
事件流：
- 用户输入 "用 bcrypt 哈希密码"
- 模型生成 tool_use 调用 FileRead
- 工具返回结果
- 模型生成 tool_use 调用 FileEdit
- 工具返回结果
- 模型生成 assistant message "已完成"

每个事件都可能触发 Memory 提取。
```

Debounce 会等"事件停止 500ms"，但用户可能立刻继续提问，导致永远不提取。
Coalescing 会在每次事件后保证至少一次 extract，且不会与前一次 extract 并发。

### 6.8.4 在 File Watcher 中的应用

`startHooksWatcher()` 监听 settings.json 变化。如果用户在 1 秒内修改 5 次（如自动保存），Debounce 会丢失中间状态。Coalescing 会确保每次修改都被处理。

## 6.9 abort 与中断协议

### 6.9.1 AbortController 的全链路传递

Claude ​Code 通过 `AbortController` 实现可中断：

```text
QueryEngine.abortController
  ↓
query(..., { abortController })
  ↓
deps.callModel({ signal: abortController.signal })
  ↓
fetch(url, { signal })   // SDK 内部
```

用户按 Ctrl+C 时：
1. UI 拦截 SIGINT
2. 调用 `QueryEngine.abortController.abort()`
3. 信号沿链路传到 fetch，fetch 取消 HTTP 请求
4. 信号传到 streaming tool executor，调用每个 tool 的 abort 逻辑
5. 信号传到 hooks，让 hook 子进程也被 kill

### 6.9.2 工具 abort 的协议要求

工具被 abort 时必须：

1. 立即停止副作用（kill 子进程、关闭文件句柄、cancel 网络请求）
2. **生成 synthetic tool_result**（不能丢弃）
3. tool_result 标记 `is_error: true`，content 描述为"Aborted by user"

否则下一次重试时 model 看到的 messages 里会有孤儿 tool_use，API 直接拒绝。

### 6.9.3 abort 与 stop hooks 的关系

abort 不应该触发 stop hooks。理由：

- abort 是用户主动中断，不是 agent 自然结束
- stop hooks 设计为"agent 完成 turn 时的钩子"
- 在 abort 路径上触发 stop hooks 可能导致 hook 自身被 abort，状态混乱

```typescript
if (state.transition.reason === 'aborted') {
  // 不跑 stop hooks
  return terminal
}
```

## 6.10 死循环防护

### 6.10.1 maxTurns 限制

每个 query() 调用有 `maxTurns` 参数，默认 100。超过此数则强制终止，返回 "Max turns reached"。

这防止：
- 模型陷入无限自我对话（"我需要再读一次 → 再调用 → 又得到结果 → 再读一次..."）
- 工具失败 → 模型重试 → 又失败 → 再重试...
- 错误恢复循环（如 max_output_tokens recovery 失败后又 recover）

### 6.10.2 hasAttemptedReactiveCompact 标记

reactive compact 在每个 query 调用中限制一次（通过 `hasAttemptedReactiveCompact` 标记）。如果 compact 后仍然 prompt_too_long，不再 compact，直接报错。

这防止：
- compact 自身又溢出，无限 compact

### 6.10.3 stopHookActive 标记

stop hooks 触发后，如果 hook 返回 blocking errors 让 agent 继续一轮，新一轮如果又结束又触发 stop hooks——可能无限循环。

`stopHookActive` 标记防止 stop hook 在自身触发的 turn 内再次触发：

```typescript
if (state.stopHookActive) {
  // 跳过 stop hooks
  return terminal
}
```

### 6.10.4 MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES

autoCompact 连续失败次数限制：

```typescript
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```

超过 3 次连续 autocompact 失败后，不再尝试 autocompact，避免每轮都浪费一次失败的 compact 调用。

## 6.11 资源泄漏防护

### 6.11.1 子进程清理

Tool / Hook 启动的子进程必须在以下情况被清理：

- tool / hook 正常完成
- tool / hook 被 abort
- query() 退出
- 进程退出（SIGTERM / SIGINT）

`StreamingToolExecutor` 维护一个 subprocess 注册表，进程退出时统一 kill：

```typescript
process.on('SIGTERM', () => {
  for (const sub of activeSubprocesses) {
    sub.kill('SIGTERM')
  }
})
```

### 6.11.2 文件句柄

打开的文件、socket、watcher 必须显式关闭。Node.js 的 GC 不保证及时回收，长会话可能耗尽 fd 上限。

Claude ​Code 的策略：
- 文件读取用 `fs.promises.readFile`（一次性读取，自动关闭）
- watcher 在不需要时显式 close（如离开 session 时关闭 hooks watcher）
- 长连接（MCP HTTP / WS）有 lifecycle 管理

### 6.11.3 Promise 泄漏

未 await 的 Promise 可能导致 unhandled rejection。Claude ​Code 的做法：

- 所有 fire-and-forget 操作显式 `.catch(() => {})` 或 `.catch(err => log(err))`
- `loadRemoteManagedSettings().catch(() => {})` 等模式贯穿启动代码

例如：

```typescript
loadRemoteManagedSettings().catch(() => {})  // 非阻塞 + 不报错
syncSettings().catch(() => {})               // 后台 sync + 不阻塞
```

## 6.12 关键设计权衡

| 决策 | What | Why | Trade-off |
|------|------|-----|-----------|
| 三层错误恢复 | 上下文 / 输出 / API 各自有恢复策略 | 让 Runtime 在各种故障下保持会话 | 状态机分支多，代码复杂 |
| 不信 stop_reason | 以实际 tool_use block 为准 | 模型行为不可靠 | 代码看似"不信任 API" |
| Fail-Closed 默认 | 工具默认不安全 | 安全优先 | 工具开发者负担更大 |
| Trust Boundary | 初始化两阶段 | 防止 config 攻击面 | 初始化复杂度增加 |
| Coalescing 提取 | 替代 Debounce | 保证最终状态一定被处理 | 偶尔比 Debounce 多一次执行 |
| 提前写 transcript | 接受输入即写 | 保证 resume 一致性 | 增加每次输入的 IO |
| maxTurns 强制终止 | 默认 100 turn 上限 | 防死循环 | 偶尔合法长任务被截断 |

---

## 本章小结

本章从稳定性视角横切了 Claude ​Code 的核心模块，覆盖错误恢复、协议守护、并发安全、Fail-Closed 默认、Trust Boundary、安全防护、可观测性、Coalescing 策略、abort 协议、死循环防护、资源泄漏防护等多个维度。

核心观察：

- 稳定性不是单点机制，而是一组互补机制共同维护
- "协议正确性优先于输出漂亮"在错误恢复、配对约束、normalize 边界中反复体现
- "Fail-Closed 默认"贯穿工具、权限、未知工具、非交互场景
- "Trust Boundary"把"配置文件本身可能是攻击面"作为安全模型的基本假设
- "Coalescing 优于 Debounce" 是 Agent Runtime 在事件处理上的关键判断
- "提前写 transcript" 是故障恢复的根基

第 7 章将从部署视角横切，看 Claude ​Code 如何把这些机制打包成可发布、可分发、可跨平台运行的产品。
