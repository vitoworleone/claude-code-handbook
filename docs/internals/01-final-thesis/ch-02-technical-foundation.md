# 第 2 章 技术基础

> 本章梳理 Claude ​Code 所依托的关键技术基础。理解这些基础不是为了背技术栈表格，而是为了理解 Claude ​Code 的工程决策——它的状态机为什么这样设计、它的 UI 为什么是 React + Ink、它的工具协议为什么这样组织——很多答案都藏在它所依赖的技术形态中。

---

## 2.1 编程 Agent 的运行时范式

### 2.1.1 从 ReAct 到 Tool Use

学术界对 LLM Agent 的范式化研究始于 2022 年 Princeton 团队提出的 **ReAct**（Reasoning + Acting）框架。ReAct 的核心思想是：让模型在每一步交替输出 *Thought*（推理）、*Action*（行动）、*Observation*（观察结果），形成一个**思考—行动—反馈**的闭环。这个范式后来被 LangChain、AutoGPT 等早期 Agent 框架广泛采用。

ReAct 的局限在于它依赖文本模式匹配——Agent loop 的实现者需要从模型的输出文本中正则提取 `Action:` 和 `Action Input:`，再用这些字符串去查找工具、构造调用参数。这种方式有几个问题：

1. 模型输出格式不稳定，正则解析常常失败
2. 工具参数是字符串，无类型保证，复杂参数无法表达
3. 多轮调用时的 token 浪费严重（Thought / Action / Observation 都是纯文本）
4. 工具结果回流给模型时只能用约定字符串拼接，难以表达图像、文件、错误等结构化内容

**Tool Use 协议**是对 ReAct 的工程化改进。OpenAI 在 2023 年 6 月推出 Function Calling 时首次提出这种思路：让模型 API 原生支持工具声明与工具调用，模型输出的工具调用不是文本而是结构化的 JSON 对象；工具结果也作为结构化消息回传。Anthropic 在 2024 年 3 月发布的 Claude 3 系列同步推出了功能等价的 Tool Use 协议，采用 `tool_use` 与 `tool_result` 块代替函数调用对象。Google Gemini 在 Function Declaration 中采用了类似设计。

Tool Use 协议带来的工程化好处：

- 模型输出可结构化解析，不依赖正则
- 工具参数有 JSON Schema 描述，可类型校验
- 工具结果支持多模态（text / image / file）
- 多个工具调用可以一次输出（parallel tool use）

Claude ​Code 完全建立在 Anthropic Tool Use 协议之上，几乎所有运行时机制（query_loop、StreamingToolExecutor、Compact、Permission）都围绕这个协议设计。理解 Tool Use 协议是理解 Claude ​Code 的前提。

### 2.1.2 Agent Loop 的最简形式

无论采用 ReAct 还是 Tool Use 协议，Agent Loop 的本质都可以简化为以下伪代码：

```text
while not done:
    response = model.invoke(messages)
    messages.append(response)
    if response.has_tool_calls:
        for tool_call in response.tool_calls:
            result = execute(tool_call)
            messages.append({"role": "tool", "content": result})
    else:
        done = True
return response
```

这个最简形式在原型阶段足够用，但在生产环境下立刻显出多个不足：

- 模型调用是流式的，怎么把流式响应集成进 loop？
- 工具执行可能失败，怎么处理？
- messages 越来越长，怎么压缩？
- 用户可能想中断，怎么打断 loop？
- 多个工具调用可以并发，怎么调度？
- 模型可能输出 `stop_reason='tool_use'` 但实际没有 tool_use block，怎么办？

Claude ​Code 的 `query_loop`（详见第 5.2 节）就是对这个最简形式的全面工程化扩展。它通过引入 `transition.reason` 把循环转化为带命名状态迁移边的状态机，通过 `QueryParams` / `State` 拆分把不可变参数和可变状态分离，通过 `StreamingToolExecutor` 把工具执行物理提前，通过 `normalizeMessagesForAPI` 维护内部 transcript 与 API 协议之间的边界。这些扩展不是炫技，每一项都解决一个真实问题。

### 2.1.3 流式响应与 Tool Use 的耦合

现代 LLM API 普遍支持流式响应（streaming response）。流式响应不是一次性返回完整结果，而是分片返回 token / content block delta，让 UI 可以边接收边渲染，降低用户的感知延迟。

流式响应与 Tool Use 协议结合时引入了一个微妙的工程问题：**工具调用什么时候可以开始执行？**

朴素答案是：等整个响应结束后，从最终 message 中提取 `tool_use` 块，再去执行工具。但这种做法浪费了流式带来的延迟优势——如果模型说了 1000 字然后在最后一个 block 里调用一个 30 秒的 Bash 命令，用户在看完 1000 字后还要再等 30 秒。

更激进的做法是：在流式过程中，一旦某个 `content_block_stop` 事件标记 `tool_use` 块已经完整出现，就立即启动工具执行。Claude ​Code 采用了这种激进做法（`StreamingToolExecutor`），但带来了若干复杂问题：

- 如果流到一半模型出错（如 `prompt_too_long`）需要重试，已经启动的工具该怎么办？
- 多个工具按顺序流出，能否并发执行？
- 如果用户在工具执行中按 Ctrl+C 中断，怎么把已经启动的工具优雅关掉？
- 工具执行结果是否要按 tool_use 在 stream 中的顺序返回给模型？

第 5.3 节将详细解析 Claude ​Code 对这些问题的解法。本节只指出：**流式与 Tool Use 的耦合不是 API 层面的问题，而是 Runtime 工程层面的问题**——API 协议允许你激进，但激进的代价由 Runtime 工程师承担。

## 2.2 Anthropic Tool Use 协议

Claude ​Code 完全建立在 Anthropic Messages API 之上。理解这套 API 的协议规则，就理解了 Claude ​Code 必须遵守的"游戏规则"。

### 2.2.1 Messages API 的角色与块结构

Anthropic Messages API 把对话表达为一个 `messages` 数组，每条 message 有 `role` 与 `content`。`role` 只有两种合法值：`user` 与 `assistant`。`content` 不是简单字符串，而是一个 **content block** 数组，每个块有 `type` 字段标识其类型：

| Block Type | 出现在 | 含义 |
|------------|--------|------|
| `text` | user / assistant | 纯文本内容 |
| `image` | user | 图像（支持 url 或 base64） |
| `document` | user | PDF 文档 |
| `tool_use` | assistant | 模型决定调用某个工具 |
| `tool_result` | user | 工具执行结果 |
| `thinking` | assistant | 模型的思考过程（extended thinking 模式） |
| `redacted_thinking` | assistant | 经安全过滤的思考 |

这套块结构是 Tool Use 协议的基础。**模型只能输出 `assistant` role，工具结果只能放在 `user` role 的 `tool_result` 块中**——这是协议的硬性要求，违反就会被 API 拒绝。

Claude ​Code 的内部 transcript 还包含若干**协议之外**的消息类型，例如：

- `SystemMessage`（local_command）：本地命令输出（如 `/help` 的结果）
- `TombstoneMessage`：被压缩或删除的消息占位
- `ToolUseSummaryMessage`：工具调用的摘要
- Progress / display-only virtual message：UI 进度提示
- Synthetic API error：内部模拟的错误以供下游处理

这些消息在内部 transcript 中存在以支持 UI 渲染、resume 与审计，但**绝对不能直接发到 API**。Claude ​Code 通过 `normalizeMessagesForAPI()` 在每次调用模型前把内部 transcript 投影成 API 兼容的 messages（详见第 4.5 节）。这种"内部 messages ≠ API messages"的边界是 Claude ​Code 的核心架构约束。

### 2.2.2 tool_use 与 tool_result 块的配对约束

Tool Use 协议要求 `tool_use` 与 `tool_result` 严格配对：

- 每个 assistant message 中的 `tool_use` 块必须有一个唯一的 `id`
- 下一个 user message 必须包含一个对应 `tool_use_id` 的 `tool_result` 块
- 一个 assistant message 中可以有多个 `tool_use` 块（并行调用），对应的 user message 也必须有多个 `tool_result` 块

如果违反这个配对约束（例如 `tool_use` 没有对应 `tool_result`，或者 `tool_result` 找不到对应 `tool_use`），API 会返回错误。这个约束看似简单，但在工程实现上要求 Runtime 做到：

1. 工具执行失败时仍然必须生成 `tool_result`（用 `is_error: true` 标记），不能直接丢弃
2. 流式调用中途中断（如 `prompt_too_long` 触发 reactive compact）时，已开始的工具调用结果不能渗漏到新尝试
3. 用户 abort 时必须为每个已发出 `tool_use_id` 生成 synthetic `tool_result`，否则下一次重试会因孤儿 `tool_use` 失败

Claude ​Code 源码中大量代码（例如 `query.ts:730-740`、`query.ts:909-919`、`query.ts:1011-1029`）都在维护这个配对约束。第 6.2 节会专门讨论这个约束如何影响错误恢复的实现。

### 2.2.3 流式事件类型

Anthropic Messages API 的流式响应通过 Server-Sent Events（SSE）传输，事件类型包括：

| 事件 | 含义 |
|------|------|
| `message_start` | 开始一条 assistant message |
| `content_block_start` | 开始一个 content block（type 已知） |
| `content_block_delta` | block 增量内容（如 text 的 delta，tool_use 的 input_json_delta） |
| `content_block_stop` | 当前 block 结束 |
| `message_delta` | message 级别的 delta（如 stop_reason、usage） |
| `message_stop` | 整条 message 结束 |
| `ping` | 保活事件 |
| `error` | 错误事件 |

Claude ​Code 在 `services/api/claude.ts` 中通过 `for await` 消费这些事件并归并成更高层的语义事件（如 "tool_use block ready"、"text delta"、"thinking delta"），再向上层 UI / SDK yield。这层归并是 `StreamingToolExecutor` 能够工作的前提——它需要在 `content_block_stop` 标记 `tool_use` 完成时立即触发执行。

### 2.2.4 stop_reason 的不可靠性

API 文档定义 `stop_reason` 有以下合法值：

| stop_reason | 含义 |
|-------------|------|
| `end_turn` | 模型自然结束 |
| `max_tokens` | 达到输出上限 |
| `tool_use` | 模型希望调用工具 |
| `stop_sequence` | 命中显式 stop 序列 |
| `pause_turn` | 长任务暂停（extended thinking） |
| `refusal` | 模型拒绝回答 |

直觉上，Runtime 可以根据 `stop_reason === 'tool_use'` 决定是否继续 loop。但 Claude ​Code 源码（`query.ts:552-557`）明确写道：**不信任 `stop_reason === 'tool_use'`**。理由是模型 stop reason 并不总是可靠——可能出现以下情况：

- `stop_reason` 是 `tool_use` 但实际没有发出 tool_use block（模型抽风）
- `stop_reason` 是 `end_turn` 但 content 中确实有 tool_use block（模型 stop 判定与 content 生成不一致）
- 长输出被中间 max_tokens 截断时，stop_reason 给出 `max_tokens`，但本意是继续 tool_use

Claude ​Code 的实际判定逻辑是：**只看 streaming 中是否真的出现过 tool_use block**。如果出现过，无论 stop_reason 是什么，都进入下一轮 Phase 6（执行工具）；如果没出现过，无论 stop_reason 是什么，都尝试 Phase 3 的错误恢复或进入 stop hooks。

这个细节体现了 Claude ​Code 的核心设计哲学：**协议正确性优先于输出漂亮**。宁可代码看起来不那么"信任 API"，也要保证不会因为模型偶然抽风导致 Runtime 行为崩坏。

## 2.3 Model Context Protocol（MCP）

### 2.3.1 MCP 的设计目标

Model Context Protocol 是 Anthropic 于 2024 年 11 月开源的协议，目标是为 AI 应用提供**标准化的外部能力接入接口**。Anthropic 把 MCP 类比为 "USB-C of AI applications"——任何符合 MCP 规范的服务器都可以作为工具/数据源被任何符合 MCP 规范的 AI 客户端使用，无需为每个组合定制集成。

MCP 解决的问题是：在大模型应用中，外部工具与数据源的接入长期是"M×N 问题"——M 个应用各自实现到 N 个外部系统的连接器，复杂度 O(M·N)。MCP 把这个问题转化为"M+N 问题"：每个应用实现一次 MCP Client，每个外部系统实现一次 MCP Server，复杂度 O(M+N)。

到 2026 年初，MCP 已经形成相对成熟的生态：GitHub、Postgres、Sentry、Stripe、Cloudflare、Notion 等都提供官方 MCP Server；Claude ​Code、Cline、Cursor、Continue 等都内置 MCP Client。

### 2.3.2 MCP 的角色与组件

MCP 协议定义三类角色：

| 角色 | 说明 |
|------|------|
| Host | 终端应用，如 Claude ​Code、Cursor |
| Client | Host 内部的协议实现，负责与 Server 通信 |
| Server | 外部能力提供方，暴露 tools / resources / prompts |

Server 暴露三类能力：

| 能力 | 说明 |
|------|------|
| Tools | 可被模型调用的函数（类似 Function Calling） |
| Resources | 静态或动态资源（如数据库行、文件内容） |
| Prompts | 预制 prompt 模板 |

### 2.3.3 传输层

MCP 协议不绑定特定传输层，规范定义了多种支持：

| 传输 | 用途 |
|------|------|
| stdio | 本地子进程通过 stdin/stdout 通信，零网络依赖 |
| http+sse | HTTP POST 请求 + Server-Sent Events 推送 |
| streamable-http | HTTP/2 长连接双向流 |
| ws | WebSocket |
| sse-ide / ws-ide | IDE 集成专用变体 |

Claude ​Code 在 `services/mcp/client.ts` 中实现了对所有这些传输的支持（详见第 5.9 节）。其中 stdio 是最常用的传输——大量本地 MCP Server（如官方的 filesystem、github 等）都通过 stdio 提供。

### 2.3.4 Claude ​Code 的双向角色

Claude ​Code 在 MCP 协议中**同时承担 Client 和 Server 两种角色**：

- 作为 Client：通过 `services/mcp/client.ts` 连接外部 MCP Server，把 Server 提供的 tools 融合进内部工具池
- 作为 Server：通过 `entrypoints/mcp.ts` 把自己的内置工具（FileEdit、FileRead、Bash 等）封装成 MCP tools，对外暴露

这种双向角色让 Claude ​Code 既能消费外部能力（如调用 GitHub MCP Server 创建 issue），也能被其他 AI 应用调用（如 Cursor 把 Claude ​Code 当 MCP Server 调用其 BashTool）。这两个角色共用同一个工具实现，不存在重复代码——内置工具通过 `wrapToolAsMcpHandler` 被重新包装，但底层仍然调用相同的 `tool.call()`。

## 2.4 终端 UI 技术栈

### 2.4.1 React 19 + Ink：组件化终端 UI

Claude ​Code 的 TUI 使用 **React 19** 配合 **Ink**（一个把 React 渲染目标改为终端文本的库）。这个选择在终端 CLI 工具中并不常见——大多数 CLI（包括 Aider、Codex CLI）使用更原生的终端控制方案（如 prompt-toolkit、blessed、rich）。

选择 React + Ink 的工程动机：

1. **组件化复用**：权限弹窗、流式 Markdown 渲染、命令补全、状态栏等复杂 UI 元素可以以组件方式独立开发与测试
2. **声明式状态驱动 UI**：UI 状态（如"当前是否在权限确认中"）通过 React state 驱动，不需要手动管理 cursor / 屏幕坐标
3. **复用 React 生态**：useEffect、useMemo、useDeferredValue、useSyncExternalStore 等 hooks 可以直接用于 TUI 场景
4. **同构开发体验**：团队成员从 Web 端转 TUI 几乎无切换成本

代价是：

- Ink 的默认 reconciler 对终端的优化不够，复杂场景需要自定义渲染器（详见 2.4.2）
- Bundle 体积更大（React + Ink + 自定义渲染器 ≈ 数 MB）
- 调试更复杂（需要在 stdout/stderr 上模拟 DevTools）

Claude ​Code 接受这些代价，因为它的 TUI 复杂度（权限弹窗、流式输出、Plan Mode、Slash command 补全、Multi-line input）已经远超传统 CLI。`screens/REPL.tsx` 文件达 4500+ 行，import 200+ 个模块，说明这不是普通的命令行界面。

### 2.4.2 自定义 Ink Renderer

源码中 `src/ink.ts` 与 `src/ink/ink.tsx` 实现了一个自定义的 Ink Renderer。它的职责包括：

- **Front/Back Frame 管理**：维护两个 frame buffer，渲染时计算 diff，只输出变化的部分
- **React Reconciler 集成**：使用 `ConcurrentRoot`，支持 React 18+ 的并发渲染
- **Terminal Diff**：通过自定义算法计算两帧之间的差异，避免全屏重绘
- **Console/Stderr Patch**：覆盖 `console.log`、`process.stderr.write`，把它们的输出整合到 TUI 渲染管线
- **Alt-Screen / Selection / Cursor 管理**：处理终端 alternate screen buffer、文本选择、光标位置

这个自定义渲染器是 Claude ​Code TUI 流畅度的关键。原生 Ink 在频繁渲染（如 streaming markdown）时会出现闪烁与撕裂，自定义渲染器通过 frame 复用与 diff 计算显著降低了感知延迟。

### 2.4.3 状态管理：useSyncExternalStore + 外部 store

Claude ​Code 没有把所有状态塞进 React `useState`。它在 `src/state/store.ts` 实现了一个小型外部 store，通过 `useSyncExternalStore` 集成到 React：

```typescript
type Store<T> = {
  getState: () => T
  setState: (updater: (prev: T) => T) => void
  subscribe: (listener: () => void) => () => void
}
```

`setState` 使用 updater 计算 next，`Object.is(next, prev)` 时直接返回，否则通知 listeners。`AppStateProvider` 创建 store，`useAppState(selector)` 通过 `useSyncExternalStore` 订阅一个 slice。

这种"外部 store + React selector"模式（类似 Zustand）的好处：

- **避免 React tree 嵌套的 props drilling**
- **避免 Context provider 重新渲染整个子树**
- **支持深选 slice，减少不必要的重渲染**
- **可以在 React 外部（如 query loop 内）直接读写 state**

源码注释明确要求 selector 只返回稳定 slice，不要返回新对象——说明性能问题是真实发生过的。这种细节体现了 Claude ​Code 在 TUI 性能上的工程投入。

## 2.5 构建与运行时

### 2.5.1 Bun bundler

Claude ​Code 使用 **Bun** 作为构建工具与运行时。Bun 是 2022 年推出的 JavaScript 运行时，定位为 Node.js 的高性能替代品，特色包括：

- 集成的打包器（Bun bundler），无需 Webpack / Rollup
- 内置 TypeScript 支持，无需 ts-loader / tsc
- 支持构建时宏（macros），可在编译期执行任意 JS 代码
- 启动速度快（接近原生）
- 兼容 Node.js API

Claude ​Code 利用 Bun 的若干特性：

1. **`bun:bundle` import**：源码中多处出现 `import { feature } from 'bun:bundle'`，这是 Bun bundler 提供的编译期 import
2. **Build-time macros**：`MACRO.VERSION` 在编译期被替换为实际版本号字符串
3. **Dead Code Elimination**：通过 `ifFeature()` 宏实现按构建变体的死码消除

### 2.5.2 `ifFeature()` 宏：发行模型作为编译策略

Claude ​Code 维护三个构建变体：OSS（开源版）、Enterprise（企业版）、Internal（Anthropic 内部版）。三个版本共用同一份源码，差异通过 `ifFeature()` 宏在编译期裁剪。

`ifFeature()` 不是传统的运行时 feature flag（`if (process.env.FEATURE_X)`），而是**构建时死码消除**：

```typescript
// 传统 feature flag —— 代码在 bundle 中
if (process.env.FEATURE_X) {
  import('./enterpriseModule')
}

// ifFeature —— 编译期判断，false 分支被完全删除
ifFeature('ENTERPRISE', () => import('./enterpriseModule'))
```

源码中出现的 feature gates 包括（部分）：

| Feature | 用途 |
|---------|------|
| `COORDINATOR_MODE` | 多 Agent 协调模式 |
| `KAIROS` | 高级调度系统 |
| `TRANSCRIPT_CLASSIFIER` | 自动模式分类器 |
| `VOICE_MODE` | 语音输入 |
| `BUDDY` | AI 伙伴功能 |
| `BRIDGE_MODE` | 远程桥接 |
| `FORK_SUBAGENT` | 子 Agent fork |
| `WORKFLOW_SCRIPTS` | 工作流脚本 |
| `MCP_SKILLS` | MCP 技能 |
| `AGENT_MEMORY_SNAPSHOT` | Agent 记忆快照 |
| `EXPERIMENTAL_SKILL_SEARCH` | 实验性技能搜索 |
| `WEB_BROWSER_TOOL` | Web 浏览器工具 |
| `TOKEN_BUDGET` | Token 预算管理 |
| `PROACTIVE` | 主动建议 |

收益：

- **更小的包体积**：OSS 版本不包含 Enterprise 代码
- **更快的启动速度**：不需要的模块不在 bundle 中
- **企业代码不会泄露到 OSS**

代价：

- 必须在构建时确定特性集合，不能运行时动态切换
- 代码中 `ifFeature` 判断散布，阅读时需要关注当前构建上下文

### 2.5.3 TypeScript 严格模式与 Zod schema

Claude ​Code 全代码库使用 **TypeScript 严格模式**（`strict: true`）。从 source map 还原的源码中，所有 public API、tool definition、message 类型都有完整的 TypeScript 类型注解。

工具参数校验使用 **Zod**（TypeScript-first 的 schema validation 库）。每个工具的 `inputSchema` 是一个 Zod schema 对象：

```typescript
const inputSchema = z.object({
  command: z.string().describe("Shell command to execute"),
  timeout: z.number().optional()
})

type Input = z.infer<typeof inputSchema>  // 自动推导 TS 类型
```

Zod schema 在 Claude ​Code 中承担三个职责：

1. **生成 JSON Schema 给模型**：Zod schema 可以转换为 JSON Schema，作为 Anthropic Tool Use 协议中的 `input_schema` 发给模型
2. **运行时输入校验**：模型返回的 `tool_use.input` 通过 `inputSchema.safeParse()` 校验，失败时返回 `InputValidationError` 的 tool_result
3. **TypeScript 类型推导**：工具实现内部可以用 `z.infer<typeof inputSchema>` 得到强类型 input

这种"一处定义，三处使用"的设计让工具开发者不必维护多份重复定义。MCP 工具是个例外——MCP 协议本身使用 JSON Schema 而非 Zod，所以 MCP 工具的 schema 来自 server 声明，Claude ​Code 直接使用 JSON Schema 校验。

## 2.6 Anthropic SDK

Claude ​Code 通过官方 **`@anthropic-ai/sdk`** TypeScript SDK 与 Anthropic Messages API 通信。SDK 提供：

- 类型安全的 API 客户端
- 流式响应解析（`stream` 方法返回 AsyncIterable）
- 错误处理（包括 rate limiting、retry 提示）
- Token 计数辅助
- Beta features 支持（如 prompt caching、computer use）

Claude ​Code 在 `services/api/claude.ts` 中封装了对 SDK 的调用，主要扩展包括：

- **prompt caching 控制**：在 system prompt 和 tools 中精确放置 `cache_control: { type: 'ephemeral' }` 标记
- **流式归并**：把 SDK 返回的低层事件归并为高层语义事件
- **错误分类**：把 SDK 错误分类为 recoverable / unrecoverable，传给 query_loop 的 Phase 3 恢复逻辑
- **多模型支持**：支持在不同模型（Opus / Sonnet / Haiku / 第三方兼容如 Bedrock / GCP Vertex / 阿里云百炼）之间无缝切换

## 2.7 Prompt Caching

Anthropic Messages API 在 2024 年 8 月引入 **prompt caching** 特性，允许在请求中标记某段 prompt 作为"缓存断点"。下次请求时如果 prompt 前缀相同，缓存的部分以更低成本计算（约原价格的 10%）并加速响应。

Prompt caching 的工程影响远超"省钱"。它本质上是一个工程约束：**任何让 prompt 前缀位置变化的修改都会让缓存失效**。这意味着：

- system prompt 的拼装顺序必须稳定
- tools 的声明顺序必须稳定（详见 4.2 节关于 `assembleToolPool` 的设计）
- 缓存断点的位置必须精确（不能放错位置导致后续内容也失效）

Claude ​Code 把 prompt cache 稳定性提升到**一等工程约束**的高度。例如 `tools.ts:354-359` 的注释明确说明：MCP 工具与内建工具不能 flat sort，否则新 MCP 工具按字母序插入会让 prompt cache key 失效。这种"prompt cache 影响代码结构"的现象只有在工程化深度足够的 Agent Runtime 中才会出现。

## 2.8 同类编程 Agent 对照

Claude ​Code 不是孤立产品。理解它的技术选择，需要把它放在同类编程 Agent 的坐标系中观察。

### 2.8.1 Aider

**Aider** 是较早期的开源编程 Agent CLI（2023 年 4 月发布），主打"git 中心"的设计哲学：每次模型修改都自动 commit，用户可以方便 review/revert。

技术对照：

| 维度 | Aider | Claude ​Code |
|------|-------|-------------|
| Runtime 形态 | 终端 CLI（无 TUI） | 终端 REPL（React + Ink TUI） |
| 核心循环 | 对话 + git commit | 6 阶段状态机 + AsyncGenerator |
| 工具协议 | Aider 自定义文本格式（搜索/替换块） | Anthropic Tool Use 协议 |
| 模型 | 多模型支持（OpenAI/Anthropic/Bedrock 等） | 主要 Anthropic（兼容百炼） |
| 上下文管理 | repo map + recent files | Auto-compact + memory + skill |
| 多 Agent | 无 | Agent Teams |

Aider 的设计哲学是"**git 是变更的 single source of truth**"——所有修改都先 commit，再让用户决定保留或回滚。这是非常工程化的设计，但牺牲了流畅性（每次模型修改都自动 commit 会让历史很乱）。

Claude ​Code 选择的哲学是"**transcript 是 Runtime 的 single source of truth**"——所有状态都体现在内部消息历史中，git 是用户自己管理的领域，Runtime 不强加 commit 策略。

### 2.8.2 Cline

**Cline**（前身 Claude Dev）是流行的 VS Code 扩展形态的编程 Agent。它的核心循环遵循 **Plan-Act-Observe** 范式，强调"先规划再执行"。

技术对照：

| 维度 | Cline | Claude ​Code |
|------|-------|-------------|
| Runtime 形态 | VS Code 扩展（Webview UI） | 终端 REPL |
| 集成方式 | 紧耦合 VS Code API | 与 IDE 解耦，IDE 通过 LSP/MCP 通信 |
| Plan 模式 | 显式 Plan/Act 切换 | EnterPlanMode 工具（可选） |
| 多 Agent | 单 Agent | Agent Teams |

Cline 的优势是 IDE 集成的天然便利（直接读编辑器选区、文件 diff 渲染、内联建议）；代价是绑定 VS Code 生态。Claude ​Code 选择"终端优先 + IDE 通过协议接入"，更通用但失去部分 IDE 原生体验。

### 2.8.3 Devin

**Devin**（Cognition AI 2024 年 3 月发布）是云端 Web Dashboard 形态的"自主软件工程师"，主打"接受一个 issue，自主完成"的端到端能力。

技术对照：

| 维度 | Devin | Claude ​Code |
|------|-------|-------------|
| Runtime 形态 | 云端沙箱 + Web Dashboard | 本地终端 |
| 自主程度 | 高度自主（隔离环境，最小化人介入） | 交互式（每个高风险动作可拦截） |
| 权限模型 | 沙箱内全权限 | 三级 PermissionMode + 用户确认 |
| 多 Agent | 隐式多步 | 显式 Agent Teams |

Devin 的设计哲学是"**最大化自主性，隔离保证安全**"——既然在隔离沙箱里跑，就让 Agent 充分发挥。Claude ​Code 选择"**本地执行，权限细粒度**"——既然在用户真实机器上跑，就必须用权限机制平衡自主与安全。

### 2.8.4 Codex CLI

**OpenAI Codex CLI**（2025 年 4 月发布）是 OpenAI 推出的官方编程 Agent CLI，主打"简单即安全"——默认 `suggest` 模式只建议不执行，需要用户显式批准 `auto-edit` 或 `full-auto` 模式。

技术对照：

| 维度 | Codex CLI | Claude ​Code |
|------|-----------|-------------|
| Runtime 形态 | 终端 CLI | 终端 REPL（TUI） |
| 默认权限 | suggest（不执行） | DEFAULT（执行需确认） |
| 模型 | OpenAI Function Calling | Anthropic Tool Use |
| 多 Agent | 单 Agent | Agent Teams |

Codex CLI 的设计哲学是"**简单即安全**"。Claude ​Code 走的是另一条路：通过精细的权限模型、流式工具执行、Trust Boundary 等机制让用户在保持安全的同时获得高效率。两条路径各有适用场景。

### 2.8.5 设计哲学对比表

| 产品 | 核心哲学 |
|------|----------|
| Aider | git 是变更的 single source of truth |
| Cline | 把 Agent 深度集成进 IDE，让 Plan 显式可控 |
| Devin | 最大化自主性，隔离保证安全 |
| Codex CLI | 简单即安全——suggest 模式默认不执行 |
| **Claude ​Code** | **协议正确性优先于输出漂亮** |

后续章节将持续展开 Claude ​Code 这个哲学如何具体体现在每个机制中。

## 2.9 学术理论基础

### 2.9.1 ReAct 范式

ReAct（Yao et al., 2022）首次系统化提出"Reasoning + Acting"交替的 Agent 范式。Claude ​Code 的 query_loop 是 ReAct 范式的工程化实现，但通过 Tool Use 协议把 Action 与 Observation 结构化，避免了纯文本 ReAct 的解析脆弱性。

### 2.9.2 Reflection 与 Self-Correction

Reflexion（Shinn et al., 2023）等工作探索了 Agent 自我反思与自我纠错的机制。Claude ​Code 的对应实现是：

- 通过 `doing_tasks` prompt 的第 6 条规则约束模型"失败时先诊断再换策略"
- 通过 stop hooks 的 `blocking error` 机制允许 Hooks 在 Agent 完成时强制继续一轮（`transition.reason: stop_hook_blocking`）

### 2.9.3 Memory 与 Long-term Context

MemGPT（Packer et al., 2023）、Generative Agents（Park et al., 2023）等工作探索了 LLM 长期记忆的实现。Claude ​Code 的四层 Memory 体系（Auto / Session / Agent / Team）受这些工作影响，但采用了截然不同的具体策略：

- 不使用向量数据库（避免依赖外部服务）
- 不使用摘要分层（避免摘要质量下降）
- 使用 Markdown 文件 + MEMORY.md 索引（保持可审计、可手动编辑）
- 使用 Coalescing 而非 Debounce 提取策略（保证最终状态被处理）

第 5.7 节会详细对比这些不同。

### 2.9.4 Multi-Agent Frameworks

AutoGen（Wu et al., 2023）、CrewAI、MetaGPT 等工作探索了多 Agent 编排。Claude ​Code 的 Agent Teams 在这些工作的基础上选择了一种偏保守、偏工程化的路径：

- 不使用消息总线（用文件系统邮箱替代）
- 不依赖外部 orchestrator（用 contextvars 在同进程内做身份隔离）
- 三种后端（in-process / tmux / iterm2）覆盖大多数本地场景

详见第 5.8 节。

---

## 本章小结

本章梳理了 Claude ​Code 所依托的技术基础，覆盖编程 Agent 范式、Anthropic Tool Use 协议、MCP 协议、终端 UI 技术栈、构建与运行时、Prompt Caching、同类项目对照与学术理论基础。

关键判断：

- Claude ​Code 完全建立在 Anthropic Tool Use 协议之上，所有运行时机制围绕这个协议设计
- "stop_reason 不可靠"、"tool_use 与 tool_result 严格配对"、"内部 messages ≠ API messages"是 Runtime 必须处理的协议约束
- MCP 协议让 Claude ​Code 既能作为 Client 消费外部能力，也能作为 Server 对外暴露
- React + Ink + 自定义 Renderer 是 Claude ​Code 选择终端 TUI 的实现方案
- Bun bundler + `ifFeature()` 宏让单一代码库支持多构建变体
- Zod schema 在工具协议中扮演"一次定义，三处使用"的核心角色
- Prompt cache 稳定性是影响代码结构的一等工程约束

接下来的第 3 章将从需求分析的视角，把 Claude ​Code 需要解决的问题分解为功能性需求与非功能性需求，为后续详细设计章节奠定语境。
