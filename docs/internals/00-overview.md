# Claude Code 运行时：项目总览

> 本仓库是对 Claude Code 一类编程 Agent 运行时机制的系统性拆解与重建。

---

## 项目定位

ClaudeCode-Runtime 是一个 **Agent Harness 架构研究与复刻项目**。它的核心不是做一个 Claude Code 的克隆，而是理解、重建并讲明白 Agent 运行时的关键机制。

它不是简单的 API wrapper。其价值在于理解和复现：

- Agent 查询循环（query loop）
- 流式工具执行模型
- 权限决策系统
- 记忆提取与召回
- 上下文压缩
- MCP 集成
- Hooks 与 Skills
- 会话持久化
- Agent Teams 协作

### 适合谁

- AI 全栈工程师（需要理解 Agent 运行时）
- AI 基础设施工程师
- Agent 应用工程师
- 能推理实现约束的 AI 产品经理

---

## 项目边界

### 包含范围

- Python 实现的 Claude Code 风格 Agent 运行时
- 运行时架构的源码阅读笔记
- 系统分析文档
- 单元测试与端到端测试

### 不包含

- 本地密钥文件
- 操作系统元数据
- 缓存目录
- 编译后的字节码
- 日志与本地数据库

---

## 代码现状

当前代码库是一个经过整理的 Python 运行时实现副本。核心待阅读模块：

- query loop
- streaming tool executor
- message models
- prompt assembly
- permission engine
- memory module
- compaction module
- MCP client
- hooks system
- skills system
- session persistence
- agent team orchestration

---

## 架构分层

本项目通过以下分层框架来阅读和扩展：

```
ClaudeCode-Runtime
  -> 项目总览与方向（本文档）
  -> 运行时核心（Runtime Core）
  ->  harness 表面层（Harness Surface）
  -> 长程状态（Long-Run State）
  -> 多 Agent 运行时（Multi-Agent Runtime）
  -> 参考实现
  -> 证据、评估、报告
```

### 运行时核心

运行时核心是让模型作为 Agent 行动的最小循环。

主要职责：

- 维护 transcript 作为真相来源
- 调用模型 provider
- 流式接收模型事件
- 检测 tool-use 请求
- 执行工具
- 将工具结果追加回 transcript
- 直到本轮完成

代表模块：`cc/core/query_loop.py`、`cc/core/query_engine.py`、`cc/api/claude.py`、`cc/models/messages.py`

### Harness 表面层

Harness 表面层将原始运行时循环转化为可用的编程 Agent 系统。

主要职责：

- CLI 与 REPL 生命周期
- 系统 prompt 组装
- 工具注册
- 权限检查
- Hook 执行
- MCP 工具集成
- Skill 加载与调用
- UI/事件渲染

代表模块：`cc/main.py`、`cc/tools/base.py`、`cc/tools/streaming_executor.py`、`cc/permissions/`、`cc/hooks/`、`cc/mcp/`、`cc/skills/`、`cc/prompts/`

### 长程状态

长程 Agent 工作需要超越单次模型调用的状态。

三个独立概念：

- **Session**：恢复当前工作对话
- **Compaction**：在上下文窗口中存活
- **Memory**：提取持久事实供未来回忆

代表模块：`cc/session/`、`cc/compact/`、`cc/memory/`

### 多 Agent 运行时

多 Agent 功能是 harness 的扩展，不是基础。

正确的依赖顺序：

```
stable transcript
  -> stable tool execution
  -> stable permissions
  -> stable session/task state
  -> stable background agents
  -> stable team runtime
```

代表模块：`cc/tools/agent/`、`cc/session/task_registry.py`、`cc/swarm/`

---

## 扩展原则

1. **偏好深度模块而非散落功能**：每个重要子系统应暴露清晰接口，隐藏内部策略。
2. **报告必须可追溯代码证据**：架构报告不应停留在抽象评论层面。
3. **分离运行时、研究与呈现**：保持代码实现、架构参考和证据评估的清晰分离。
4. **不将团队模式视为首要产品**：团队模式依赖于底层 harness 的正确性。
5. **将参考作为架构输入，而非复制目标**：外部项目应启发本项目的架构，而非被一对一复制。

---

## 推荐路线图

### 第一阶段：标准化 Harness 架构图

创建一张统一的架构图，连接：项目目录、源码模块、核心运行时流、扩展点、当前报告、参考项目。

### 第二阶段：证据与评估

填充证据层和评估层。

### 第三阶段：Harness 模块深化

优先改进最高杠杆的模块：权限决策模块、EventLog/Telemetry 模块、上下文存活模块、记忆流水线模块、MCP 适配器模块、团队运行时模块。

### 第四阶段：报告与面试包装

将架构工作转化为可交付物：项目论文、架构报告、角色特定解释、面试答辩 Q&A、逐模块图表、精选代码走读。

---

## 非目标

- 构建通用聊天机器人
- 仅构建 Claude API wrapper
- 从可视化产品 UI 开始
- 一对一复制 Claude Code
- 在底层运行时可观测之前扩展团队模式
- 写无法追溯源码证据的报告

---

## 最终定位

```text
ClaudeCode-Runtime is an Agent Harness reconstruction and architecture lab.

It studies how a coding-agent runtime is assembled from model streaming,
message protocol, tools, permissions, context management, memory, sessions,
skills, MCP, and multi-agent orchestration.

Its outputs are not only code, but also evidence, evaluation, architecture
reports, and interview-ready explanations.
```
