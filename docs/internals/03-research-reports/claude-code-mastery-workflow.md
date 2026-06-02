# Claude Code 完全掌握指南：从认知到工作流

> 定位：面向希望深度掌握 Claude Code 与 AI Coding 实践的开发者，将架构认知转化为日常生产力。

---

## 第一部分：必须掌握的八大核心知识域

---

### 1. Context Compiler：五层流水线决定"模型看到什么"

Claude Code 不是简单地把聊天记录发给 API，而是经过一套**五层 Context Compiler 流水线**：

```
System Prompt（11段动态拼装）
    → Context Injection（用户/系统上下文）
        → Attachments（40+ 类型自动提取）
            → Message Normalization（消息归一化 + 缓存边界切割）
                → API Call（最终请求体）
```

**掌握要点**：
- System Prompt 由 11 个 section 动态拼装，包含 tool descriptions、doing_tasks 规则、 cwd 文件列表等。Prompt 的变化会直接导致 cache break。
- `normalize_messages_for_api()` 在每次调用前执行，负责消息裁剪、tool result 清理、缓存边界插入。所有缓存控制逻辑集中于此。
- Attachments 支持 40+ 类型：本地文件、URL、粘贴板、图片、PDF、git diff 等。同一 attachment 在不同上下文中语义不同。
- **缓存边界** (`SYSTEM_PROMPT_DYNAMIC_BOUNDARY`) 是 cache key 的分割点，边界之上命中缓存，边界之下重新计算。

**源码锚点**：`src/services/contextCompiler.ts`、`src/utils/api.ts:321-435`

---

### 2. Prompt Cache：三级架构与 2 阶段击穿检测

Claude Code 实现了完整的**客户端 Prompt Cache 模拟**：

| 层级 | key 来源 | 命中策略 |
|------|----------|----------|
| global | `globalPrefixKey` | 最稳定，通常包含系统级常量 |
| org | `orgKey` | 组织级配置，变化频率中等 |
| null | 无 | 完全重新计算 |

**2 阶段击穿检测**：
1. **记录阶段**：`recordPromptState()` 在调用前记录 system prompt、工具集、上下文 fingerprint。
2. **检测阶段**：`checkResponseForCacheBreak()` 在收到响应后比对 12 种击穿原因，包括：工具集变化、文件系统变化、MCP 服务器变化、环境变量变化等。

**关键认知**：Prompt Cache 是**成本优化手段**，不是**功能保证**。即使 cache miss，业务逻辑也必须正确运行。但 cache hit 可以节省 50-90% 的 API 成本。

**源码锚点**：`src/services/api/promptCacheBreakDetection.ts`

---

### 3. Compact 机制：四层防御体系

上下文压缩不是"删旧消息"那么简单，而是**四层防御体系**：

| 层级 | 触发时机 | 策略 | 是否 lossy |
|------|----------|------|-----------|
| Microcompact | 单轮工具结果过多 | 删除已消费的 tool result，保留引用 | 否 |
| Cached Microcompact | 同上，利用 `cache_edits` API | 删除不 invalidate 缓存前缀 | 否 |
| Snip | 消息体过大 | 截断长文本，保留前后文 | 是 |
| Autocompact | Token 接近阈值（200K - 13K buffer） | 保留最近 4 轮，摘要更早历史 | 是 |
| Context Collapse | API 返回 prompt_too_long | 应急压缩，每轮限 1 次 | 是 |

**双防御策略**：
- **事后防御**：`COMPACT_SYSTEM_PROMPT` 指导摘要器保留关键决策、文件路径。
- **事前防御**：`SUMMARIZE_TOOL_RESULTS` 指导模型把关键信息写入自己回复，防止原始 tool result 被压缩丢弃。

**关键参数**：`AUTOCOMPACT_BUFFER_TOKENS = 13_000`，预留 13K token 缓冲，避免 API 端因估算误差触发 PTL。

**源码锚点**：`src/services/compact/microCompact.ts`、`src/services/compact/autoCompact.ts:62`、`src/services/compact/compact.ts:122-131`

---

### 4. Tool 系统：能力对象而非函数映射

Claude Code 的 Tool 是**带安全/并发/UI 语义的能力对象**：

```typescript
interface Tool {
  name: string
  description: string
  inputSchema: JSONSchema
  isReadOnly: boolean      // 是否只读
  isDestructive: boolean   // 是否具有破坏性
  isConcurrencySafe: boolean // 是否可并发执行
  renderToolUseMessage?: (args) => string    // UI 展示：调用时
  renderToolResultMessage?: (result) => string // UI 展示：结果时
}
```

**三层执行栈**：
```
Semaphore 并发控制（安全工具并行 max 10，非安全独占）
    → PreToolUse Hook（用户自定义拦截）
        → Permission Check（权限门控）
            → Actual Execute（实际执行）
```

每层都可短路返回 `ToolResult(is_error=True)`。

**StreamingToolExecutor 核心创新**：在流式响应期间，当 ToolUseBlock 解析完成时**立即启动工具执行**，不等完整响应结束。实测可减少 30-50% 工具等待时间。

**源码锚点**：`src/Tool.ts:402-433`、`src/services/tools/StreamingToolExecutor.ts`

---

### 5. 权限系统：三级模式 + 白名单安全模型

| 模式 | 读操作 | 编辑操作 | 命令执行 | 场景 |
|------|--------|----------|----------|------|
| BYPASS | 自动 | 自动 | 自动 | CI/CD，完全信任 |
| ACCEPT_EDITS | 自动 | 自动 | 需确认 | 推荐日常交互模式 |
| DEFAULT | 自动 | 需确认 | 需确认 | 最安全模式 |

**白名单安全模型**：
- `READ_ONLY_TOOLS`：所有模式下自动允许。
- `EDIT_TOOLS`：ACCEPT_EDITS 模式下自动允许。
- **不在白名单的工具**：始终需确认。
- **未知工具默认 ASK**：新增工具不会意外绕过安全。

**拒绝追踪**：连续拒绝 3 次或总计 20 次后，自动降级权限模式。

**源码锚点**：`src/utils/permissions/denialTracking.ts:12-15`、`src/permissions.ts`

---

### 6. Memory 系统：四层记忆 + Coalescing 提取

| 层级 | 时间范围 | 持久性 | 内容 |
|------|----------|--------|------|
| Auto Memory | 跨会话（天/周） | 文件系统 | 用户偏好、项目上下文、长期协作信息 |
| Session Memory | 当前会话（分钟/小时） | 内存 | 当前会话摘要，辅助 compact |
| Agent Memory | 某类 Agent 专属 | 文件系统（按 scope） | user/project/local 三 scope |
| Team Memory | 团队共享 | 文件系统 | 团队共享知识同步 |

**存储模型**：每条记忆单独 markdown 文件，`MEMORY.md` 只维护索引链接。硬截断保护：200 行 / 25KB 上限。

**Coalescing 提取策略**（区别于 Debounce）：
- Debounce 可能丢弃中间状态。
- Coalescing 设置 dirty 标记，当前提取完成后**立即重跑**，保证最终状态一定被扫描。

**源码锚点**：`src/memory/`、`src/services/memory/extraction.ts`

---

### 7. Agent Teams：文件系统邮箱 + contextvars 隔离

**三个角色**：
| 角色 | System Prompt | 工具集 |
|------|---------------|--------|
| Team Lead | 完整版（11 段拼装） | 全部工具 |
| Teammate | DEFAULT_AGENT_PROMPT + TEAMMATE_ADDENDUM | 排除 Agent/TeamCreate/TeamDelete/AskUserQuestion |
| Coordinator | COORDINATOR_SYSTEM_PROMPT + 完整版 | 全部（但 prompt 指导只用 Agent/SendMessage/TaskStop） |

**通信机制**：文件系统邮箱 `~/.claude/teams/{team}/inboxes/{agent}.json`
- 零外部依赖、跨进程持久化、低频率场景可接受无锁设计。

**身份隔离**：同一进程内多个 `asyncio.Task` 并发运行，`contextvars` 提供协程级身份隔离，无需多进程开销。

**Coordinator 四阶段工作流**：
```
Research（并行工作者调研）
    → Synthesis（协调器汇总分析）
        → Implementation（工作者执行修改）
            → Verification（验证结果）
```

**源码锚点**：`src/tools/AgentTool/AgentTool.tsx`、`src/utils/teammateMailbox.ts`、`src/coordinator/coordinatorMode.ts`

---

### 8. 成本模型：Session-Global 而非 Per-Agent

Claude Code 的成本追踪是**会话级全局**的：

```typescript
// src/bootstrap/state.ts:557-564
export function addToTotalCostState(cost: number): void {
  totalCostState += cost
  // ... 更新 UI 显示
}
```

**关键认知**：
- 所有 Agent（主 Agent + 子 Agent）的调用成本都累加到同一个 `totalCostState`。
- **没有内置的 per-agent 成本隔离**。如果需要按 Agent 核算成本，需要自行在 Agent 调用前后记录。
- Token 预算采用**双轨制**：客户端 `tokenBudget.ts`（硬限制）+ API `taskBudget`（软提示）。

**Token 估算策略**：不加载 ~2MB BPE 词表，采用**字节除法**（O(n) 零开销），用 13K buffer 补偿估算误差。

**源码锚点**：`src/cost-tracker.ts`、`src/bootstrap/state.ts:557-564`

---

## 第二部分：工作流集成——将认知转化为生产力

---

### 2.1 日常编码工作流

**场景**：增量开发、功能实现、小重构。

**推荐配置**：
- 权限模式：ACCEPT_EDITS（自动编辑，执行命令需确认）
- 紧凑策略：依赖 Autocompact 自动触发
- 记忆策略：让 Auto Memory 沉淀项目约定

**最佳实践**：
1. **启动时加载上下文**：使用 `/load` 或 attachment 加载相关文件，而非让模型自行搜索。
2. **明确指令边界**："修改 `src/utils/api.ts` 中的 `splitSysPromptPrefix` 函数，添加对 custom prefix 的支持" 优于 "改一下缓存逻辑"。
3. **利用 Read-before-Edit 校验**：Claude Code 在编辑前会读取文件内容（`FileEditTool.ts:275-287`），如果文件与预期不符会报 Error 6。确保你的编辑基于最新文件状态。

**示例对话**：
```
User: 在 src/services/api.ts 中添加一个函数 `checkCacheHealth()`,
      遍历所有缓存条目，删除过期的。
      参考 src/services/cache.ts 中的过期判断逻辑 [attach: src/services/cache.ts]
```

---

### 2.2 大规模重构工作流

**场景**：跨模块重构、接口迁移、目录重组。

**推荐配置**：
- 权限模式：ACCEPT_EDITS（减少确认干扰）
- 工具策略：优先使用 `AgentTool` 并行处理多个子目录
- 记忆策略：在 Session Memory 中记录重构进度

**最佳实践**：
1. **拆分任务**：不要让一个 Agent 处理全部重构。使用 Coordinator 模式将任务拆分为 Research → Synthesis → Implementation → Verification 四阶段。
2. **并行执行**：对独立的模块，派生多个 Teammate Agent 并行处理。每个 Agent 有独立的 cache 和成本核算（见第三部分）。
3. **中间检查点**：每完成一个子模块，让 Lead Agent 汇总进度，更新 Session Memory。

**工作流模板**：
```
Phase 1 - Research:  "分析 src/legacy/ 目录，列出所有依赖 `oldAPI` 的文件"
Phase 2 - Plan:      "制定迁移计划：先迁移 utils，再迁移 services，最后迁移 controllers"
Phase 3 - Implement: [并行] Agent A 迁移 utils，Agent B 迁移 services
Phase 4 - Verify:    "运行测试套件，检查是否有遗漏的 oldAPI 引用"
```

---

### 2.3 Bug 修复工作流

**场景**：定位并修复生产 Bug。

**推荐配置**：
- 权限模式：DEFAULT（编辑和执行都需确认，防止误操作）
- 上下文策略：加载 error log、相关测试文件、git blame 信息
- 工具策略：使用 `BashTool` 运行复现命令，使用 `ReadTool` 逐步排查

**最佳实践**：
1. **复现优先**：先写一个最小复现脚本，确认 Bug 可复现。
2. **二分定位**：对大型代码库，先让 Agent 读取相关模块的入口文件，逐步缩小范围。
3. **修复后验证**：修复后运行原复现脚本 + 相关测试用例，确认无回归。

**示例对话**：
```
User: [attach: error.log] 这个报错发生在用户登录时。
      先读取 src/auth/ 目录下的入口文件，找出可能抛出此异常的位置。
      然后写一个最小复现脚本，验证问题。
      最后修复并运行测试。
```

---

### 2.4 探索性研究工作流

**场景**：学习新代码库、调研技术方案、阅读开源项目。

**推荐配置**：
- 权限模式：DEFAULT 或 BYPASS（只读操作）
- 上下文策略：加载 README、架构文档、关键配置文件
- 记忆策略：将关键发现写入 Auto Memory，供后续会话使用

**最佳实践**：
1. **先问架构，再问细节**："这个项目的主要模块有哪些？它们之间如何交互？" → "`src/core/engine.ts` 中的 `process()` 函数做了什么？"
2. **利用 Attachment 加载多个文件**：一次 attach 多个相关文件，让模型建立关联。
3. **记录发现**：对重要发现，显式要求模型写入记忆："请将这个架构决策记录到记忆中，标签 'project-architecture'。"

---

### 2.5 代码审查工作流

**场景**：Review PR、检查代码质量、安全审计。

**推荐配置**：
- 权限模式：DEFAULT（不自动执行任何修改）
- 上下文策略：加载 PR diff、相关测试、项目规范文档
- 工具策略：使用 `ReadTool` 检查代码，使用 `BashTool` 运行 linter

**最佳实践**：
1. **加载 diff 作为 attachment**：让模型直接分析 diff，而非读取整个文件。
2. **分维度审查**："请从以下维度审查：1) 功能正确性 2) 边界条件处理 3) 安全漏洞 4) 性能影响 5) 测试覆盖"。
3. **对发现的问题要求引用**："对每个问题，请引用具体的文件路径和行号。"

---

## 第三部分：高级技巧——榨取 Runtime 的每一分价值

---

### 3.1 并行执行与 Cache 隔离

**核心原理**：Claude Code 的 Prompt Cache 是 per-agent 隔离的。每个 Agent（包括 Teammate）有独立的 `previousStateBySource` Map（`src/utils/forkedAgent.ts:345-462`），缓存状态按 `agentId` 隔离。

**实践方法**：

#### 方法 A：Agent Teams 并行（推荐）

通过 Coordinator 或 Team Lead 派生多个 Teammate，每个处理独立任务：

```python
# 伪代码：Coordinator 工作流
team = create_team("refactor-team")

# 并行派生两个 Agent
task_a = spawn_agent("migrate-utils", "迁移 src/utils/ 到新 API")
task_b = spawn_agent("migrate-services", "迁移 src/services/ 到新 API")

# 等待结果（通过 mailbox 轮询）
results = await collect_results([task_a, task_b])

# Lead 汇总
synthesize_results(results)
```

**Cache 隔离效果**：
- Agent A 和 Agent B 的 system prompt 分别缓存，互不影响。
- 每个 Agent 的 cache key 独立计算，`previousStateBySource` 按 `agentId` 隔离（上限 10 个条目）。

#### 方法 B：同一进程内 Fire-and-Forget

在 Agent Tool 内部，async agents 使用 `asyncio.create_task()` 启动，sync agents 阻塞等待。

```typescript
// src/tools/AgentTool/AgentTool.tsx
if (isAsync) {
  // 创建独立 Task，不阻塞主循环
  const task = asyncio.create_task(runAgent(agentConfig))
  // 返回 task_id，后续通过 TaskStop 查询状态
} else {
  // 同步 Agent：阻塞等待结果
  const result = await runAgent(agentConfig)
}
```

**关键区别**：
- Async Agent：独立 AbortController，可独立中断，不阻塞主 Agent 的 query_loop。
- Sync Agent：共享 AbortController（通过 child/parent 引用），中断主 Agent 会级联中断子 Agent。

**成本影响**：
- 每个 Agent 的 API 调用独立计费，但累加到同一个 session-global `totalCostState`。
- 如果需要 per-agent 成本追踪，需在 Agent 调用前后手动记录。

---

### 3.2 Per-Agent 成本追踪

由于 Claude Code 的成本是 session-global 的，要实现 per-agent 核算需要**手动插桩**：

**方法：Hook 成本累加点**

```typescript
// 在 Agent 调用前记录基线
const baselineCost = getTotalCostState()

// 执行 Agent 任务
const result = await runAgent(agentConfig)

// 计算增量成本
const agentCost = getTotalCostState() - baselineCost
console.log(`Agent ${agentId} cost: $${agentCost.toFixed(4)}`)
```

**进阶：自定义 Cost Tracker**

在 Python 复刻中，可以为每个 Agent 维护独立成本计数器：

```python
class AgentCostTracker:
    def __init__(self):
        self.per_agent_costs: dict[str, float] = {}
    
    def record_usage(self, agent_id: str, input_tokens: int, output_tokens: int):
        cost = self.calculate_cost(input_tokens, output_tokens)
        self.per_agent_costs[agent_id] = self.per_agent_costs.get(agent_id, 0) + cost
    
    def get_report(self) -> dict[str, float]:
        return self.per_agent_costs.copy()
```

**注意**：此成本追踪仅包含 API 调用费用，不包含本地计算成本。

---

### 3.3 中断与任务拆分

**场景**：Agent 输出太长、执行卡住、陷入循环。

#### 中断机制

Claude Code 支持两种中断：

**A. 用户主动中断**（Ctrl+C / UI 按钮）：
```typescript
// src/QueryEngine.ts:1158-1160
interrupt() {
  this.abortController.abort()  // 触发 AbortSignal
  // 流式响应会立即终止
}
```

**B. 超时中断**：
- 流式空闲超时：90 秒无数据 → 自动中断
- 卡顿检测：30 秒无进展 → 提示用户
- 非流式回退：5 分钟无响应 → 报错

#### 中断后的最佳实践

**不要直接重试**。先诊断原因：

```
User: [中断] 你刚才的输出持续了 3 分钟，看起来卡住了。
      请告诉我：
      1. 你正在执行哪个工具？
      2. 当前处理到第几步？
      3. 你认为卡住的原因是什么？
```

模型会基于当前 transcript 回答，通常能指出：
- 某个工具调用超时（如大型 grep）
- 陷入了循环（如反复修改同一文件）
- 遇到了未预期的 API 错误

#### 任务拆分避免记忆过期

**核心问题**：如果让 Agent 执行一个非常长的任务（如"重构整个项目"），可能导致：
- 上下文窗口溢出，触发 compact，丢失早期决策
- 执行时间过长，记忆提取（coalescing）错过中间状态
- API 调用链过长，单点失败导致全部重来

**拆分策略**：

1. **按模块拆分**：
   ```
   不要："重构 src/ 下所有文件"
   要：  "先重构 src/utils/，完成后我让你继续重构 src/services/"
   ```

2. **按阶段拆分**：
   ```
   Phase 1: "列出所有需要修改的文件"
   Phase 2: "修改文件 A、B、C"
   Phase 3: "运行测试验证"
   ```

3. **显式检查点**：
   ```
   "每修改 3 个文件后，停下来汇报进度，确认后再继续。"
   ```

**记忆管理配合**：
- 在每个检查点，显式要求模型将进度写入 Session Memory：
  ```
  "请将当前进度记录到 session memory：
   - 已完成：src/utils/api.ts, src/utils/cache.ts
   - 待完成：src/services/auth.ts, src/services/db.ts
   - 遇到的问题：utils 和 services 之间有循环依赖"
  ```

---

### 3.4 Token 预算管理

**双轨预算系统**：

| 层级 | 机制 | 行为 |
|------|------|------|
| 客户端 | `tokenBudget.ts` | 硬限制，超出时抛出异常 |
| API | `taskBudget` | 软提示，模型会尝试精简 |

**实用技巧**：

1. **监控 token 消耗趋势**：
   - 在长时间任务中，定期询问模型当前 transcript 的估算 token 数。
   - 如果接近 150K（200K 窗口 - 50K 缓冲），主动触发 compact。

2. **主动 compact**：
   ```
   "当前上下文可能已经很大了。请执行一次 compact，
    保留最近 4 轮的对话，摘要更早的内容。"
   ```

3. **避免 token 浪费**：
   - 不要 attach 大文件的完整内容，只 attach 相关片段。
   - 及时清理已完成的 tool result（依赖 microcompact 自动处理）。
   - 使用 `/clear` 清理不相关的历史（慎用，会丢失上下文）。

---

### 3.5 记忆系统的主动管理

**记忆不是被动的**——你可以主动塑造它。

#### 写入记忆

```
User: 请记住：在这个项目中，所有数据库查询都必须使用参数化查询，
      禁止字符串拼接 SQL。这是安全红线。
```

模型会将此信息提取到 Auto Memory，后续会话自动加载。

#### 更新记忆

```
User: 更新记忆 'database-policy'：除了参数化查询外，
      现在还要求所有查询必须记录到 audit log。
```

#### 查询记忆

```
User: 你记忆中关于这个项目的测试策略是什么？
```

模型会检索相关记忆并回答。

#### 记忆的硬截断

- 单条记忆上限：200 行 / 25KB。
- 如果记忆超过上限，模型会自动截断，保留开头和结尾。
- **建议**：保持记忆简洁，一条记忆只记录一个知识点。

---

## 第四部分：实践检查清单

---

### 4.1 启动新项目时的检查清单

- [ ] 确认权限模式（DEFAULT/ACCEPT_EDITS/BYPASS）
- [ ] 加载项目 README 和关键配置文件
- [ ] 检查 MCP 服务器是否连接正常
- [ ] 确认工具白名单是否符合预期
- [ ] 验证 Prompt Cache 是否命中（首次通常 miss，后续应 hit）

### 4.2 每次对话前的检查清单

- [ ] 当前上下文 token 估算是否在安全范围（< 150K）
- [ ] 相关文件是否已 attach 或加载到上下文
- [ ] 是否需要先 compact 释放空间
- [ ] 是否明确指定了任务边界和期望输出

### 4.3 长任务执行中的检查清单

- [ ] 每 3-5 轮检查一次 token 消耗趋势
- [ ] 每完成一个子任务记录进度到 Session Memory
- [ ] 观察工具执行时间，超过 30 秒考虑中断诊断
- [ ] 确认 Agent 未陷入循环（重复执行相同操作）

### 4.4 结束会话前的检查清单

- [ ] 是否有重要发现需要写入 Auto Memory
- [ ] 未完成的子任务是否已记录到 Session Memory
- [ ] 是否需要导出 transcript 供后续参考
- [ ] 检查总成本是否在预算内

### 4.5 故障排查速查表

| 现象 | 可能原因 | 解决方案 |
|------|----------|----------|
| API 调用突然变慢 | Cache miss 或上下文过大 | 检查 cache hit 率，执行 compact |
| 模型重复相同错误 | Context collapse 丢失关键信息 | 显式重述关键约束，或写入 memory |
| 工具执行超时 | 命令复杂或网络问题 | 中断后拆分任务，或增加超时配置 |
| 权限频繁确认 | 工具不在白名单 | 调整权限模式，或将工具加入白名单 |
| 记忆未生效 | 记忆未正确提取或超过上限 | 手动要求写入记忆，保持简洁 |

---

## 附录：关键命令与快捷键速查

---

### A.1 REPL 命令

| 命令 | 作用 |
|------|------|
| `/clear` | 清除当前会话上下文（慎用） |
| `/compact` | 手动触发上下文压缩 |
| `/memory` | 查看当前加载的记忆 |
| `/cost` | 查看当前会话累计成本 |
| `/mode` | 切换权限模式 |
| `/load <file>` | 将文件加载到上下文 |
| `/agents` | 查看活跃的 Agent 列表 |
| `/stop <agent>` | 停止指定 Agent |

### A.2 快捷键

| 快捷键 | 作用 |
|--------|------|
| `Ctrl+C` | 中断当前操作 |
| `Ctrl+D` | 退出 REPL |
| `Tab` | 自动补全命令/路径 |
| `↑/↓` | 浏览历史输入 |

### A.3 环境变量

| 变量 | 作用 |
|------|------|
| `CLAUDE_CODE_COORDINATOR_MODE=1` | 启用 Coordinator 模式 |
| `CLAUDE_CODE_DEBUG=1` | 启用调试日志 |
| `CLAUDE_CODE_MAX_TURNS=50` | 设置最大回合数 |
| `CLAUDE_CODE_BYPASS_PERMISSIONS=1` | 绕过权限确认（危险） |

---

## 结语

掌握 Claude Code 不是记住所有命令，而是理解其**运行时内核的设计哲学**：

> **协议正确性优先于输出漂亮。**

这意味着：
- 每次 API 调用前的 `normalize_messages_for_api` 保证了协议正确性。
- 每次工具执行的四层栈保证了安全正确性。
- 每次 compact 的双防御策略保证了信息不丢失。
- 每次 memory 提取的 coalescing 保证了状态一致性。

当你理解了这些机制，你就能：
- **预判** Agent 的行为（它会先 check permission 再执行）。
- **诊断** 异常的原因（cache miss？context overflow？permission denied？）。
- **优化** 工作流的效率（并行 Agent、cache 隔离、任务拆分）。
- **扩展** Runtime 的能力（自定义 tool、hook、mcp server）。

这才是"完全掌握"的含义。

---

*本报告基于 Claude Code TypeScript 源码（1902 文件、513,237 行）及 Python 复刻项目（ClaudeCode-Runtime）的分析。*
*生成日期：2026-05-22*