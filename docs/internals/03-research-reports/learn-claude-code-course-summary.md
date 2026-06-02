# learn-claude-code s01-s12 课程总结

> 来源：shareAI-lab/learn-claude-code  
> 整理时间：2026-05-16  
> 适用范围：ClaudeCode-Runtime 面试准备与架构理解

---

## 课程总览

这个仓库用 12 个递进式 session，从最核心的 `while True` 循环出发，逐步构建出一个完整的 Claude Code Python Runtime。每一层都在前一层基础上增加一个关键机制，最终覆盖 Agent Loop、工具系统、权限、Memory、Compact、Task、Streaming、Agent Teams、Background Agents、Autonomous Agents、Worktree 隔离等完整能力。

---

## s01: 最简 Agent Loop —— 一切的核心

**核心代码**: `while True` + `stop_reason == "tool_use"`

```python
while True:
    response = call_model(messages)
    if response.stop_reason != "tool_use":
        break
    for tool in response.tool_uses:
        result = execute_tool(tool)
        messages.append(tool_result(result))
```

**关键设计**:
- 模型不是"记住"历史，而是每次重新喂入完整的 `messages` transcript
- `stop_reason == "tool_use"` 是继续循环的唯一条件
- 工具结果以 `tool_result` 消息回写给模型，形成"调用-观察-再决策"的闭环

**面试要点**: 这个循环是 Claude Code 的本质。它不是聊天机器人，而是"观察-行动-再观察"的自主代理。

---

## s02: 工具系统 —— 如何让模型动手

**核心模式**: `TOOL_HANDLERS = {name: handler}` 映射表

**三层工具**:
1. **读工具** (Read, Glob, Grep) — 信息收集，并发安全
2. **写工具** (Write, Edit, Bash) — 修改状态，需要权限
3. **编排工具** (Agent, Task, Skill) — 调度其他代理

**关键设计**:
- 每个工具是独立的 Python class，实现 `name`, `description`, `input_schema`, `execute()`
- 工具描述直接注入 system prompt，模型据此决定调用哪个
- 并发安全工具可并行执行（最多 10 个），状态修改工具串行执行

---

## s03: 权限系统 —— 安全边界

**三种模式**:
- `DEFAULT`: 所有写操作都问用户
- `ACCEPT_EDITS`: 自动批准文件编辑
- `BYPASS`: 全部自动执行（危险）

**实现**: `PermissionContext` 白名单机制
- 未知工具默认 `ASK`，安全优先
- 用户可标记"always allow"
- 非交互环境（如 CI）遇到 `ASK` 直接失败，防止挂起

---

## s04: 文件操作与编辑 —— 代码是怎么被改写的

**Read**: 按行读取，加行号返回，支持 `offset/limit` 分页
**Write**: 完整覆盖，带 checkpoint 快照
**Edit**: 字符串替换，`old_string` 必须唯一匹配

**关键设计**:
- 每次编辑前自动 checkpoint（文件快照），支持 `/rewind` 回滚
- Bash 命令的副作用（如 `git commit`）不在 checkpoint 范围内
- 图片文件转 base64 image block 给模型

---

## s05: 上下文注入 —— Memory 与 CLAUDE.md

**CLAUDE.md 注入**:
- 从当前目录向上搜索 `.claude/CLAUDE.md` 或 `CLAUDE.md`
- 作为 system prompt 的最后一段注入（靠后位置，影响更强）
- 支持 `@path` import 引用外部文件

**Memory 注入**:
- 路径: `~/.claude/projects/{SHA256(cwd)[:12]}/memory/`
- `MEMORY.md` 是索引文件，启动时注入 system prompt
- 模型通过索引知道有哪些 memory，再通过 Read 工具读取具体内容

**Skill 注入**:
- 与 CLAUDE.md 不同，Skill 作为 `UserMessage` 追加到 transcript
- 触发后不是修改 system prompt，而是像"用户贴了一段任务说明"

---

## s06: Compact 机制 —— 长对话如何不爆炸

**三种 compact**:
1. **Micro Compact**: 静默替换旧消息为占位符，无 LLM 调用
2. **Auto Compact**: 到达阈值（~95% context window）时自动摘要
3. **Manual Compact**: 用户通过 `/compact` 主动触发

**核心逻辑**:
```text
[Previous conversation summary]
+ 最近 4 轮 user-assistant 对话（保留原文）
```

**关键澄清**:
- Compact 压缩的是 `messages`（喂给模型的上下文），不是磁盘文件
- 代码始终安全，compact 只影响对话历史
- 一万行代码的编辑不会导致 compact，compact 只针对对话 transcript

---

## s07: Task System —— 结构化任务管理

**TodoManager**:
- 最多 1 个 `in_progress`，强制单线程专注
- `pending` -> `in_progress` -> `completed`/`failed`
- 无进度自动 nag 提醒

**TaskManager (DAG)**:
- 文件-based JSON 任务图
- `blockedBy` 依赖关系
- 任务完成自动 unblock 下游
- 支持子任务嵌套

**任务 JSON 文件**:
- 让 LLM 知道"有哪些任务、进度如何、阻塞在哪"
- 不是给人看的，是给模型读的上下文

---

## s08: Streaming 与并发 —— 一边输出一边执行

**核心机制**: `StreamingToolExecutor`

模型还没输出完，就识别出工具调用并开始执行：
```python
# 在 SSE 流中实时解析 tool_use
if chunk.type == "tool_use":
    executor.add_tool(chunk)  # 立即启动执行
```

**并发控制**:
- 读工具（Read/Glob/Grep）可并行，上限 10 个
- 写工具（Write/Edit/Bash）串行，防止竞态

**关键澄清**:
- 模型本身是单线程的（一次只输出一个 token）
- Harness 可以并发执行工具（多个工具同时跑）
- 这是"思考串行、行动并行"的设计

---

## s09: Agent Teams —— 多代理协作

**核心概念**:
- **Leader Agent**: 主代理，分配任务
- **Teammate Agents**: 持久化子代理，各自有独立线程
- **Inbox 通信**: 每个代理有 JSONL 收件箱，异步消息传递

**通信协议**:
- `request_id` 关联请求与响应
- `SendMessage` 工具发送，`poll_inbox()` 轮询接收
- 关闭协议：发送 shutdown -> 等待 acknowledgment -> 确认关闭

**团队模式**:
- `request` 模式：Leader 分配任务，Teammate 执行后返回
- `plan_approval` 模式：Leader 提出计划，Teammate 审批（FSM 状态机）

---

## s10: Background Agents —— 后台并行任务

**核心机制**:
- `BackgroundAgentManager.spawn()` 创建后台任务
- 包装为 `asyncio.Task`，注册到 `TaskRegistry`
- REPL 每轮 `poll_completed()` 检查完成通知

**生命周期**:
```
spawn -> running -> completed/failed -> notify -> main agent consumes
```

**用途**: 让主代理不被阻塞，后台跑耗时任务（如搜索、测试、构建）

---

## s11: Autonomous Agents —— 自主运行的代理

**核心特征**:
- 不依赖用户输入，自主决策
- **IDLE 阶段**: 轮询 inbox + 扫描任务板 + 自动 claim 任务
- Compact 后自动重新注入身份提示（防止"我是谁"失忆）

**Recap 机制**:
- 紧凑模式下保留身份锚点
- 确保代理在上下文压缩后仍能记住自己的目标和角色

**触发条件**:
- 通常是 Background Agent 的进阶形态
- 或 Teammate Agent 在自主模式下运行

---

## s12: Worktree 隔离 —— 文件系统级别的并发安全

**核心设计**:
- 每个 Task 对应一个 Git worktree
- Control Plane: `.tasks/` 目录管理任务元数据
- Execution Plane: `.worktrees/` 目录实际执行代码

**WorktreeManager**:
- 自动创建/清理 worktree
- 基于分支或 commit 的隔离
- 任务完成后可选择合并回主分支

**EventBus**:
- Append-only JSONL 事件流
- 记录任务全生命周期（start/complete/fail）
- 支持崩溃恢复和审计追踪

**解决的核心问题**:
- 多代理同时写同一文件 -> 每个代理有自己的 worktree
- Git 冲突 -> 在 worktree 中独立开发，合并时处理
- 文件污染 -> 物理隔离，主分支始终干净

---

## 架构演进路线图

```
s01: Agent Loop       ← 核心循环
s02: 工具系统          ← 让模型能动手
s03: 权限系统          ← 安全边界
s04: 文件操作          ← 读写编辑实现
s05: 上下文注入        ← Memory + CLAUDE.md
s06: Compact          ← 长对话压缩
s07: Task System      ← 结构化任务
s08: Streaming        ← 并发执行
s09: Agent Teams      ← 多代理协作
s10: Background       ← 后台任务
s11: Autonomous       ← 自主决策
s12: Worktree         ← 文件隔离
```

---

## 与 Claude Code 官方的关系

| 维度 | 本仓库 (Python) | 官方 (TypeScript) |
|------|----------------|-------------------|
| 核心循环 | `while True` + `query_loop` | `query.ts` ~1400 行 |
| 部署面 | 终端 only | CLI/VS Code/JetBrains/Desktop/Web |
| 编译 | 纯 Python | 编译为 native binary |
| 企业功能 | 无 | Managed settings, policy enforcement |
| 远程执行 | 无 | SSH/Cloud 环境 |
| 背景代理 | 基础实现 | BackgroundAgentManager + TaskRegistry |

本仓库的价值：**用最清晰的 Python 代码，逐层解构官方产品的核心机制**。

---

## 面试高频考点

1. **Agent Loop 的退出条件是什么？**
   - `stop_reason != "tool_use"`，即模型不再调用工具时停止

2. **模型是怎么"记住"上下文的？**
   - 不是记住，是每轮重新喂入完整 `messages` transcript

3. **Compact 和 Session 的区别？**
   - Session 是磁盘快照（用于 resume），Compact 是上下文压缩（用于不超限）

4. **模型是单线程还是多线程？**
   - 模型单线程（token 逐个输出），Harness 可并发执行工具

5. **Agent Teams 如何避免文件冲突？**
   - s12 Worktree 隔离，每个代理独立工作空间

6. **Skill 和 CLAUDE.md 的区别？**
   - CLAUDE.md 进 system prompt；Skill 作为 UserMessage 进 transcript

7. **权限系统的默认策略？**
   - 未知工具默认 ASK，安全优先

---

## 推荐复习顺序

1. 先通读 `learn-claude-code-docs/zh/` 对应 session 的文档（概念层）
2. 再对照 `learn-claude-code-agents/s*_*.py` 代码实现（细节层）
3. 最后结合 `ClaudeCode-Runtime/src/cc-python-runtime/` 真实源码（工程层）
4. 交叉验证 `08_Skill-Outputs/` 中的调研报告（体系层）
