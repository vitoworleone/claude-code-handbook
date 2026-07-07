# 第八集：MCP 协议与扩展架构

大家好，欢迎回到 Claude Code 的深度解析系列。

Claude Code 内置了 60 多个工具——听起很多对吧？但开发者的需求是无限的。

有人想查 PostgreSQL 数据库，有人想访问公司内网的 API，有人要控制特定的硬件设备，还有人想调用自己训练的自定义模型。Claude Code 不可能把所有工具都内置进去。

所以它实现了 **MCP（Model Context Protocol）**——这是 Anthropic 定义的开放标准，让任何人都能给 Claude 添加新工具。

这集我们深入看看：MCP 到底是什么，它在 Claude Code 里是怎么集成的，以及 Anthropic 在集成时做了哪些重要的工程决策。

## MCP 基本概念

MCP 的架构很简单：它定义了 AI 模型和外部工具之间的通信标准。

三个核心角色：

| 角色 | 职责 |
|------|------|
| **MCP Server** | 运行中的服务，提供工具、资源和提示模板 |
| **MCP Client** | 消费这些工具的一方（Claude Code 本身就是 MCP Client） |
| **Transport** | 两边的通信方式 |

你可以在配置文件里同时配置多个 MCP 服务器。它们提供的工具都会被 Claude Code 自动发现，合并到工具列表里。Claude 调用时就像调用内置工具一样。

## 四种传输层（Transport）

选择正确的传输层直接影响 MCP 服务器的部署方式和运维体验。

| 传输层 | 机制 | 优点 | 缺点 |
|--------|------|------|------|
| **Stdio** | 子进程，标准输入输出 | 零配置，进程隔离，安全 | 无持久化状态，随 Claude Code 启停 |
| **SSE** | HTTP 长连接，Server-Sent Events | 独立进程，状态持久，可云部署 | 需网络连接，需认证 |
| **Streamable HTTP** | HTTP 流式响应 | 适合流式场景 | 复杂度较高 |
| **WebSocket** | 双向实时通信 | 实时双向 | 较重 |

选择原则：**看你的 MCP 服务器在哪里。**
- 本地命令行工具 → Stdio
- 远程 API → SSE 或 HTTP
- 测试调试 → In-Process

### Stdio 深入

Claude Code 作为主进程，启动一个子进程运行 MCP 服务器，两者通过标准输入输出进行 **JSON-RPC** 格式的通信。

优点：零配置，不需要操心网络连接、端口、认证。进程隔离意味着安全性好——一个服务器崩溃不影响其他部分。

缺点：服务器生命周期完全由 Claude Code 控制。Claude Code 退出，服务器就退出。这意味着没法在服务器里维护跨会话的持久化状态。

### SSE 深入

适合需要持久化状态的场景。核心是 HTTP 长连接：

- 客户端发起 GET 请求到服务器，保持连接不断开。
- 服务器可以随时通过这个连接向客户端推送消息（事件流机制）。

因为状态维护在服务器端，不会因客户端重启而丢失。代价是需要处理网络连接、断线重连、认证机制。GitHub 的 MCP 服务就是用这种方式。

## MCP 工具集成：适配器模式

MCP 服务器提供的工具接口和 Claude Code 的内置工具接口不一样。怎么办？

**答案是适配器模式。** Claude Code 用 `wrapMcpTool` 函数把每个 MCP 工具包装成 Claude Code 的标准 Tool 接口。

### 名字转换

每个 MCP 工具的名字变成 `mcp__<server>__<tool>` 的格式。比如文件系统服务器的 `ReadFile` 工具变成 `mcp__filesystem__ReadFile`。

### call 方法

工具的 `call` 方法就是实际执行逻辑：调用 MCP 服务器获取结果，自动截断过大输出，或把大文件持久化到磁盘。

### checkPermissions 方法

权限检查。Claude Code 支持 Deny Rules 来禁用某些工具。这里有一个很巧妙的设计：

- `mcp__filesystem` → 禁用整个文件系统服务器的所有工具（**粗粒度**）
- `mcp__` → 禁用所有 MCP 工具
- `mcp__filesystem__ReadFile` → 只禁用特定工具（**细粒度**）

这种**通配符设计**实现了从粗粒度到细粒度的权限控制——全在一个命名规则里解决。

### Prompt Cache 考量

MCP 工具集成时还有一个重要考量：MCP 工具的描述也会出现在 API Prompt 里。如果 MCP 服务器频繁变化（新增/删除工具），会导致 Prompt Cache 失效。

因此 Claude Code 建议：**稳定使用的 MCP 工具配置好后就少动**，让 Prompt Cache 尽可能命中。
