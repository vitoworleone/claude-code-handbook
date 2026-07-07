# 第三集：查询循环 — Query 状态机

同学们好，欢迎来到第三集。

今天我们要聊的这个文件叫 `query.ts`，总共 1765 行代码。毫不夸张地说，这是 **Claude Code 的最核心、最复杂、也是最难理解的模块**。

为什么这么重要？因为它控制着 Claude 怎么思考、怎么调用工具、怎么处理各种边界情况。这个文件的设计直接决定了整个系统的稳定性和可扩展性。

## 函数签名

先来看它的函数签名：

```typescript
async function* query(params: QueryParams, state: QueryState): AsyncGenerator<QueryEvent, TerminalResult>
```

这是一个**异步生成器**，返回类型是 `AsyncGenerator`，后面跟着一个 `TerminalResult` 类型作为返回结果。

为什么用生成器，为什么不用回调、Promise 或者 EventEmitter？别急，我们一会详细对比这几种方案。先记住这个签名——它是我们理解整个系统的起点。

## 两层架构

整个系统分为两层：

**第一层：QueryEngine**

这是面向外部的一个包装类，维护整个会话的生命周期、消息历史、累计的 Token 统计、文件状态缓存。

**第二层：query 函数**

这是真正的核心。单次查询的 API 调用循环、工具执行的逻辑都在这里。第二层每次调用只管自己这一轮的事情，但第一层要管整个会话。

打个比方：

- **QueryEngine** 就像是 HTTP 会话，负责维护会话状态（Cookie、历史记录），生命周期贯穿整个用户会话。
- **query** 就像是单次 HTTP 请求，可能有重试，但逻辑上是一个独立的请求单元。

在 Claude Code 里：
- **TUI 模式**：直接调用 `query`，历史由 TUI 层自己管理。
- **SDK 模式**（被代码调用时）：用 `QueryEngine` 来维护跨次调用的状态。

## 为什么选择异步生成器？

有三种方案可以让调用方实时看到 Claude 的输出：

| 方案 | 写法 | 问题 |
|------|------|------|
| 回调函数 | `query(onText, onToolUse, onComplete)` | 回调地狱，`onComplete` 何时调用语义不清 |
| EventEmitter | `query.on('text', ...).on('end', ...)` | 需要自己监听 `end` 事件，生命周期管理麻烦 |
| **异步生成器** | `for await (const event of query(...))` | 循环结束 = 查询结束，不需要额外信号 |

**异步生成器本质上是一个双向协议：`yield` 用来产出进度，`return` 用来返回最终结果。**

EventEmitter 的问题在于它没有内置的完成语义——你需要自己发射一个 `end` 事件，还得记得在合适的时候停止监听。

生成器就不一样了：`return` 语句天然就表示「这件事结束了，给你最终结果」。这种语义完美匹配我们的场景——等待 API 流式输出、执行工具、再等 API，循环往复。

## 不可变参数 + 可变状态

Query 内部有一个非常重要的设计模式：把参数分成**不可变的**和**可变的**两类。

**Params（不可变）：**
- `tools`、`maxTokens`、`systemPrompt`、`model`
- 这些在整个循环期间都不会变

**State（可变）：**
- `messages`、`toolUseContext`、`turnCount`、`transition`
- 这些在循环的每次迭代中可能会被更新

特别要注意 `transition` 字段——它记录了**为什么继续循环**。

### 为什么做这个区分？

当需要继续循环时，不是直接修改 State，而是用扩展运算符创建一个新的 State 对象：

```typescript
state = { ...state, messages: [...state.messages, newMsg], transition: 'tool_use' };
```

好处：

1. **可读性**：看到 `Params` 你就知道这个值不会变。看到 `State` 你就知道它可能会被修改。
2. **安全性**：避免意外修改参数导致的 Bug。
3. **可调试性**：可以给 State 拍照，保存快照，用来调试甚至重播整个循环。

### Transition 字段的妙用

来看一个具体例子：

```
Round 1: transition = 'tool_use'      → Claude 调用了 Read 工具
Round 2: transition = 'tool_use'      → Claude 调用了 Edit 工具
Round 3: transition = 'max_tokens'    → 输出被 API 截断，触发恢复
Round 4: transition = 'tool_use'      → Claude 调用了 Bash 工具
```

通过 `transition` 链，我们可以完整回溯整个循环的执行路径。在复杂系统里，**「为什么发生了这个」比「发生了什么」重要得多**。

## 七种继续循环的路径

这是今天最核心的内容。Query 循环里有 7 个地方可以触发 `continue`，把循环继续下去：

| # | 路径 | 描述 |
|---|------|------|
| 1 | **ToolUse** | Claude 要调用工具，执行完了继续 |
| 2 | **MaxOutputTokensRecovery** | API 截断了输出，用更小的 max_tokens 重试 |
| 3 | **StopHook** | 用户注册的 Hook 注入了新消息 |
| 4 | **ReactiveCompact** | Token 数超阈值，触发上下文压缩 |
| 5 | **HistorySnip** | SDK 模式下截断历史 |
| 6 | **ContextCollapse** | 语义聚合旧消息 |
| 7 | **AutoBackground** | 任务转后台 |

这七种路径揭示了一个事实：**一次用户请求，可能触发 20+ 次 API 调用，每次原因都不同。**

## 状态机四阶段

每次循环迭代包含四个阶段：

### Phase 1：准备

`normalizeMessagesForApi()` 转换消息格式 + 自动压缩检查。

### Phase 2：调用

`callModel()` 流式调用 API。期间通过 `StreamingToolExecutor` **提前启动工具**——模型还在输出工具调用参数时，工具就已经开始执行了。

### Phase 3：恢复

错误处理与恢复：
- `prompt_too_long` → 触发响应式压缩后重试
- `max_output_tokens` → 追加「请继续」消息，最多重试 3 次
- 429/529 → 指数退避重试

### Phase 4：执行

收集工具执行结果，拼装 `ToolResultBlock`，追加到 messages，继续下一轮。

## 循环的三个 continue 和退出条件

**三个 continue：**

1. Phase 3 错误恢复成功 → 重试本轮（turn_count 不增加）
2. Phase 2 max_tokens 正常截断 → 追加「请继续」消息后继续
3. Phase 4 有工具调用 → 拼回结果后继续下一轮

**三个退出：**

1. Phase 3 不可恢复错误 → `yield ErrorEvent` + `return`
2. Phase 4 后无工具调用（`stop_reason != 'tool_use'`）→ `return`（正常结束）
3. `turn_count >= max_turns` → `yield ErrorEvent`（超限）
