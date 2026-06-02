# Claude Code 原始 TypeScript 源码：全局架构与执行链路分析

> 源码路径：`src/`（约 1902 个 .ts/.tsx/.js/.json 文件）
> 编程语言：TypeScript (React + Ink 终端 UI)
> 运行时：Node.js（实际使用 Bun 构建/打包）
> 构建工具：Bun bundler（`bun:bundle` 的 `feature()` 宏）

---

## 1. 源码规模概述

| 维度 | 数值 |
|------|------|
| 总文件数 | ~1902 |
| 一级目录数 | ~50 |
| UI 框架 | React 19 + Ink（终端渲染库） |
| 状态管理 | React Context + 自建 Store（`createStore`） |
| 构建系统 | Bun bundler，支持 `feature()` 编译期死码消除 |
| 特色能力 | CLI / REPL / SDK(Headless) / MCP Server / Bridge Remote |

**核心目录分布：**

- `entrypoints/` -- 5 个入口文件（cli, init, mcp, sdk types, sandbox types）
- `tools/` -- 30+ 工具实现目录（BashTool, FileEditTool, AgentTool 等）
- `commands/` -- 50+ 命令实现目录（config, mcp, login, plan 等）
- `services/` -- 服务层（API 调用、MCP、压缩、分析、遥测等）
- `components/` -- React 组件（Messages, PromptInput, PermissionRequest 等）
- `screens/` -- 顶级界面组件（REPL.tsx 达 200+ 导入行）
- `utils/` -- 工具函数（permissions, model, messages, swarm 等）

---

## 2. 架构分层

| 层 | 目录 | 核心文件 | 职责 |
|---|------|---------|------|
| **入口层** | `entrypoints/` | `cli.tsx`, `init.ts`, `mcp.ts` | 进程入口、快速路由、初始化编排 |
| **编排层** | `src/` 根 | `main.tsx`, `replLauncher.tsx`, `query.ts`, `QueryEngine.ts` | 主流程编排、REPL 启动、主查询循环 |
| **界面层** | `screens/`, `components/` | `REPL.tsx`, `App.tsx`, `Messages.tsx`, `PromptInput/` | 终端 UI 渲染（Ink/React）、用户交互 |
| **工具层** | `tools/` | `tools.ts`(注册表), `BashTool/`, `FileEditTool/`, `AgentTool/` 等 | 30+ 工具实现、Tool 接口定义 |
| **命令层** | `commands/`, `commands.ts` | `commands.ts`(注册表), 50+ 子命令目录 | 斜杠命令与 CLI 子命令实现 |
| **服务层** | `services/` | `api/`(API 调用), `mcp/`(MCP 协议), `compact/`(上下文压缩), `analytics/`(遥测) | 对外 API 调用、MCP 集成、上下文管理、分析 |
| **基础设施层** | `utils/`, `bootstrap/`, `state/` | `config.ts`, `auth.ts`, `permissions/`, `messages.ts`, `swarm/` | 配置、认证、权限、消息处理、状态管理 |

---

## 3. 主执行链路

### 3.1 进程启动到 REPL 就绪

```
进程启动
  │
  ▼
cli.tsx  :: main() 异步入口
  ├─ --version/-v         → 直接输出版本号（零模块加载）
  ├─ --dump-system-prompt → 加载 prompts.js 输出 system prompt
  ├─ --daemon-worker      → runDaemonWorker() (内部 worker)
  ├─ remote-control/bridge→ bridgeMain() (远程控制)
  ├─ daemon               → daemonMain() (守护进程)
  ├─ ps/logs/attach/kill  → bg.js 会话管理
  ├─ --tmux --worktree    → execIntoTmuxWorktree()
  └─ 默认路径             → 动态 import('../main.js')
                              │
                              ▼
                         main.tsx :: main()
                           ├─ 顶层副作用：profileCheckpoint, startMdmRawRead, startKeychainPrefetch
                           ├─ init() :: entrypoints/init.ts
                           │   ├─ enableConfigs()          # 启用配置系统
                           │   ├─ applySafeConfigEnvironmentVariables()
                           │   ├─ applyExtraCACertsFromConfig()  # TLS 证书
                           │   ├─ setupGracefulShutdown()
                           │   ├─ configureGlobalMTLS()
                           │   ├─ configureGlobalAgents()  # HTTP 代理
                           │   ├─ preconnectAnthropicApi() # API 预热连接
                           │   └─ 异步: 1P Event Logging, OAuth, JetBrains检测, MDM/策略
                           │
                           ├─ 信任对话框 (Trust Dialog): 检查 hasTrustDialogAccepted
                           │   ├─ 若拒绝 → showSetupScreens() (Login/Onboarding)
                           │   └─ 若通过 → applyConfigEnvironmentVariables()
                           │              initializeTelemetryAfterTrust()
                           │
                           ├─ 异步初始加载:
                           │   ├─ loadPolicyLimits / loadRemoteManagedSettings
                           │   ├─ prefetchFastModeStatus / prefetchOfficialMcpUrls
                           │   └─ prefetchAwsCredentials.../prefetchGcpCredentials...
                           │
                           ├─ 配置 MCP Servers + Plugins + Skills
                           ├─ 构建 commands / tools / agents 注册表
                           │
                           ├─ [Headless/SDK] → 直接进入 QueryEngine 无头循环
                           │
                           └─ [REPL 模式]
                               │
                               ▼
                          replLauncher.tsx :: launchRepl()
                            ├─ 动态 import App 组件
                            ├─ 动态 import REPL 组件
                            └─ renderAndRun(root, <App><REPL/></App>)
                                │
                                ▼
                           REPL.tsx 挂载 (Ink TUI 运行)
```

### 3.2 一次用户输入的完整流转

```
用户输入（PromptInput）
  │
  ▼
REPL.tsx :: handlePromptSubmit()
  ├─ 解析输入：斜杠命令 / 引用文件 / @agent
  ├─ 构建 ProcessUserInputContext
  │   ├─ 添加 user context (记忆文件, CLAUDE.md, 仓库信息)
  │   ├─ 添加 system context (MCP 资源, 提示, hooks)
  │   └─ 构建消息列表 (Message[])
  │
  ▼
query.ts :: query()  [AsyncGenerator]
  │
  ├─ queryLoop() 迭代:
  │   ├─ 迭代前：
  │   │   ├─ buildQueryConfig() 冻结配置快照
  │   │   ├─ 处理挂起的 tool_use_summary
  │   │   └─ 检查 token 预算 (createBudgetTracker)
  │   │
  │   ├─ 检查 auto-compact 触发条件
  │   │   ├─ isAutoCompactEnabled → reactiveCompact / snipCompact
  │   │   └─ buildPostCompactMessages() 构建压缩后消息
  │   │
  │   ├─ 构建 API 请求参数:
  │   │   ├─ system prompt (appendSystemContext + prependUserContext)
  │   │   ├─ messages (normalizeMessagesForAPI)
  │   │   ├─ tools definitions
  │   │   └─ thinking config, max_tokens, task_budget
  │   │
  │   ├─ API 调用 :: Anthropic Messages API (streaming)
  │   │   ├─ yield 每个 StreamEvent (thinking/text/tool_use)
  │   │   └─ API 返回 AssistantMessage (含 stop_reason)
  │   │
  │   ├─ [stop_reason = tool_use] → 并行执行所有工具:
  │   │   ├─ runTools() → toolOrchestration.ts
  │   │   │   ├─ 权限检查 (PermissionRequest)
  │   │   │   ├─ 工具调用分发:
  │   │   │   │   ├─ BashTool    → 执行 shell 命令
  │   │   │   │   ├─ FileEditTool → 编辑文件
  │   │   │   │   ├─ AgentTool   → 启动子 Agent (TaskTool 模式)
  │   │   │   │   ├─ SkillTool   → 调用 Skill
  │   │   │   │   └─ ... 30+ 工具
  │   │   │   └─ 收集 tool_result 消息
  │   │   │
  │   │   └─ yield tool_result → 继续下一轮迭代
  │   │
  │   ├─ [stop_reason = end_turn] → 终止循环
  │   │   └─ 返回 terminal (Terminal 对象)
  │   │
  │   └─ [错误] → max_output_tokens 恢复 / fallback 模型 / 重试
  │
  ▼
REPL.tsx 收到最终结果
  ├─ 更新 messages 列表到 AppState
  ├─ 记录会话 transcript
  ├─ 更新 token 使用量 / 成本
  └─ 等待下一次用户输入
```

---

## 4. 多入口设计

Claude Code 通过 `cli.tsx` 的 fast-path 路由实现了多种入口模式，每种走不同的执行路径：

| 入口模式 | 触发条件 | 执行路径 | 说明 |
|---------|---------|---------|------|
| **CLI 标准** | 默认 `claude` | `cli.tsx` → `main.tsx` → `launchRepl` → `REPL.tsx` | 交互式终端 REPL |
| **REPL (Headless/SDK)** | 通过 SDK 调用 | `cli.tsx` → `main.tsx` → `QueryEngine` (跳过 Ink 渲染) | 无界面 Agent SDK |
| **MCP Server** | `--claude-in-chrome-mcp` 等 | `cli.tsx` → `runClaudeInChromeMcpServer()` | 作为 MCP Server 暴露 |
| **Bridge (Remote)** | `remote-control` / `bridge` / `sync` / `remote` | `cli.tsx` → `bridgeMain()` | 远程控制本地机器 |
| **Daemon** | `daemon start/stop/status` | `cli.tsx` → `daemonMain()` | 长期运行的守护进程 |
| **Background Sessions** | `ps/logs/attach/kill` / `--bg` | `cli.tsx` → `bg.js` handlers | 会话的持久化管理 |
| **Self-Hosted Runner** | `self-hosted-runner` | `cli.tsx` → `selfHostedRunnerMain()` | BYOC 无头 Runner |
| **Environment Runner** | `environment-runner` | `cli.tsx` → `environmentRunnerMain()` | 环境运行器 |
| **Template Jobs** | `new/list/reply` | `cli.tsx` → `templatesMain()` | 模板任务系统 |

关键设计：所有非默认路径使用**动态 import()**，在 `cli.tsx` 中通过 if/else 判断后按需加载，避免启动时代价。`feature()` 宏允许编译期删除内部路径（外部构建中这些代码完全不存在）。

---

## 5. 关键设计决策

### 5.1 渐进式加载 (Progressive Loading)

**做了什么：** `cli.tsx` 对 `--version` 路径执行零模块加载的快速路径；对其他快速路径（daemon, bridge, bg sessions 等）使用 `await import()` 按需加载；只有默认路径才加载完整的 `main.tsx`（200+ 静态导入）。

**为什么：** 减少冷启动延迟。`--version` 只需 console.log 一条预编译的版本号字符串；其他快速路径只加载所需的最小模块集。`main.tsx` 中有大量的工具注册、MCP 客户端、插件加载等，仅在真正需要 REPL 时加载。

### 5.2 feature() 宏与编译期死码消除 (DCE)

**做了什么：** 使用 `bun:bundle` 的 `feature('FLAG_NAME')` 宏包裹内部功能代码。例如 `feature('KAIROS')`、`feature('DAEMON')`、`feature('BRIDGE_MODE')` 等。外部构建时这些分支会被完全删除。

**为什么：** Claude Code 有内外部两个构建产物。内部（Ant）包含所有实验性功能和内部工具（REPLTool、internal logging、ablation baseline 等），外部（公开发布版）仅包含稳定功能。`feature()` 在编译期做 DCE，确保了：
- 外部构建体量更小
- 内部代码不会泄露到公开发布版
- 功能开关不影响运行时性能（是编译期决策，不是运行时检查）

### 5.3 AsyncGenerator 模式的查询循环

**做了什么：** `query()` 被设计为 `AsyncGenerator<StreamEvent | Message, Terminal>`，在 `queryLoop()` 内部通过 `yield*` 和 `yield` 逐个输出消息/事件/工具调用结果。调用方（REPL.tsx 或 SDK）通过 `for await...of` 逐帧消费。

**为什么：** 这使得流式输出与事件分发的边界清晰：
- REPL 模式下，每个 yield 的事件都能即时渲染到终端
- SDK 模式下，每个事件即时推送给调用者
- Generator 的 `return` 值 (`Terminal`) 提供循环的最终状态
- 错误通过 throw 传播，`generator.return()` 可提前终止
- 与 Anthropic 的 streaming API 天然契合

### 5.4 单一 AppState + React Context 的状态管理

**做了什么：** 全局状态集中在 `AppStateStore`（`src/state/AppStateStore.ts`），通过 `createStore` 创建，在 React 组件树根部的 `AppStateProvider` 中通过 `AppStoreContext` 提供。使用 `useSyncExternalStore` 实现对外部 store 订阅的 React 集成。

**为什么：** Ink（React 的终端渲染）天然需要 React 状态管理。但 Claude Code 的许多模块（utils, services）不使用 React，需要非 React API 访问状态。`createStore` 提供了 `getState()/setState()/subscribe()` 三件套，既支持 React hooks（`useAppStore`），又支持外部模块直接调用。这避免了引入 Redux/MobX 等第三方库的依赖负担。

### 5.5 工具与命令的插件化注册

**做了什么：** `tools.ts` 和 `commands.ts` 作为注册中心，所有工具和命令各自实现为独立模块，通过统一接口注册。工具实现 `Tool` 接口（含 `prompt`、`inputSchema`、`async call()` 等），命令通过 Commander.js 注册。两者都支持通过 feature 标志条件编译，以及运行时通过 MCP/Plugins 动态扩展。

**为什么：**
- 每个工具/命令独立开发和测试
- MCP 协议允许第三方工具注册
- `assembleToolPool()` 合并内置工具 + MCP 工具 + 插件工具 + 用户自定义 Agent 工具
- 条件编译确保特定工具只在特定构建中存在

### 5.6 上下文压缩的分层策略

**做了什么：** 实现了多层压缩：① `autoCompact` -- 自动检测触发条件；② `reactiveCompact` -- 基于规则的响应式压缩；③ `snipCompact` -- 基于历史截断的压缩；④ `contextCollapse` -- 上下文折叠。压缩后通过 `buildPostCompactMessages()` 重建消息列表。

**为什么：** 长对话的上下文窗口管理是 Claude Code 的核心挑战。分层策略允许不同场景使用不同算法：snipCompact 适合快速截断，reactiveCompact 适合智能裁剪，contextCollapse 适合极端长对话。每层独立可测试和调优。

### 5.7 Lazy Require 打破循环依赖

**做了什么：** 多个模块使用 `const getXxx = () => require('./xxx')` 的延迟获取模式，而不是直接 `import`。例如 `tools.ts` 中 TeamCreateTool/TeamDeleteTool 的获取，`main.tsx` 中 teammate 相关模块的获取。

**为什么：** Claude Code 有复杂的模块依赖图，循环依赖难以避免（工具依赖状态，状态依赖命令，命令依赖工具）。Lazy require 将依赖解析推迟到**调用时**而非**导入时**，在大多数循环中打破了初始化死锁。

---

## 6. 与 Python 复刻版的对应关系

| TypeScript 源码模块 | 路径 | Python 复刻模块 | 说明 |
|-------------------|------|----------------|------|
| `cli.tsx` | `src/entrypoints/cli.tsx` | `cli.py` | CLI 入口与快速路由分发 |
| `main.tsx` | `src/main.tsx` | `main.py` / `app.py` | 主编排中心，信任检查，REPL 启动 |
| `init.ts` | `src/entrypoints/init.ts` | `init.py` | 初始化：配置、代理、mTLS、遥测 |
| `query.ts` | `src/query.ts` | `query_loop.py` | 主查询循环 (AsyncGenerator) |
| `QueryEngine.ts` | `src/QueryEngine.ts` | `query_engine.py` | 查询引擎封装 (SDK 兼容) |
| `replLauncher.tsx` | `src/replLauncher.tsx` | `repl_launcher.py` | REPL 启动器 |
| `REPL.tsx` | `src/screens/REPL.tsx` | `repl_screen.py` | REPL 界面主组件 |
| `commands.ts` + `commands/` | `src/commands.ts` | `commands.py` + `commands/` | 命令注册与实现 |
| `tools.ts` + `tools/` | `src/tools.ts` | `tools.py` + `tools/` | 工具注册与实现 |
| `Tool.ts` | `src/Tool.ts` | `tool_base.py` | Tool 接口定义 |
| `AppStateStore.ts` | `src/state/AppStateStore.ts` | `app_state.py` | 全局应用状态 |
| `services/api/` | `src/services/api/` | `services/api/` | Anthropic API 调用层 |
| `services/mcp/` | `src/services/mcp/` | `services/mcp/` | MCP 协议客户端 |
| `services/compact/` | `src/services/compact/` | `services/compact/` | 上下文压缩 |
| `utils/messages.ts` | `src/utils/messages.ts` | `utils/messages.py` | 消息构建与标准化 |
| `utils/config.ts` | `src/utils/config.ts` | `utils/config.py` | 配置管理 |
| `utils/permissions/` | `src/utils/permissions/` | `utils/permissions/` | 权限系统 |
| `utils/model/` | `src/utils/model/model.ts` | `utils/model.py` | 模型选择与管理 |
| `constants/prompts.ts` | `src/constants/prompts.ts` | `constants/prompts.py` | System Prompt 构建 |
| `context.ts` | `src/context.ts` | `context.py` | 用户/系统上下文 |
| `ink.ts` + `ink/` | `src/ink.ts` | *(不使用)* | Ink 终端渲染 (Python 用 Textual/Rich 替代) |
| `components/` | `src/components/` | `components/` | React 组件 → Python UI 组件 |
| `hooks/` | `src/hooks/` | `hooks/` | React hooks → Python 等效逻辑 |
