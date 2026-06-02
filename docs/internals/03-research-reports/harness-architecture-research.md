# Harness 架构设计调研报告

> 生成时间：2026-05-16
> 调研范围：Claude Code Python Runtime 的 Harness 层（Control Plane）与 Anthropic 官方 Claude Code Harness 的架构设计
> 数据来源：Harness_Architecture_Design.json + Official_Claude_Code_Harness.json

---

## 目录

1. [概述](#一概述)
2. [Harness 的定义与架构定位](#二harness-的定义与架构定位)
3. [本地复现项目的 Harness 设计](#三本地复现项目的-harness-设计)
4. [官方 Claude Code Harness 设计](#四官方-claude-code-harness-设计)
5. [本地 vs 官方：对比分析](#五本地-vs-官方对比分析)
6. [Harness 与 Runtime 的协作关系](#六harness-与-runtime-的协作关系)
7. [关键设计决策](#七关键设计决策)
8. [面试要点](#八面试要点)
9. [总结与启示](#九总结与启示)
10. [Agent Builder 设计哲学与实现模式](#十agent-builder-设计哲学与实现模式)

---

## 一、概述

### 1.1 调研背景

在 AI Agent 系统的架构中，"Harness"是一个经常被提及但定义模糊的概念。它既不是模型本身（LLM），也不是单纯的用户界面（UI），而是介于两者之间的"控制平面"——负责将散落的组件组装成一台可运转的机器。

本调研基于两个维度展开：
- **本地维度**：ClaudeCode-Runtime（Python 复现项目）的 Harness 层实现
- **官方维度**：Anthropic Claude Code 官方产品的 Harness 架构

### 1.2 核心结论

**Harness = 容器 + 控制平面 + 生命周期管理器**

Harness 是 Agent 系统的"外壳"，负责：
- 组装依赖（工具、权限、配置）
- 管理生命周期（启动、运行、保存、恢复）
- 提供交互界面（CLI、IDE、桌面应用）
- 协调 Runtime 核心与外部环境

Runtime（query_loop）是"引擎"，负责思考-行动循环；Harness 是"车身"，负责让引擎在正确的道路上行驶。

---

## 二、Harness 的定义与架构定位

### 2.1 什么是 Harness？

在 AI Agent 的语境中，Harness 是**包裹 Runtime 核心的控制平面层**。它不做"思考"或"决策"，而是提供：

1. **输入输出通道**：把用户的打字/语音转换成消息，把模型的回复展示出来
2. **生命周期管理**：启动、关闭、会话保存/恢复
3. **环境集成**：文件系统访问、终端渲染、IDE 通信
4. **配置管理**：加载用户设置、hooks、CLAUDE.md

### 2.2 架构层次模型

```
┌─────────────────────────────────────────┐
│           Harness（外壳/载体）            │
│  ┌─────────────────────────────────────┐│
│  │  Control Plane / 装配层              ││
│  │  - CLI 参数解析、REPL 循环           ││
│  │  - 配置加载（settings.json、hooks）  ││
│  │  - API Client 创建                   ││
│  │  - System Prompt 组装                ││
│  │  - 工具注册与"二次布线"              ││
│  │  - QueryEngine 装配                  ││
│  └─────────────────────────────────────┘│
│  ┌─────────────────────────────────────┐│
│  │  UI / 交互层                         ││
│  │  - 终端渲染（Rich/TUI）              ││
│  │  - IDE 面板（VS Code Webview）       ││
│  │  - 桌面 GUI                          ││
│  │  - Web 界面                          ││
│  └─────────────────────────────────────┘│
├─────────────────────────────────────────┤
│         Agent Runtime Core（引擎）        │
│  - query_loop（对话状态机）              │
│  - 工具注册与执行（ToolRegistry）        │
│  - 权限检查（Permission Gate）           │
│  - 上下文压缩（Compact）                 │
│  - Memory 系统                           │
├─────────────────────────────────────────┤
│              LLM API（大脑）              │
│  - Anthropic API / OpenAI API           │
│  - 流式响应解析                          │
└─────────────────────────────────────────┘
```

### 2.3 Harness 与 Runtime 的分工边界

| 职责 | Harness | Runtime |
|---|---|---|
| 解析命令行参数 | ✅ | ❌ |
| 加载配置文件 | ✅ | ❌ |
| 组装 system prompt | ✅ | ❌ |
| 注册工具 | ✅ | ❌ |
| 创建 API client | ✅ | ❌ |
| 管理 REPL 循环 | ✅ | ❌ |
| 渲染 UI | ✅ | ❌ |
| 保存/恢复会话 | ✅（调用方） | ❌ |
| 驱动对话循环 | ✅（通过 QueryEngine） | ✅（实际执行） |
| 调用模型 API | ❌ | ✅ |
| 解析流式响应 | ❌ | ✅ |
| 执行工具 | ❌ | ✅ |
| 管理上下文压缩 | ❌ | ✅ |
| 错误恢复 | ❌ | ✅ |

**核心原则**：Harness 负责"准备和装配"，Runtime 负责"实际执行"。

### 2.4 两种 Harness 定义的对比与统一

在调研过程中，我们发现社区中存在两种对 Harness 的理解视角。GitHub 仓库 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 提供了一个**哲学/功能视角**的定义，而我们的调研报告采用了**工程/架构视角**的定义。两者并不矛盾，而是同一事物的不同抽象层次。

#### 2.4.1 哲学定义（shareAI-lab/learn-claude-code）

该仓库的核心观点是：**Agency 来自模型，Harness 只是让 Agency 落地的载体。**

```
Harness = Tools + Knowledge + Observation + Action Interfaces + Permissions

    Tools:          file I/O, shell, network, database, browser
    Knowledge:      product docs, domain references, API specs, style guides
    Observation:    git diff, error logs, browser state, sensor data
    Action:         CLI commands, API calls, UI interactions
    Permissions:    sandboxing, approval workflows, trust boundaries
```

**核心论断：**
- "The model decides. The harness executes."
- "The model is the driver, the harness is the vehicle."
- "You are not writing the intelligence. You are building the world the intelligence inhabits."

在这个定义中，Harness 被理解为**模型所处的世界**——所有让模型能够感知、推理和行动的外部基础设施的总和。它强调 Harness 的五个功能维度：
1. **Tools**：给模型"手"——文件读写、命令执行、网络访问
2. **Knowledge**：给模型"领域知识"——文档、规范、风格指南
3. **Observation**：给模型"感官"——git diff、错误日志、浏览器状态
4. **Action**：给模型"行动接口"——CLI、API、UI 交互
5. **Permissions**：给模型"边界"——沙箱、审批、信任边界

#### 2.4.2 工程定义（ClaudeCode-Runtime 调研）

我们的调研从代码实现出发，将 Harness 定义为：

```
Harness = 容器 + Control Plane + 生命周期管理器
```

**核心职责：**
- **组装依赖**：注册工具、构建 prompt、加载 hooks/MCP/team 连接
- **管理生命周期**：启动、运行、保存、恢复会话
- **提供交互界面**：CLI REPL、IDE 面板、桌面 GUI
- **协调 Runtime 与外部环境**：将外部输入转化为模型消息，将模型输出转化为外部动作

在这个定义中，Harness 是**代码层面的控制平面**——main.py 中的 `_build_engine()`、QueryEngine、REPL 循环、Session 存储等具体组件的集合。

#### 2.4.3 两种定义的映射关系

| 哲学定义（功能维度） | 工程定义（实现组件） | 对应源码位置 |
|---|---|---|
| **Tools** | ToolRegistry + Tool 实现类 | `cc/tools/` 目录 |
| **Knowledge** | System Prompt 组装 + CLAUDE.md 加载 + Skill 系统 | `cc/prompts/builder.py` + `cc/prompts/claudemd.py` |
| **Observation** | 工具执行结果的格式化与回传 | `cc/tools/*/execute()` + `cc/api/claude.py` |
| **Action** | QueryEngine 驱动 query_loop + StreamingToolExecutor | `cc/core/query_engine.py` + `cc/core/query_loop.py` |
| **Permissions** | PermissionContext + Hook 系统 | `cc/permissions/gate.py` + `cc/hooks/hook_runner.py` |
| （隐含：环境封装） | CLI Entry + REPL Loop + Session 管理 | `main.py` + `cc/session/storage.py` |

#### 2.4.4 为什么会有差异？

两种定义的差异源于**抽象层次**和**目标受众**的不同：

| 维度 | 哲学定义 | 工程定义 |
|---|---|---|
| **抽象层次** | 功能维度（What） | 实现组件（How） |
| **目标受众** | 学习者、架构师、产品经理 | 工程师、面试官、贡献者 |
| **边界范围** | 广义的"模型所处世界" | 狭义的"代码控制平面" |
| **隐喻** | Harness = Vehicle（载具） | Harness = Container（容器） |

**关键洞察**：哲学定义回答了"Harness 为模型提供了什么"，工程定义回答了"Harness 在代码中是什么"。前者是需求视角，后者是设计视角。

#### 2.4.5 统一后的完整定义

综合两种视角，Harness 的完整定义应为：

```
Harness 是 AI Agent 系统的控制平面，负责为模型构建、装配并维护一个完整的
"可操作世界"——包括工具、知识、观测通道、执行接口和权限边界——同时管理
该世界的生命周期（启动、运行、保存、恢复）和多表面交互（CLI、IDE、Desktop、Web）。
```

**一句话总结：**
- **哲学版**：Harness 是模型施展智能的舞台。
- **工程版**：Harness 是组装和驱动 Agent  Runtime 的控制平面。
- **统一版**：Harness 是**构建舞台并运营剧场的工程系统**——既包括舞台本身（功能维度），也包括剧场管理（生命周期维度）。

---

## 三、本地复现项目的 Harness 设计

### 3.1 整体架构

本地复现项目（ClaudeCode-Runtime）的 Harness 层集中在 `main.py` 中，实现了以下核心组件：

```
main.py
├── CLI Entry (click-based)
│   ├── --print mode (非交互式)
│   └── REPL mode (交互式)
│
├── _build_engine() —— 8 步装配流程
│   ├── Step 1: 加载 CLAUDE.md + 构建 system prompt
│   ├── Step 2: Coordinator 模式注入
│   ├── Step 3: 创建 PermissionContext
│   ├── Step 4: 创建 TaskRegistry + BackgroundAgentManager + TeamContext
│   ├── Step 5: 构建 call_model_factory（engine_ref 闭包技巧）
│   ├── Step 6: 注册所有工具（_build_registry）
│   ├── Step 7: 二次布线（AgentTool、Team 工具、SendMessage、TaskStop）
│   └── Step 8: 组装 QueryEngine
│
├── REPL Loop
│   ├── 读取用户输入（多行支持）
│   ├── 处理 slash 命令
│   ├── 轮询 inbox（team 模式）
│   ├── 驱动 engine.run_turn()
│   └── 后处理（保存 session + 后台 memory 提取）
│
└── _build_system() —— system prompt 组装
    ├── 静态段落（7 段，可缓存）
    ├── 动态段落（2 段）
    └── 条件段落（Memory + CLAUDE.md）
```

### 3.2 关键设计：QueryEngine 作为枢纽

QueryEngine 是 Harness 与 Runtime 之间的**关键桥梁**。它是一个**有状态容器**，持有：

```python
class QueryEngine:
    client: object          # Anthropic API client
    model: str              # 模型标识符
    registry: ToolRegistry  # 工具注册表
    system_prompt: str      # 完整的 system prompt
    hooks: list[HookConfig] # Hook 配置
    permission_ctx: PermissionContext  # 权限上下文
    messages: list[Message] # 对话 transcript（可变状态）
```

QueryEngine 暴露三个入口方法：
- `submit()` —— print 模式（非交互式单轮）
- `run_turn()` —— REPL 模式（交互式单轮）
- `submit_messages()` —— AgentTool 子 agent 调用

**设计意图**：QueryEngine 是 Harness 能直接操作的最小单元。它把 Harness 的"装配逻辑"与 Runtime 的"执行逻辑"解耦：
- Harness 负责创建和配置 QueryEngine
- QueryEngine 负责驱动 query_loop
- query_loop 是纯函数，不依赖任何全局状态

### 3.3 工具注册的三层策略

```
Tier 1: 核心文件操作（Eager 注册）
  Bash, FileRead, FileEdit, FileWrite, Glob, Grep

Tier 2: 扩展工具（Lazy Import）
  Task tools, WebFetch, AskUser, Todo, WebSearch,
  Notebook, ToolSearch, Skill, PlanMode, Brief, LSP

Tier 3: 协作工具（二次布线）
  AgentTool, TeamCreate, TeamDelete, SendMessage
```

**为什么需要"二次布线"？**

因为 Tier 3 工具（如 AgentTool）的构造函数需要 `TaskRegistry`、`BackgroundAgentManager` 等运行时依赖，而这些依赖是在 `_build_engine()` 的 Step 4 才创建的，晚于 Step 6 的工具注册。

解决方案：`engine_ref` 闭包技巧：
```python
engine_ref: list[QueryEngine] = []

def _factory(m=None, max_tokens=16384):
    if engine_ref:
        return engine_ref[0].make_call_model(model=m, max_tokens=max_tokens)
    return _make_call_model(client, m or model, max_tokens)

# 先注册一个"骨架版"工具
registry.register(AgentTool(call_model_factory=_factory, ...))

# 创建 engine 后，engine_ref[0] 指向 engine
engine = QueryEngine(...)
engine_ref.append(engine)
```

### 3.4 REPL 循环的五个阶段

```python
while True:
    # 阶段 1: 读取用户输入
    user_input = _read_multiline_input()

    # 阶段 2: 处理 slash 命令
    if user_input.startswith('/'):
        _handle_slash_command(user_input)
        continue

    # 阶段 3: 轮询 inbox（team 模式）
    _poll_inbox()

    # 阶段 4: 驱动对话引擎
    async for event in engine.run_turn(user_input):
        renderer.render_event(event)

    # 阶段 5: 后处理
    save_session(session_id, engine.messages)
    trigger_memory_extraction(engine.messages)
```

### 3.5 会话生命周期管理

**保存**：
- 格式：JSONL（每行一个 JSON 对象）
- 路径：`~/.claude/sessions/{session_id}.jsonl`
- 任务快照：单独存储为 `{session_id}.tasks.json`

**恢复**：
- `load_session()` 逐行读取 JSONL
- `validate_transcript()` 修复孤儿 tool_use（崩溃后 tool_result 缺失）
- 合成错误结果：`[Tool result missing due to internal error]`

**为什么用 JSONL？**
- 流式写入友好（每写完一条就 flush）
- 部分损坏可恢复（跳过损坏行）
- 便于追加（不需要读取整个文件）

### 3.6 事件驱动的 UI 架构

query_loop 通过 `yield` 输出事件，UI 层通过 `for ... in` 消费事件：

```python
# Runtime 层（query_loop）
async def query_loop(...):
    ...
    yield TextDelta(text="Let me check...")
    yield ToolUseStart(tool_name="Read", ...)
    yield ToolResultReady(tool_id="...", content="...")
    yield TurnComplete(stop_reason="tool_use")

# Harness 层（renderer）
class Renderer:
    def render_event(self, event):
        if isinstance(event, TextDelta):
            self.console.print(event.text, end="")
        elif isinstance(event, ToolUseStart):
            self._show_tool_call(event.tool_name, event.input)
```

**设计价值**：Runtime 完全不依赖 UI 层。你可以用同样的 query_loop，把终端 UI 换成 Web UI，不需要改动核心逻辑。

### 3.7 权限系统的 Harness 集成

Harness 在 `_build_engine()` 中创建 `PermissionContext`：

```python
permission_ctx = PermissionContext(
    mode=PermissionMode.ACCEPT_EDITS,  # 文件编辑自动放行
    is_interactive=is_interactive,      # 非交互模式全部放行
)
```

然后通过 `permission_checker` 回调传入 query_loop：

```python
# query_loop 内部调用
permission_checker(tool_name, tool_input)  # 返回 allow / deny / ask
```

这种设计让权限检查可以**在 Runtime 执行过程中**被调用，但权限策略的定义和配置完全由 Harness 控制。

### 3.8 MCP 集成

Harness 负责：
1. 从 `~/.claude/mcp.json` 和项目级 `.mcp.json` 加载 MCP 配置
2. 建立 stdio/SSE/HTTP 传输连接
3. 发现工具并注册为 `McpToolProxy`
4. Proxy 将工具调用转发给 MCP Server

Runtime 视角：MCP 工具与内置工具**无差别**，统一走 ToolRegistry 查找和 StreamingToolExecutor 执行。

### 3.9 Background Agent 管理

Harness 通过 `BackgroundAgentManager` 管理并发子 agent：

```python
bg_manager.spawn(
    coroutine=run_subagent(...),
    task_id="task_01",
)
```

- `spawn()` 包装为 `asyncio.Task`，注册到 `TaskRegistry`
- `poll_completed()` 非阻塞检查已完成任务
- REPL 的 `_poll_inbox()` 定期调用 `poll_completed()`，让主 agent 了解后台进展

### 3.10 Memory 提取的异步触发

每轮 REPL 结束后，Harness 触发 memory 提取：

```python
asyncio.create_task(
    extraction_coordinator.extract(messages)
)
```

- **异步执行**：不阻塞用户输入
- **Coalescing 策略**：如果新消息到达时提取仍在运行，设置 dirty 标志，完成后自动重跑
- **时间差**：提取的记忆**不会**刷新当前 session 的 system prompt，只在**下次启动时**生效

---

## 四、官方 Claude Code Harness 设计

### 4.1 整体架构

官方 Claude Code 是 **TypeScript 实现**，编译为**各平台原生二进制**：

```
官方 Claude Code Harness
├── CLI Entry（原生二进制）
│   ├── curl/bash 安装
│   ├── Homebrew / WinGet / apt
│   └── 后台自动更新
│
├── query.ts —— 核心状态机（~1400 行）
│   └── queryLoop：while(true) 四阶段
│
├── 多界面部署
│   ├── Terminal CLI
│   ├── VS Code Extension
│   ├── JetBrains Plugin
│   ├── Desktop App
│   └── Web（claude.ai/code）
│
├── Settings 系统（四级层次）
├── Hooks 系统（五类处理器）
├── Session 管理（JSONL + Checkpointing）
└── MCP 集成（stdio/SSE/HTTP/SDK）
```

### 4.2 多界面部署架构

官方 Harness 的核心设计决策：**同一引擎，多个界面**。

| 界面 | 实现方式 | 特色功能 |
|---|---|---|
| **Terminal CLI** | TUI（Rich 渲染） | 最完整的 slash 命令、最快的启动 |
| **VS Code Extension** | Webview Panel + IDE MCP Server | 内联 diff、@-mentions、plan review、集成终端 |
| **JetBrains Plugin** | Plugin API | diff 查看、选择上下文共享、诊断共享 |
| **Desktop App** | 独立应用（Chat/Cowork/Code 三标签） | 并行会话、拖拽布局、SSH 环境、定时任务 |
| **Web** | claude.ai/code | 浏览器访问、无需安装 |
| **CI/CD** | Headless 模式（`-p` 参数） | 结构化输出、非交互式执行 |

**VS Code 的 IDE MCP Server**：
- 名称：`ide`
- 地址：`127.0.0.1:随机端口`
- 认证：每次激活生成新 Token
- 暴露工具：`getDiagnostics`、`executeCode`
- 内部 RPC：diff 查看、文件操作

### 4.3 Settings 系统的四级层次

```
优先级（高 → 低）

Managed（企业/MDM 策略）
  └─ 服务器推送的策略，用户无法覆盖

CLI Args（命令行参数）
  └─ --model, --verbose, --print 等

Local（.claude/settings.local.json）
  └─ 本地私有配置，不入 git

Project（.claude/settings.json）
  └─ 项目级配置，可共享给团队成员

User（~/.claude/settings.json）
  └─ 用户全局配置
```

**JSON Schema**：官方提供 `https://json.schemastore.org/claude-code-settings.json`，在 VS Code 中支持自动补全。

**配置项示例**：
```json
{
  "permissions": {
    "allow": ["Bash(npm run *)", "Read(./.env)"],
    "deny": ["Bash(rm -rf *)"]
  },
  "editorMode": "vim",
  "tuiMode": "fullscreen",
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] }
  }
}
```

### 4.4 Hooks 系统的五类处理器

Hooks 是官方 Harness 的**扩展机制**，支持在 ~25 个生命周期事件插入自定义逻辑：

| 处理器类型 | 触发方式 | 能力 |
|---|---|---|
| **command** | 执行 shell 命令 | 阻塞/非阻塞、修改输入 |
| **http** | POST 到指定端点 | 远程审计、集成 |
| **mcp_tool** | 调用 MCP 工具 | 复用外部服务 |
| **prompt** | LLM 评估 | 智能判断 |
| **agent** | 子 agent 验证 | 复杂验证逻辑 |

**生命周期事件**：
- Session 级：`SessionStart`, `SessionEnd`
- Turn 级：`UserPromptSubmit`, `Stop`
- Tool 级：`PreToolUse`, `PostToolUse`, `PostToolBatch`, `PermissionRequest`, `PermissionDenied`

**PreToolUse Hook 的强大能力**：
```json
{
  "permissionDecision": "allow" | "deny" | "ask" | "defer",
  "updatedInput": { "modified": "tool arguments" }
}
```

### 4.5 Session 管理与 Checkpointing

**JSONL 存储**：
- 路径：`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`
- 自动清理：30 天后删除（可配置 `cleanupPeriodDays`）

**Session 操作**：
- `--continue`：继续上次会话
- `--resume <id>`：恢复指定会话
- `--fork-session <id>`：分叉会话（复制当前状态到新会话）
- `/branch`：在会话内创建分支（实验性方案）

**Checkpointing（检查点）**：
- 每次编辑前自动快照文件内容
- 回滚：`Esc+Esc` 或 `/rewind`
- **不是 git 替代**：仅 session 级撤销，Bash 命令变更不被追踪

### 4.6 MCP 集成策略

**传输协议**：stdio / SSE / HTTP / SDK / claudeai-proxy

**工具命名**：`mcp__<server>__<tool>`（如 `mcp__fetch__get_webpage`）

**Schema 延迟加载**：
- 默认 defer：不把所有 MCP 工具的 schema 一次性加载到 context
- 需要时通过 Tool Search 按需获取
- 节省 context window 空间

### 4.7 Desktop App 架构

Desktop App 是官方 Harness 最复杂的界面实现：

**三标签页设计**：
- **Chat**：标准对话界面
- **Cowork**：后台 agent 管理
- **Code**：交互式编码环境

**核心功能**：
- 本地 / 远程 / SSH 执行环境
- 并行会话（通过 git worktree 隔离）
- 拖拽式面板布局
- 集成终端和文件编辑器
- 可视 diff 审查
- App 预览
- PR 监控（自动合并）
- 定时任务（cron-like）

**限制**：Linux 暂无 Desktop App，使用 CLI 替代。

### 4.8 Agent SDK

官方提供 **TypeScript** 和 **Python** 两种 SDK：

```typescript
// TypeScript SDK
import { query } from '@anthropic-ai/claude-agent-sdk';

const events = query({
  messages: [...],
  system: '...',
  tools: [...],
  effort: 'high',  // 默认 high
});
```

```python
# Python SDK
from claude_agent_sdk import query

events = query(
    messages=[...],
    system='...',
    tools=[...],
    # effort 未设置
)
```

**关键差异**：
- TypeScript SDK **捆绑原生二进制**（无需单独安装 CLI）
- Python SDK 需要**单独安装 CLI**
- Python SDK 提供 `ClaudeSDKClient` 用于有状态会话管理
- TypeScript 使用 `continue: true` 实现会话续接

### 4.9 上下文管理策略

官方 Harness 的上下文管理比复现项目更丰富：

1. **Auto-compact**：~95% context window 时触发（复现为 200K - 13K = 187K）
2. **Prompt Caching**：重复前缀自动缓存（静态 system prompt 段落）
3. **MCP Schema Defer**：按需加载工具定义
4. **CLAUDE.md 指导压缩**：compactor 读取 CLAUDE.md 获取摘要指导
5. **子 agent 隔离**：每个子 agent 获得独立的 context window

### 4.10 权限模式的演进

官方提供 **6 种权限模式**（复现项目仅实现 3 种）：

| 模式 | 行为 | 切换方式 |
|---|---|---|
| **Default** | 编辑/命令前询问 | Shift+Tab |
| **Accept Edits** | 文件编辑自动放行 | Shift+Tab |
| **Plan** | 只读工具，不执行 | Shift+Tab |
| **Auto** | 模型分类器自动批准/拒绝 | Shift+Tab（研究预览） |
| **Don't Ask** | 不询问（危险） | 配置 |
| **Bypass** | 运行所有允许的工具 | 配置 |

**规则优先级**：`deny > ask > allow`

---

## 五、本地 vs 官方：对比分析

### 5.1 核心差异矩阵

| 维度 | Python 复现 | 官方 TypeScript |
|---|---|---|
| **实现语言** | Python 3.11+ | TypeScript → 原生二进制 |
| **部署形式** | uv 包管理 + Python 源码 | 各平台原生二进制（自动更新） |
| **运行界面** | Terminal only | Terminal / VS Code / JetBrains / Desktop / Web |
| **多界面共享引擎** | N/A | 是（同一 query.ts 核心） |
| **企业功能** | 无 | Managed settings / MDM 策略 |
| **远程执行** | Local only | Cloud / SSH / 容器环境 |
| **Settings 层次** | 2 层（User + Project） | 5 层（Managed > CLI > Local > Project > User） |
| **Hooks 类型** | 2 种（Pre/Post ToolUse） | 5 种（command/http/mcp_tool/prompt/agent） |
| **权限模式** | 3 种（BYPASS/ACCEPT_EDITS/DEFAULT） | 6 种（+ Plan/Auto/Don't Ask） |
| **Checkpointing** | 无 | 每次编辑前自动快照 |
| **Desktop App** | 无 | Chat/Cowork/Code 三标签 |
| **Agent SDK** | 无 | TypeScript + Python SDK |
| **内置工具数** | 22 个 | 更多（含云端工具） |
| **MCP 传输** | stdio | stdio / SSE / HTTP / SDK / proxy |
| **Background Agent** | 基础实现 | 完整实现（TaskRegistry + Inbox） |
| **Session 操作** | Save/Resume | Save/Resume/Fork/Branch |
| **Context 管理** | Auto-compact | Auto-compact + Prompt Caching + Schema Defer |
| **代码规模** | ~5000 行（Python） | ~1400 行 query.ts + 大量周边代码 |

### 5.2 复现项目的完整度评估

**已完整复现的组件**：
- ✅ query_loop 四阶段状态机
- ✅ ToolRegistry + 三层注册策略
- ✅ StreamingToolExecutor（并发控制）
- ✅ Permission Gate（三层白名单）
- ✅ Auto-compact + Reactive compact
- ✅ Session 保存/恢复（JSONL）
- ✅ Memory 系统（4 类型 + Coalescing）
- ✅ MCP Client（stdio 连接）
- ✅ Hooks 系统（基础实现）
- ✅ Background Agent（基础实现）

**未复现的组件**：
- ❌ 多界面部署（VS Code / JetBrains / Desktop / Web）
- ❌ 原生二进制编译
- ❌ 企业级 Managed Settings
- ❌ Checkpointing（文件级撤销）
- ❌ Agent SDK（Python SDK 是独立的）
- ❌ Desktop App 功能（SSH、定时任务、PR 监控）
- ❌ Prompt Caching 的 API 层实现
- ❌ 完整的 Hooks 类型（仅实现 2/5）
- ❌ 云端/远程执行环境

### 5.3 架构一致性评估

**核心架构高度一致**：
- query_loop 的四阶段设计完全相同
- ToolRegistry 的三层注册策略一致
- Event-driven UI 架构一致
- Session 的 JSONL 格式一致
- Memory 的两级文件结构一致
- Permission 的白名单模式一致

**差异主要在"产品化"层面**：
- 官方有更多界面、更多企业功能
- 复现项目专注于核心 runtime 的精确还原

---

## 六、Harness 与 Runtime 的协作关系

### 6.1 数据流图

```
用户输入
    │
    ▼
┌─────────────┐
│   Harness   │  ← 读取输入、解析 slash 命令
│  (main.py)  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ QueryEngine │  ← 组装请求（messages + system + tools）
│  (有状态)    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ query_loop  │  ← 纯函数状态机（4 阶段）
│  (无状态)    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  LLM API    │  ← Anthropic API
└──────┬──────┘
       │
       ▼
  流式响应
       │
       ▼
┌─────────────┐
│ query_loop  │  ← 解析响应、执行工具
│  (Phase 2-4)│
└──────┬──────┘
       │
       ▼
  QueryEvent 流
       │
       ▼
┌─────────────┐
│   Harness   │  ← 渲染 UI、保存 session
│  (Renderer) │
└─────────────┘
```

### 6.2 关键接口：QueryEvent

Harness 与 Runtime 之间的**唯一通信接口**是 `QueryEvent` 异步生成器：

```python
# Runtime 产出事件
async def query_loop(...) -> AsyncIterator[QueryEvent]:
    yield TextDelta(text="...")           # 文本片段
    yield ThinkingDelta(text="...")       # 思考过程
    yield ToolUseStart(...)               # 工具调用开始
    yield ToolResultReady(...)            # 工具结果就绪
    yield CompactOccurred(...)            # 压缩触发
    yield TurnComplete(...)               # 本轮结束
    yield ErrorEvent(...)                 # 错误

# Harness 消费事件
async for event in query_loop(...):
    if isinstance(event, TextDelta):
        print(event.text, end="")
    elif isinstance(event, ToolUseStart):
        show_tool_call(event.tool_name)
```

**设计价值**：
- Runtime 完全不知道 UI 如何渲染
- 同一 Runtime 可以驱动 Terminal TUI、VS Code Webview、Desktop GUI
- 测试可以 mock event 流，无需真实 API

### 6.3 状态分离

| 状态类型 | 所在位置 | 说明 |
|---|---|---|
| **Transcript** | QueryEngine.messages | 对话历史，每轮追加 |
| **System Prompt** | QueryEngine.system_prompt | 启动时组装，固定 |
| **工具注册表** | QueryEngine.registry | 启动时注册，运行时只读 |
| **权限上下文** | QueryEngine.permission_ctx | 启动时创建，运行时只读 |
| **Turn 计数** | query_loop 局部变量 | 每轮重新初始化 |
| **重试计数** | query_loop 局部变量 | 每轮重新初始化 |
| **流式累积** | query_loop 局部变量 | 当前轮次累积 |

**设计原则**：持久状态在 QueryEngine，临时状态在 query_loop。

---

## 七、关键设计决策

### 7.1 为什么 query_loop 是纯函数？

**决策**：所有依赖通过参数注入，不持有全局状态。

**收益**：
- 可测试性：mock call_model、mock tools、mock permissions
- 可复用性：同一 query_loop 用于 REPL、print 模式、子 agent
- 可预测性：给定相同输入，产生相同输出

**代价**：
- 参数列表较长（messages, system_prompt, tools, call_model, max_turns, ...）
- 需要 QueryEngine 作为有状态容器来管理参数

### 7.2 为什么用 engine_ref 闭包？

**问题**：AgentTool 的构造函数需要 `call_model_factory`，但 factory 需要 `engine.make_call_model()`，而 engine 在 factory 之后才创建。

**解决方案**：
```python
engine_ref = []  # 列表是可变对象，闭包捕获引用

def factory():
    if engine_ref:
        return engine_ref[0].make_call_model()
    return fallback()

# 注册工具时传入 factory（engine 还不存在）
registry.register(AgentTool(call_model_factory=factory))

# 创建 engine 后，engine_ref[0] 指向 engine
engine = QueryEngine(...)
engine_ref.append(engine)
```

**设计模式**：延迟初始化（Lazy Initialization）+ 可写闭包（Mutable Closure）。

### 7.3 为什么工具注册分三层？

| 层级 | 注册时机 | 原因 |
|---|---|---|
| Tier 1（核心） | 启动时立即 | 无依赖、最常用 |
| Tier 2（扩展） | 首次使用时 lazy import | 避免循环依赖、减少启动时间 |
| Tier 3（协作） | 二次布线 | 依赖运行时组件（TaskRegistry 等） |

### 7.4 为什么事件驱动 UI？

**替代方案**：Runtime 直接调用 UI 函数（如 `ui.print_text()`）。

**事件驱动的优势**：
- Runtime 不依赖 UI 库
- UI 层可以替换（TUI → GUI → Web）
- 支持多个消费者（日志、分析、UI 同时消费事件）

### 7.5 为什么 Memory 提取不刷新当前 session？

**设计**：提取的记忆只在下次启动时加载到 system prompt。

**原因**：
- 避免频繁刷新 system prompt（每次刷新都消耗 API token）
- 提取是异步的，可能在当前轮次中间完成
- 当前 session 的 transcript 已包含足够上下文

**权衡**：用户需要"重启"才能看到新记忆生效。

---

## 八、面试要点

### 8.1 核心概念

1. **Harness 是什么？**
   - AI Agent 系统的控制平面，负责组装依赖、管理生命周期、提供交互界面
   - 类比：Harness 是汽车的车身和仪表盘，Runtime 是发动机

2. **Harness 与 Runtime 的分工？**
   - Harness：准备和装配（CLI、配置、工具注册、UI）
   - Runtime：实际执行（调用模型、解析响应、执行工具、错误恢复）

3. **QueryEngine 的作用？**
   - Harness 与 Runtime 之间的枢纽
   - 有状态容器（messages、client、registry）
   - 暴露 submit() / run_turn() / submit_messages() 三个入口

### 8.2 架构设计

4. **为什么 query_loop 是纯函数？**
   - 可测试、可复用、可预测
   - 所有状态通过参数注入

5. **engine_ref 闭包解决了什么问题？**
   - 解决"工具需要 engine，但 engine 在工具之后创建"的鸡生蛋问题
   - 使用可变闭包实现延迟绑定

6. **事件驱动 UI 的好处？**
   - Runtime 与 UI 解耦
   - 同一 Runtime 可驱动多种界面

### 8.3 官方产品

7. **官方 Claude Code 支持哪些界面？**
   - Terminal、VS Code、JetBrains、Desktop、Web、CI/CD

8. **官方 Settings 的四级层次？**
   - Managed > CLI args > Local > Project > User

9. **Checkpointing 与 git 的区别？**
   - Checkpointing 是 session 级撤销（每次编辑前快照）
   - 不是 git 替代，Bash 命令变更不被追踪

10. **官方与复现的核心差异？**
    - 官方：多界面、原生二进制、企业功能、Agent SDK
    - 复现：Terminal only、纯 Python、核心 runtime 精确还原

### 8.4 深度问题

11. **MCP 工具与内置工具在 Runtime 视角有何区别？**
    - 无区别。MCP 工具通过 Proxy 注册到 ToolRegistry，Runtime 统一处理。

12. **Background Agent 如何与主 agent 通信？**
    - 通过文件系统邮箱（inbox）+ TaskRegistry 状态共享
    - 主 agent 轮询 `poll_completed()` 获取结果

13. **权限系统的三层白名单如何工作？**
    - 工具级：READ_ONLY_TOOLS / EDIT_TOOLS / 无分组
    - 命令级：Bash 内部的白名单（如 `git diff` 自动放行）
    - 用户级：`_always_allow` 缓存 + PreToolUse Hook

14. **Auto-compact 的滑动窗口设计？**
    - 保留最近 4 轮对话（POST_COMPACT_KEEP_TURNS）
    - 更早的消息压缩为 CompactBoundaryMessage
    - 摘要模型使用 COMPACT_SYSTEM_PROMPT 指导保留关键信息

15. **为什么 Memory 提取使用 Coalescing 而非 Debounce？**
    - Coalescing：设置 dirty 标志，完成后重跑，**保证最终状态被处理**
    - Debounce：可能丢弃中间状态，如果新消息在窗口关闭前到达会丢失

---

## 十、Agent Builder 设计哲学与实现模式

> 来源：`shareAI-lab/learn-claude-code` 的 `agent-builder` skill  
> 核心文件：`references/agent-philosophy.md`、`minimal-agent.py`、`tool-templates.py`、`subagent-pattern.py`、`scripts/init_agent.py`

### 10.1 核心哲学：模型即 Agent，代码即 Harness

agent-builder skill 的最核心论断与 2.4 节的哲学定义完全一致，但表述更加尖锐：

> **"The model is the agent. The code is the harness."**

#### 10.1.1 什么是 Agent？

Agent 不是代码，而是**经过训练的神经网络**——它通过数十亿次梯度更新，学会了感知环境、推理目标、采取行动。

- DeepMind 的 DQN 是 Agent（卷积网络学玩 Atari）
- OpenAI Five 是 Agent（五个网络学打 Dota 2）
- Claude 是 Agent（语言模型学推理和行动）

**在每种情况下，Agent 都是训练好的模型。不是游戏引擎，不是终端，不是代码。**

#### 10.1.2 什么是 Harness？

如果模型是 Agent，那么代码就是 **Harness**——让 Agent 能够在特定领域中感知和行动的环境。

```
Harness = Tools + Knowledge + Observation + Action Interfaces + Permissions
```

| 组成 | 回答的问题 | 对应 Runtime 组件 |
|------|-----------|------------------|
| **Tools** | Agent 能做什么？ | `ToolRegistry` + 工具实现 |
| **Knowledge** | Agent 知道什么？ | `System Prompt` + `CLAUDE.md` + Skill |
| **Observation** | 环境当前状态？ | 工具执行结果的回传 |
| **Action** | 如何影响环境？ | `QueryEngine` 驱动 `query_loop` |
| **Permissions** | 边界在哪里？ | `PermissionContext` + Hook 系统 |

#### 10.1.3 思维范式转换

agent-builder 要求开发者完成四个思维转换：

| 从 | 到 |
|---|---|
| "How do I make the system do X?" | "How do I enable the model to do X?" |
| "What should happen when user says Y?" | "What tools would help address Y?" |
| "What's the workflow for this task?" | "What does the model need to figure out the workflow?" |
| "I'm building an agent." | "I'm building a harness for the agent." |

### 10.2 渐进复杂度模型（Level 0-4）

agent-builder 明确反对"一开始就构建完整系统"，主张从最小可行版本开始，逐级增加复杂度。

#### 10.2.1 五级复杂度定义

```
Level 0: 模型 + bash（一个工具走天下）              ← ~50 行
Level 1: 模型 + 4 个核心工具（bash/read/write/edit）  ← ~200 行
Level 2: 模型 + TodoWrite（结构化规划）               ← ~300 行
Level 3: 模型 + Task 子代理（上下文隔离）              ← ~450 行
Level 4: 模型 + Skill 知识注入（领域专家）             ← ~550 行
```

#### 10.2.2 Level 0 的自递归技巧

Level 0 只有 `bash` 一个工具，但它通过 system prompt 教会模型**自递归**：

```python
SYSTEM = """You are a coding agent. Use bash for everything:
- Read: cat, grep, find, ls
- Write: echo 'content' > file
- Subagent: python my_agent.py "subtask"    # ← 自递归做子代理！
"""
```

**核心洞察**：一个工具（bash）就能完成所有事情——文件读写可以用 `cat` 和 `echo`，子代理可以用 `python agent.py` 启动新进程。其他工具只是让模型更方便，不是必须的。

#### 10.2.3 何时升级到下一级？

| 升级信号 | 升级到 |
|---------|--------|
| 模型频繁用 `cat`/`echo` 做文件操作，效率低 | Level 1（专用 read/write/edit 工具） |
| 多步骤任务经常遗漏步骤或顺序混乱 | Level 2（TodoWrite 结构化规划） |
| 探索代码库的详细输出污染主上下文 | Level 3（Task 子代理隔离） |
| 需要领域专业知识（如医学、法律） | Level 4（Skill 按需注入） |

### 10.3 最简 Agent 实现分析（minimal-agent.py）

`minimal-agent.py` 是上述哲学的**可运行证明**——80 行代码验证了整个架构的正确性。

#### 10.3.1 代码结构

```python
# 1. 配置（API key、模型、工作目录）
client = Anthropic(api_key=...)
MODEL = "claude-sonnet-4-20250514"
WORKDIR = Path.cwd()

# 2. System Prompt（极简规则）
SYSTEM = """You are a coding agent at {WORKDIR}.
Rules:
- Use tools to complete tasks
- Prefer action over explanation
- Summarize what you did when done"""

# 3. 工具定义（仅 3 个）
TOOLS = [bash_tool, read_file_tool, write_file_tool]

# 4. 工具执行
 def execute_tool(name, args): ...

# 5. Agent 循环（核心）
def agent(prompt, history):
    history.append({"role": "user", "content": prompt})
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=history, tools=TOOLS,
            max_tokens=8000,
        )
        if response.stop_reason != "tool_use":
            return  # 完成
        results = [execute_tool(...) for ...]
        history.append({"role": "user", "content": results})
```

#### 10.3.2 与 s01-s02 的完全对应

| minimal-agent.py | s01/s02 概念 | 说明 |
|---|---|---|
| `while True` | s01 核心循环 | 完全一致的循环结构 |
| `stop_reason != "tool_use"` | s01 退出条件 | 完全相同 |
| `history.append(tool_result)` | s01 回写机制 | 工具结果回写到 messages |
| `TOOLS = [...]` | s02 工具注册 | 工具定义注入 system prompt |
| `execute_tool()` | s02 工具分发 | `if name == "bash": ...` 映射表 |

### 10.4 工程化工具模板（tool-templates.py）

`tool-templates.py` 是 `minimal-agent.py` 的**工程化版本**，增加了安全和可扩展性。

#### 10.4.1 安全设计：路径逃逸防护

```python
def safe_path(p: str) -> Path:
    """Security: Ensure path stays within workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path
```

这就是 **s03 权限系统** 中"路径沙箱"的具体实现。

#### 10.4.2 安全设计：危险命令拦截

```python
dangerous = ["rm -rf /", "sudo", "shutdown", "> /dev/"]
if any(d in command for d in dangerous):
    return "Error: Dangerous command blocked"
```

#### 10.4.3 分发器模式

```python
def execute_tool(name: str, args: dict) -> str:
    if name == "bash":
        return run_bash(args["command"])
    if name == "read_file":
        return run_read_file(args["path"], args.get("limit"))
    # ... 新增工具只需在这里加一行
```

这与 `s02` 中 `TOOL_HANDLERS = {name: handler}` 的模式完全一致。

### 10.5 子代理模式（subagent-pattern.py）

这是 agent-builder 中**最精彩的部分**，直接对应 **s09 Agent Teams** 的核心机制。

#### 10.5.1 核心问题：上下文污染

当主代理让子代理"探索代码库"时，子代理可能调用 20 次 `read_file`，产生大量输出。如果这些全部回到主代理的上下文，主代理就会被"噪声"淹没。

#### 10.5.2 三大隔离机制

**机制 1：隔离的历史**

```python
# 子代理不从 parent 的 history 开始！
sub_messages = [{"role": "user", "content": prompt}]  # ← 全新的 messages
```

**机制 2：过滤的工具**

```python
AGENT_TYPES = {
    "explore": {
        "tools": ["bash", "read_file"],  # ← 只读！没有 write
        "prompt": "Search and analyze, but NEVER modify files..."
    },
    "code": {
        "tools": "*",  # ← 全部工具
        "prompt": "Implement the requested changes..."
    },
}
```

**机制 3：只返回摘要**

```python
# 子代理跑完循环后，只把最终文本返回给 parent
for block in response.content:
    if hasattr(block, "text"):
        return block.text  # ← parent 只看到总结
```

#### 10.5.3 与 s09 的映射

| subagent-pattern.py | s09 Agent Teams | 说明 |
|---|---|---|
| `run_task(description, prompt, agent_type)` | Leader 分配任务 | 通过 Task 工具调用 |
| `sub_messages = [{"role": "user", "content": prompt}]` | Teammate 隔离执行 | 独立的 context window |
| `return block.text` | 结果摘要返回 | 通过 inbox/SendMessage 回传 |
| `AGENT_TYPES["explore"]["tools"]` | 权限过滤 | 只读代理防止意外修改 |

### 10.6 脚手架脚本（init_agent.py）

`init_agent.py` 是一个**工程化工具**，根据复杂度层级生成 Agent 项目模板。

```bash
python init_agent.py my-agent --level 1  # 生成 ~200 行的基础 Agent
```

| 参数 | 生成内容 | 代码行数 |
|------|---------|---------|
| `--level 0` | 仅 bash 工具 | ~50 |
| `--level 1` | bash + read + write + edit | ~200 |
| `--level 2` | + TodoWrite | ~300 |
| `--level 3` | + Task 子代理 | ~450 |
| `--level 4` | + Skill 注入 | ~550 |

### 10.7 对 Runtime 设计的验证

agent-builder skill 的内容**从设计哲学层面验证**了 ClaudeCode-Runtime 的架构正确性：

| agent-builder 原则 | Runtime 实现 | 验证结果 |
|---|---|---|
| "Start with 3-5 tools" | Tier 1 核心工具 6 个，Tier 2/3 懒加载 | ✅ 符合 |
| "Trust the model" | 无硬编码工作流，模型自主决定工具序列 | ✅ 符合 |
| "Context isolation" | AgentTool 子代理 fresh messages | ✅ 符合 |
| "Progressive complexity" | s01 → s12 逐级增加机制 | ✅ 符合 |
| "Constraints enable" | Todo 单 in_progress、Permission 白名单 | ✅ 符合 |

---

## 九、总结与启示

### 9.1 Harness 的本质

Harness 不是"附加功能"，而是决定 Agent 产品形态的**关键架构层**：
- **复现项目**证明了核心 Runtime（query_loop）可以独立存在
- **官方产品**证明了同一 Runtime 可以驱动多种界面和产品形态
- **两者共享**相同的架构原则：事件驱动、状态分离、纯函数 Runtime

### 9.2 对日常开发的启示

1. **清晰分层**：把"装配逻辑"（Harness）和"执行逻辑"（Runtime）分开，可以让核心更容易测试和复用。

2. **接口设计**：QueryEvent 异步生成器是一个优秀的接口设计——简单、解耦、可扩展。

3. **延迟初始化**：engine_ref 闭包展示了如何用可变闭包解决循环依赖问题。

4. **产品化路径**：从复现项目到官方产品的演进路径是：核心 Runtime → 单界面 Harness → 多界面 Harness → 企业功能。

### 9.3 待深入研究的方向

- [ ] Desktop App 的 Electron（或等价框架）架构
- [ ] VS Code Extension 的 Webview ↔ Extension Host ↔ IDE MCP Server 通信
- [ ] 官方 TypeScript 代码的精确结构（query.ts 的 1400 行如何组织）
- [ ] Agent SDK 的内部实现（如何捆绑原生二进制）
- [ ] 企业级 Managed Settings 的推送机制
- [ ] Checkpointing 的文件快照实现（copy-on-write？）

---

## 参考文件

| 文件 | 说明 |
|---|---|
| `08_Skill-Outputs/research-deep-results/Harness_Architecture_Design.json` | 本地复现项目 Harness 调研 |
| `08_Skill-Outputs/research-deep-results/Official_Claude_Code_Harness.json` | 官方 Harness 调研 |
| `02_Source-Code/01_CC-Python-Runtime/Source/cc/main.py` | 本地 Harness 入口 |
| `02_Source-Code/01_CC-Python-Runtime/Source/cc/core/query_engine.py` | QueryEngine 实现 |
| `02_Source-Code/01_CC-Python-Runtime/Source/cc/core/query_loop.py` | Runtime 核心 |
| `03_References/learn-claude-code-skills/agent-builder/SKILL.md` | Agent Builder Skill 主文档 |
| `03_References/learn-claude-code-skills/agent-builder/references/agent-philosophy.md` | Agent 与 Harness 的哲学定义 |
| `03_References/learn-claude-code-skills/agent-builder/references/minimal-agent.py` | 最简 Agent 实现（80 行） |
| `03_References/learn-claude-code-skills/agent-builder/references/subagent-pattern.py` | 子代理上下文隔离模式 |
| `https://code.claude.com/docs` | 官方文档 |
