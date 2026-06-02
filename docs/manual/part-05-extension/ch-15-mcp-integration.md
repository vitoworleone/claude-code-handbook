# 第15章 MCP 与外部系统集成

> 状态：初稿

Model Context Protocol（MCP）是 Claude Code 与**外部系统**集成的核心协议。与 Skills（内建能力扩展）和 Hooks（事件拦截）不同，MCP 让 Claude 能够**动态发现和调用**外部服务提供的工具——从数据库查询到浏览器自动化，从文件系统操作到第三方 API 调用。本章从协议概念出发，深入讲解传输机制、工具池组装流程、权限模型，以及生产环境的安全实践。

---

## 15.1 MCP 是什么

### 15.1.1 核心概念

Model Context Protocol（MCP）是一种**工具发现与调用协议**。它定义了外部系统（MCP Server）如何向 Claude Code 暴露自己的能力，以及 Claude Code 如何发现、选择和调用这些能力。

**MCP 的关键特征**：

1. **Server 贡献工具定义**：MCP Server 不是被动等待被调用的 API，而是主动向 Claude Code 注册自己的工具集——每个工具包含名称、描述、参数 schema 和调用端点。

2. **Claude 自主决定何时调用**：与 REST API 的"请求-响应"模式不同，MCP 的工具调用是由 Claude 的**推理过程**驱动的。Claude 在每一轮推理中审视可用的工具池，根据用户意图自主决定调用哪个工具、传入什么参数。

3. **双向通信**：MCP 不仅是 Claude 调用外部系统的单向通道，Server 也可以向 Claude 推送资源和提示（Resources & Prompts），丰富 Claude 的上下文。

### 15.1.2 MCP 与 REST API 的本质区别

这个区别是理解 MCP 的关键。

| 维度 | REST API | MCP |
|------|----------|-----|
| **调用方式** | 客户端明确调用特定端点 | Claude 自主决定调用哪个工具 |
| **发现机制** | 需要预先知道端点 URL 和参数 | Server 自动注册工具定义，Claude 动态发现 |
| **上下文感知** | 无——每次调用是独立的 | 有——工具调用成为对话历史的一部分 |
| **参数生成** | 由客户端代码构造 | 由 Claude 根据语义理解生成 |
| **错误处理** | 客户端代码处理 HTTP 状态码 | Claude 理解错误并决定重试或降级 |

**为什么会有这个设计差异？**

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Claude Code 的扩展架构时指出，传统的 API 集成模式（"我在代码里写死调用某某 API"）与 LLM 的工作方式存在根本矛盾：

- LLM 的强项是**语义理解和推理**，而不是精确的记忆和执行预定义调用链。
- 如果让开发者手动编写"当用户说 X 时调用 API Y"的逻辑，这不仅繁琐，而且无法覆盖自然语言的无穷变体。
- MCP 的设计将"何时调用"的决策权交给了 Claude——开发者只需要定义"我有什么能力"（工具描述），Claude 根据用户的自然语言输入自主决定"是否需要调用、调用哪个、传入什么参数"。

**这个结论能帮你做什么？**

当你考虑"应该用 MCP 还是直接调用 REST API"时，判断标准是：

- **使用 MCP**：当你希望 Claude **理解语义并自主决策**时（如"分析销售数据"→Claude 自主决定查询哪个数据库表、构造什么 SQL）
- **使用 REST API**：当你需要**精确控制调用逻辑**时（如"用户点击按钮后调用支付接口"）——这种场景更适合在应用层代码中实现，而非通过 MCP

**一句话概括**：REST API 是"你告诉程序做什么"，MCP 是"你告诉 Claude 你有什么能力，Claude 自己决定什么时候用"。

### 15.1.3 MCP 的三种内容类型

MCP Server 可以向 Claude Code 提供三种类型的内容：

| 内容类型 | 说明 | 典型用途 |
|---------|------|---------|
| **Tools** | 可执行的操作，Claude 可以调用它们来完成任务 | 查询数据库、发送邮件、操作浏览器 |
| **Resources** | 只读的数据内容，Claude 可以读取但不能修改 | 文档、配置文件、数据库 schema |
| **Prompts** | 预定义的提示模板，Claude 可以加载并使用 | 标准化的分析流程、报告模板 |

本章重点讲解 **Tools**（第15.6节会涉及 Resources 和 Prompts）。

---

## 15.2 MCP 传输类型

### 15.2.1 标准传输

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 MCP 子节时指出，MCP 协议支持多种传输机制。报告作者在解释这种多样性时强调，MCP 的设计目标是成为一个**通用的工具集成协议**，而不同的部署环境有着截然不同的网络拓扑和安全约束：个人开发者可能需要本地进程通信（stdio），企业团队可能需要跨网络的 HTTP 服务，IDE 插件需要与编辑器进程通信，而 Claude.ai 官方服务需要代理认证。

**为什么会有这么多种传输类型？**

报告分析指出，如果 MCP 只支持单一传输（如 HTTP），那么在许多场景下会变得不可用或低效：

- **本地工具场景**：使用 HTTP 意味着需要启动本地 HTTP 服务器并管理端口，这比简单的子进程通信（stdio）复杂得多
- **企业网络场景**：某些网络环境只允许 HTTP 出站流量，此时 WebSocket 或自定义协议会被防火墙阻止
- **IDE 集成场景**：IDE 插件与 Claude Code 通常运行在同一进程空间或共享内存通道，使用 stdio 或 HTTP 会造成不必要的序列化开销
- **官方托管场景**：Claude.ai 的服务需要统一的认证和路由层，不能依赖用户自行配置网络连接

因此，MCP 的多传输设计不是"为了复杂而复杂"，而是为了**最大化协议的适用性**——让不同环境都能找到最合适的集成方式。

**这个结论能帮你做什么？**

选择传输类型时，不要只看"哪个最新"或"哪个功能最多"，而要根据你的**部署环境约束**来决定：如果 Server 和 Claude Code 在同一台机器上，stdio 最简单；如果需要跨机器通信，http 或 sse 最通用；如果需要双向实时推送，websocket 最合适；如果是 IDE 插件，sse-ide/ws-ide 可以复用现有连接。选择错误的传输类型会导致不必要的配置复杂度和连接问题。

#### `stdio`（标准输入输出）

- **是什么**：MCP Server 作为一个本地子进程启动，Claude Code 通过 stdin/stdout 与其通信（JSON-RPC over stdio）。
- **适用场景**：本地工具、开发脚本、不需要网络访问的服务。
- **优点**：最简单，无需网络配置，适合个人开发和快速原型。
- **缺点**：只能本地运行，无法跨机器访问；Server 生命周期与 Claude Code 绑定。

**典型配置**：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
    }
  }
}
```

#### `sse`（Server-Sent Events）

- **是什么**：基于 HTTP 的 Server-Sent Events 传输，Server 通过持久连接向 Claude Code 推送事件。
- **适用场景**：远程服务、需要 Server 主动推送的场景（如实时数据更新）。
- **优点**：基于标准 HTTP，穿透防火墙容易；支持 Server 主动推送。
- **缺点**：单向推送（Server→Client），Client 调用 Server 需要额外机制。

#### `http`（HTTP POST）

- **是什么**：基于标准 HTTP POST 请求的传输，每次调用是一个独立的 HTTP 请求。
- **适用场景**：无状态服务、云函数、RESTful 风格的 MCP Server。
- **优点**：最通用，任何支持 HTTP 的框架都可以实现；易于负载均衡和水平扩展。
- **缺点**：每次调用有连接建立开销；不支持 Server 主动推送。

#### `websocket`（WebSocket）

- **是什么**：基于 WebSocket 的全双工通信。
- **适用场景**：需要双向实时通信的复杂服务。
- **优点**：全双工、低延迟、支持 Server 主动推送。
- **缺点**：配置复杂，需要处理连接生命周期；某些网络环境（如代理、防火墙）可能阻止 WebSocket。

#### `sdk`（SDK 集成）

- **是什么**：通过编程语言 SDK（如 Python SDK、TypeScript SDK）直接集成的 MCP Server。
- **适用场景**：需要深度定制的 Server、已有代码库集成。
- **优点**：最灵活，可以完全控制 Server 行为；易于调试和测试。
- **缺点**：需要编程实现；部署和维护成本较高。

### 15.2.2 IDE 专用传输

#### `sse-ide` / `ws-ide`

- **是什么**：专门为 IDE 插件（VS Code、JetBrains）设计的 SSE/WebSocket 变体。
- **适用场景**：IDE 扩展需要与 Claude Code 共享 MCP 连接时。
- **特点**：与 IDE 的通信管道集成，可以复用 IDE 已有的网络连接和认证机制。

### 15.2.3 内部代理传输

#### `claudeai-proxy`

- **是什么**：Claude.ai 官方服务的代理传输，用于连接托管在 Claude.ai 上的 MCP Server。
- **适用场景**：使用 Claude.ai 官方生态中的 MCP 服务。
- **特点**：自动处理认证和路由，用户无需关心底层连接细节。

### 15.2.4 传输类型选择指南

| 场景 | 推荐传输 | 理由 |
|------|---------|------|
| 本地文件系统工具 | `stdio` | 简单、无需网络 |
| 团队内部共享服务 | `http` 或 `sse` | 易于部署、穿透防火墙 |
| 需要双向实时通信 | `websocket` | 全双工、低延迟 |
| 深度定制 Server | `sdk` | 完全控制 |
| IDE 插件集成 | `sse-ide` / `ws-ide` | 复用 IDE 连接 |
| Claude.ai 官方服务 | `claudeai-proxy` | 自动认证 |

---

## 15.3 MCP 配置范围

### 15.3.1 多 Scope 合并

与 Skills 类似，MCP Server 的配置也支持多层级，不同层级的配置会**合并**而非覆盖。

| Scope | 配置位置 | 生效范围 |
|-------|---------|---------|
| **Enterprise** | 企业部署配置 | 组织内所有用户 |
| **User** | `~/.claude/mcp.json` 或 `~/.claude/settings.json` 的 `mcpServers` | 当前用户的所有项目 |
| **Project** | `.claude/mcp.json` 或 `.claude/settings.json` 的 `mcpServers` | 当前项目 |
| **Local** | 本地临时配置 | 当前会话 |

**合并逻辑**：

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 MCP 配置时指出，Claude Code 在启动时会**合并**所有生效 scope 的 MCP Server 配置，而不是简单的高优先级覆盖低优先级。这意味着：

- 如果用户级配置定义了 Server A，项目级配置定义了 Server B，那么两者**同时生效**。
- 如果两个 scope 定义了**同名 Server**（如都叫 `postgres`），则按优先级解析（Project > User > Enterprise），高优先级的配置覆盖低优先级的同名 Server。

**为什么会有合并而非覆盖的设计？**

因为 MCP Server 之间通常是**互补关系**而非**竞争关系**。用户可能希望：

- 在个人配置中定义通用的 MCP Server（如文件系统工具、Git 工具）
- 在项目配置中定义项目特定的 MCP Server（如项目的数据库、项目的内部 API）
- 两者同时可用，不需要在每个项目中重复定义通用 Server

### 15.3.2 额外来源

除了显式配置，MCP Server 还可以来自以下渠道：

**Plugin-contributed**：

插件可以在安装时自动注册 MCP Server。例如，一个 PostgreSQL 插件可能自动贡献一个 `postgres` MCP Server，用户无需手动配置连接参数。

**claude.ai servers**：

Claude.ai 官方提供的托管 MCP Server（如网络搜索、代码分析等），通过 `claudeai-proxy` 传输自动连接。

### 15.3.3 运行时合并逻辑

Claude Code 在启动时的 MCP 配置加载顺序：

1. 加载 Enterprise scope 的配置
2. 加载 User scope 的配置（同名 Server 覆盖 Enterprise）
3. 加载 Project scope 的配置（同名 Server 覆盖 User）
4. 加载 Local scope 的配置（同名 Server 覆盖 Project）
5. 加载 Plugin-contributed Server
6. 加载 claude.ai 官方 Server
7. 合并所有 Server 的工具定义，构建最终工具池

---

## 15.4 工具池组装与 MCP

### 15.4.1 assembleToolPool() 五步流水线

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Tool Pool Assembly（cc.pdf Section 6.2）时指出，Claude Code 在每次对话前都会执行一个**五步流水线**来组装可用的工具池。理解这个流水线对于理解 MCP 工具如何融入 Claude 的工作流程至关重要。

```
┌─────────────────────────────────────────────────────────────────────┐
│                    assembleToolPool() 五步流水线                     │
│                                                                      │
│  Step 1: 收集 Built-in Tools                                        │
│     └── Read, Edit, Write, Bash, Glob, Grep 等内建工具              │
│                                                                      │
│  Step 2: 收集 Bundled Skill Tools                                   │
│     └── 内嵌 Skills 贡献的工具（如 /code-review）                    │
│                                                                      │
│  Step 3: 收集 File System Skill Tools                               │
│     └── ~/.claude/skills/ 和项目 .claude/skills/ 中的工具          │
│                                                                      │
│  Step 4: 整合 MCP Tools ◄── MCP Server 在此步骤注入                 │
│     └── 从所有配置的 MCP Server 获取工具定义                         │
│                                                                      │
│  Step 5: 应用预过滤规则                                             │
│     └── Deny rules、权限检查、去重                                  │
└─────────────────────────────────────────────────────────────────────┘
```

**为什么 MCP 工具在 Step 4 才被整合？**

报告作者指出，这个顺序反映了**信任层级**和**稳定性层级**：

- **Built-in tools**（Step 1）是 Claude Code 核心的一部分，经过最严格的测试，具有最高稳定性。
- **Bundled skills**（Step 2）是随 Claude Code 分发的官方 Skills，信任度次于 built-in。
- **File system skills**（Step 3）是用户自定义的 Skills，信任度取决于用户的配置质量。
- **MCP tools**（Step 4）来自外部系统，信任度最低——它们可能不稳定、可能有延迟、可能在运行时不可用。

将 MCP 工具放在后面整合，确保了即使 MCP Server 不可用或响应缓慢，Claude Code 的核心功能（built-in tools 和 skills）仍然可以正常工作。

### 15.4.2 MCP 工具的去重规则

当多个来源提供了**同名工具**时，Claude Code 采用以下去重策略：

**Built-in tools 优先于 MCP tools**：

如果某个 MCP Server 提供了一个名为 `Read` 的工具（与 built-in `Read` 同名），built-in 版本优先，MCP 版本被忽略或重命名。

**为什么会有这个规则？**

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析工具池组装机制时指出，这个去重规则是 Claude Code **安全模型**的核心组成部分。Built-in tools（如 `Read`、`Edit`、`Write`、`Bash`）是 Claude Code 经过严格测试的内建能力，它们的行为是确定性的、已知且受控的。而 MCP tools 来自外部系统，其质量和安全性参差不齐。

报告作者强调，如果允许 MCP Server 覆盖 built-in tools，将会产生一个严重的**供应链攻击面**：一个恶意的 MCP Server 可以注册名为 `Read` 的工具，当 Claude 调用 "读取文件" 时，实际上执行的是恶意代码（如将文件内容发送到外部服务器）。通过强制 built-in tools 优先，Claude Code 确保了核心文件操作永远不会被外部 Server 劫持。

**这个结论能帮你做什么？**

这意味着你在为 MCP Server 命名工具时，**应该避免使用与 built-in tools 相同的名称**（如 `Read`、`Edit`、`Write`、`Bash`、`Glob`、`Grep` 等）。即使你不小心使用了同名，Claude Code 也会自动忽略你的 MCP 工具而非覆盖内建工具——这可能导致你的集成"不工作"却没有任何明显错误。最佳实践是为 MCP 工具使用**业务语义化的名称**（如 `query_database`、`send_email`、`search_logs`），这样既能避免冲突，又能帮助 Claude 更好地理解工具的用途。

### 15.4.3 完全限定命名

为了避免命名冲突，MCP 工具在内部使用**完全限定名**（fully qualified name）：

```
mcp__<server_name>__<tool_name>
```

例如，一个名为 `postgres` 的 MCP Server 提供的 `query` 工具，其完全限定名为 `mcp__postgres__query`。

**这个命名策略的价值**：

1. **避免冲突**：即使两个不同的 MCP Server 都提供了名为 `query` 的工具，它们的完全限定名也不同（`mcp__postgres__query` vs `mcp__mysql__query`）。
2. **权限控制**：deny rules 可以针对完全限定名进行精确匹配，也可以针对 server 级别进行批量匹配（见 15.7 节）。
3. **调试追踪**：当 Claude 调用某个 MCP 工具时，完全限定名清楚地表明了工具来自哪个 Server。

### 15.4.4 预过滤：Deny Rules 在组装阶段移除工具

在 Step 5 中，Claude Code 会应用 deny rules 对工具池进行预过滤。如果某个工具匹配了 deny rule，它在组装阶段就被移除，**不会进入最终的工具池**。

这意味着：

- 被 deny 的 MCP 工具对 Claude 是**不可见的**——Claude 甚至不知道这个工具存在。
- 这与 Hooks 的 `PreToolUse` deny 不同：`PreToolUse` 是在 Claude 已经决定调用工具后拦截，而 deny rules 是在工具池组装时就移除工具。

**两种拦截方式的对比**：

| 维度 | Deny Rules（预过滤） | PreToolUse Hook（运行时拦截） |
|------|---------------------|------------------------------|
| **拦截时机** | 工具池组装时 | 工具调用执行前 |
| **Claude 是否知道工具存在** | ❌ 不知道 | ✅ 知道，但被阻止 |
| **拦截成本** | 零（一次性过滤）| 每次调用都触发 |
| **灵活性** | 低（基于名称/模式匹配）| 高（基于运行时条件）|
| **适用场景** | 永久性禁止某类工具 | 有条件地禁止（如只在生产环境）|

---

## 15.5 Channels 与 MCP 的区别

Claude Code 提供了两种与外部系统集成的机制：MCP 和 Channels。理解它们的区别对于选择正确的集成方式至关重要。

### 15.5.1 核心区别：拉取 vs 推送

| 维度 | MCP | Channels |
|------|-----|----------|
| **方向** | **拉取（Pull）**——Claude 主动调用外部系统 | **推送（Push）**——外部系统主动通知 Claude |
| **触发方** | Claude 的推理过程 | 外部事件（CI 失败、消息、监控报警）|
| **典型用途** | 查询数据、执行操作 | 接收通知、事件驱动响应 |
| **实时性** | 按需调用 | 实时推送 |

### 15.5.2 为什么会有两种机制？

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Channels 子节时指出，这两种机制解决的是**互补问题**：

- **MCP 解决"Claude 需要外部能力"的问题**：当 Claude 在推理过程中发现"我需要查询数据库"、"我需要发送邮件"时，它通过 MCP 调用外部服务。
- **Channels 解决"外部世界需要 Claude 注意"的问题**：当 CI 构建失败、当监控报警触发、当飞书消息到达时，外部系统通过 Channels 将事件推送给 Claude，Claude 可以主动响应这些事件。

### 15.5.3 配合模式

在实际工作流中，MCP 和 Channels 经常**配合使用**：

**示例：CI 失败处理流程**

1. **Channels 阶段**：CI 系统通过 Channel 推送 "构建失败" 事件给 Claude
2. **Claude 响应**：Claude 收到通知后，决定调查失败原因
3. **MCP 阶段**：Claude 通过 MCP 调用日志查询工具获取 CI 日志、通过 MCP 调用代码分析工具定位问题
4. **Channels 阶段**：Claude 通过 Channel 发送修复结果或请求人工确认

**示例：监控报警响应**

1. **Channels 阶段**：监控系统通过 Channel 推送 "CPU 使用率超过阈值" 报警
2. **Claude 响应**：Claude 收到报警后，决定诊断问题
3. **MCP 阶段**：Claude 通过 MCP 调用服务器诊断工具（查看进程、分析日志）
4. **MCP 阶段**：Claude 通过 MCP 调用运维工具（重启服务、扩容）

**一句话概括**：Channels 是"外部世界敲门告诉 Claude 有事发生"，MCP 是"Claude 拿起电话打给外部世界请求帮助"。

---

## 15.6 MCP 专用工具

除了通过 MCP Server 暴露的自定义工具，Claude Code 还提供了两个**内建 MCP 专用工具**，用于与 MCP 资源交互。

### 15.6.1 ListMcpResourcesTool

- **功能**：列出所有可用的 MCP 资源（Resources）。
- **使用场景**：当你想知道某个 MCP Server 提供了哪些可读取的资源时。
- **返回值**：资源列表，每个资源包含 URI、名称、描述等信息。

**示例**：

```json
// 调用 ListMcpResourcesTool
{
  "server": "postgres"
}

// 返回
{
  "resources": [
    {
      "uri": "postgres://schema/users",
      "name": "Users Table Schema",
      "description": "用户表的结构定义"
    },
    {
      "uri": "postgres://schema/orders",
      "name": "Orders Table Schema",
      "description": "订单表的结构定义"
    }
  ]
}
```

### 15.6.2 ReadMcpResourceTool

- **功能**：读取指定的 MCP 资源内容。
- **使用场景**：当你需要获取某个资源的详细信息时（如数据库 schema、API 文档、配置文件模板）。
- **参数**：`server`（Server 名称）、`uri`（资源 URI）。

**示例**：

```json
// 调用 ReadMcpResourceTool
{
  "server": "postgres",
  "uri": "postgres://schema/users"
}

// 返回
{
  "content": "CREATE TABLE users (\n  id SERIAL PRIMARY KEY,\n  name VARCHAR(255),\n  email VARCHAR(255) UNIQUE\n);"
}
```

### 15.6.3 Resources & Prompts 的价值

**Resources** 提供了一种**结构化数据推送**的机制。与 Tools 不同，Resources 是只读的——它们不能被调用执行，但可以被读取以丰富 Claude 的上下文。

**典型使用场景**：

- **数据库 Schema**：MCP Server 将数据库的表结构作为 Resource 暴露，Claude 在生成 SQL 前读取 schema 以确保查询正确。
- **API 文档**：MCP Server 将 OpenAPI 规范作为 Resource 暴露，Claude 在调用 API 前读取文档以了解参数要求。
- **配置模板**：MCP Server 将项目的配置模板作为 Resource 暴露，Claude 在生成配置时参考模板。

**Prompts** 提供了一种**预定义提示模板**的机制。MCP Server 可以暴露标准化的提示模板，Claude 可以加载这些模板来执行特定任务。

**典型使用场景**：

- **标准化分析流程**：如 "请按照这个模板分析日志文件：1. 提取错误类型 2. 统计频率 3. 给出修复建议"
- **报告模板**：如 "请按照这个格式生成安全审计报告"

**为什么需要 Resources 和 Prompts？**

因为 Tools 虽然强大，但它们是**执行导向**的——"调用我来做某事"。而 Resources 和 Prompts 是**信息导向**的——"我有这些信息，你在做决策时可以参考"。它们让 MCP Server 不仅提供"能力"，还提供"知识"，从而帮助 Claude 做出更明智的决策。

---

## 15.7 MCP 权限与安全

### 15.7.1 Deny-First 规则匹配

MCP 工具的权限控制采用 **deny-first** 策略：默认允许所有 MCP 工具，除非显式配置 deny rule。

**规则匹配模式**：

Deny rules 支持以下匹配模式：

1. **完全限定名匹配**：`mcp__postgres__query` —— 精确匹配某个工具
2. **Server 级别匹配**：`mcp__postgres__*` —— 匹配某个 Server 的所有工具
3. **通配符匹配**：`mcp__*__query` —— 匹配所有名为 `query` 的 MCP 工具

**配置示例**（`settings.json`）：

```json
{
  "permissions": [
    {
      "action": "deny",
      "tool": "mcp__postgres__drop_table"
    },
    {
      "action": "deny",
      "tool": "mcp__production__*"
    }
  ]
}
```

### 15.7.2 Server-Level Rules

如果你想禁止整个 MCP Server 的所有工具，可以使用 server-level deny rule：

```json
{
  "permissions": [
    {
      "action": "deny",
      "tool": "mcp__untrusted_server__*"
    }
  ]
}
```

这比逐个工具 deny 更高效，也更容易维护。

### 15.7.3 PostToolUse 的 updatedMCPToolOutput

前文 14.4.1 提到的 `PostToolUse` hook 支持一个特殊字段 `updatedMCPToolOutput`，用于**修改 MCP 工具的输出结果**。

**使用场景**：

1. **敏感信息脱敏**：如果 MCP 工具返回了包含密码、密钥的输出，hook 可以在结果注入 Claude 上下文前将其脱敏。

2. **输出格式化**：将 MCP 工具的原始输出转换为更友好的格式。

3. **结果过滤**：移除输出中的冗余信息，只保留 Claude 需要的内容。

**示例**：

```json
{
  "event": "PostToolUse",
  "condition": "tool.name.startsWith('mcp__postgres__')",
  "action": {
    "type": "command",
    "command": "echo '{\"updatedMCPToolOutput\": \"${tool.output | sed 's/password=[^ ]*/password=***/g'}\"}'"
  }
}
```

这个 hook 在 MCP 数据库查询工具返回结果后，自动将 `password=xxx` 替换为 `password=***`。

---

## 15.8 安全边界（实务建议）

MCP 的强大能力也带来了安全风险——一个配置不当的 MCP Server 可能让 Claude 访问敏感数据或执行危险操作。以下是生产环境的安全实践建议。

### 15.8.1 只读账号原则

**永远为 MCP Server 配置只读数据库账号**。

| 风险 | 示例 |
|------|------|
| 如果 MCP 使用写权限账号 | Claude 可能意外执行 `DELETE`、`DROP TABLE`、`UPDATE` |
| 如果 MCP 使用只读账号 | 最坏情况只是查询到不该看的数据，不会破坏数据 |

**实施方法**：

```sql
-- 为 MCP 创建只读用户
CREATE USER mcp_readonly WITH PASSWORD 'strong_password';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
-- 不要授予 INSERT, UPDATE, DELETE, DROP 权限
```

如果需要写操作，**创建单独的 MCP Server**，使用更严格的权限控制，并在 deny rules 中限制其使用场景。

### 15.8.2 参数化 SQL 与查询白名单

**不要让 MCP Server 接受任意 SQL**。

| 方案 | 风险等级 | 说明 |
|------|---------|------|
| ❌ 接受任意 SQL | 极高 | Claude 可能生成危险的查询 |
| ✅ 预定义查询模板 | 低 | 只允许执行白名单中的查询 |
| ✅ 参数化查询 | 中 | 允许查询结构变化，但参数必须匹配预设模式 |

**推荐做法**：

```typescript
// 不推荐：接受任意 SQL
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const sql = request.params.arguments.sql; // 危险！
  const result = await db.query(sql);
  return { content: [{ type: "text", text: JSON.stringify(result) }] };
});

// 推荐：预定义查询模板
const ALLOWED_QUERIES = {
  "get_user_by_id": "SELECT * FROM users WHERE id = $1",
  "get_orders_by_user": "SELECT * FROM orders WHERE user_id = $1"
};

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const queryName = request.params.arguments.query_name;
  const params = request.params.arguments.params;
  
  if (!ALLOWED_QUERIES[queryName]) {
    throw new Error("Query not in whitelist");
  }
  
  const result = await db.query(ALLOWED_QUERIES[queryName], params);
  return { content: [{ type: "text", text: JSON.stringify(result) }] };
});
```

### 15.8.3 白名单与脱敏

**为 MCP 工具配置严格的 deny rules**：

```json
{
  "permissions": [
    {
      "action": "deny",
      "tool": "mcp__prod_db__*"
    },
    {
      "action": "deny",
      "tool": "mcp__*__delete_*"
    },
    {
      "action": "deny",
      "tool": "mcp__*__drop_*"
    }
  ]
}
```

**使用 `PostToolUse` hook 脱敏敏感字段**：

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "condition": "tool.name.startsWith('mcp__')",
      "action": {
        "type": "command",
        "command": "python3 /path/to/desensitize.py"
      }
    }
  ]
}
```

### 15.8.4 封装成业务工具，不要给万能接口

**最高级的安全策略是"最小能力原则"**。

不要给 Claude 一个"万能数据库查询"工具。相反，将业务操作封装成**语义化的、受限的**工具：

| 不推荐 | 推荐 |
|--------|------|
| `query(sql: string)` —— 任意 SQL | `get_user_orders(user_id: string)` —— 只查订单 |
| `execute_command(cmd: string)` —— 任意命令 | `restart_service(service_name: string)` —— 只重启服务 |
| `read_file(path: string)` —— 任意文件 | `get_config(key: string)` —— 只读配置 |

**为什么这样做更安全？**

因为即使 Claude 的推理出现偏差，它也只能调用预定义的业务工具，无法执行超出工具语义范围的操作。一个名为 `get_user_orders` 的工具，无论 Claude 传入什么参数，都不可能删除数据。

### 15.8.5 审计与监控

**记录所有 MCP 工具调用**：

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "condition": "tool.name.startsWith('mcp__')",
      "action": {
        "type": "http",
        "url": "https://audit.company.com/mcp-calls",
        "method": "POST",
        "body": {
          "server": "${tool.name.split('__')[1]}",
          "tool": "${tool.name.split('__')[2]}",
          "input": "${tool.input}",
          "timestamp": "${now}"
        }
      }
    }
  ]
}
```

**监控异常调用模式**：

- 短时间内大量 MCP 调用
- 从未使用过的 MCP 工具被突然调用
- MCP 工具返回大量数据（可能是数据泄露信号）
