# 第10章 记忆系统

> "Claude 是怎么记住我上周告诉它的项目约定的？""自动记忆和 CLAUDE.md 有什么区别？""我该把规则写进 CLAUDE.md 还是等 Claude 自己提取记忆？"

这三个问题的答案，都藏在 Claude Code 的"四层持久化体系"里。

本章不讨论上下文窗口怎么压缩（那是第 11 章的事），而是讨论**跨会话持久化**——Claude Code 如何把一次对话中学到的东西，带到下一次对话中。你将了解自动记忆如何工作、什么时候该手动写 CLAUDE.md、以及 Compact 和 Memory 这两条轨道为什么必须分开设计。

---

## 10.1 核心框架：Compact vs Memory 双轨设计

### 问题："压缩和记忆不是一回事吗？都是让 Claude 记住之前说过的话。"

不是一回事。理解这个区分，是正确使用记忆系统的前提。

MBZUAI 的学术论文 *Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems*（以下简称"Dive into Claude Code"）在分析上下文管理策略时指出，Claude Code 采用了一种"双轨设计"（dual-track design）：压缩（Compact）处理当前会话的上下文压力，记忆（Memory）处理跨会话的知识持久化。两者在时间尺度、持久性和目的上根本不同。

| 维度 | Compact | Memory |
|------|---------|--------|
| **时间范围** | 会话级（分钟/小时） | 跨会话（天/周/月） |
| **持久性** | 随会话结束丢失 | 写入文件系统 |
| **内容** | 对话摘要（有损） | 关键决策、偏好、项目上下文 |
| **目的** | 解决"上下文窗口不够" | 解决"下次还记得" |
| **触发时机** | token 接近阈值 / API 413 / 用户手动 | 定时 Coalescing 提取 / Stop hooks 后 |
| **产物** | CompactBoundaryMessage + summary | `~/.claude/projects/<hash>/memory/*.md` |

**关键洞见**：压缩摘要不是长期记忆，是会话内为继续对话而制造的"代替旧上下文的消息"。论文将其归类为"上下文缩减策略"（context-reduction strategy），与记忆提取（memory extraction）属于完全不同的子系统。

### Compact 不是零成本

常见误解："自动压缩无额外 API 调用"。**不准确**。

真实过程：`should_auto_compact()` → `compactConversation()` → `streamCompactSummary()` → **额外模型调用**（优先 fork 复用 prompt cache）→ 得到 summary → 主请求继续。

**为什么值得**：避免主请求因 prompt_too_long 失败，净收益 = 避免"失败 + compact + 重试"的两步成本。CMU、Yale、Amazon 等联合发表的综述 *Agent Harness Engineering: A Survey*（以下简称"Agent Harness 综述"）在分析 Agent 成本结构时指出，生产级 Agent 的上下文管理成本占总调用成本的 15%-30%，但失败的 retry 成本往往是成功调用的 3-5 倍。从这个角度看，提前压缩是"买保险"。

### 决策建议

- 如果你在想"当前对话太长了，Claude 开始重复自己" → 用 `/compact`（第 11 章详述）
- 如果你在想"下次打开这个项目，Claude 应该还记得我们的约定" → 用 Memory 系统（本章余下内容）

---

## 10.2 四层持久化体系

Claude Code 的持久化不是单一层级，而是一个从"临时"到"永久"的梯度。理解这个梯度，才能决定把信息放在哪一层。

```
Layer 1: Compact Summary（内存中，会话级）→ 会话结束即丢失
Layer 2: CLAUDE.md（项目级，手动维护）→ 最高优先级，系统不自动修改
Layer 3: Auto Memory（用户/项目长期，自动提取）→ ~/.claude/projects/<hash>/memory/
Layer 4: Session Memory（会话摘要，自动维护）→ 辅助 compact 决策
```

**加载优先级**：`CLAUDE.md > auto memory > session memory`

### Layer 1：Compact Summary —— "让我把前面的话总结一下"

这是第 11 章的核心内容。一句话概括：压缩是在**当前会话内**对旧对话的有损摘要，会话结束即消失。它不解决"下次还记得"的问题，只解决"现在还能继续聊"的问题。

### Layer 2：CLAUDE.md —— "这是你必须遵守的规则"

这是**你**写的文件，不是 Claude 自动生成的。它的特点是：

- **最高加载优先级**：每次会话启动时自动注入
- **系统不修改**：Claude 永远不会自动改写你的 CLAUDE.md
- **确定性注入**：不像 auto memory 是概率性选取，CLAUDE.md 的内容**一定**出现在上下文中

**CLAUDE.md 文件位置**（按加载顺序，越后加载优先级越高）：

| 范围 | 位置 | 共享对象 |
|------|------|----------|
| 托管策略 | macOS: `/Library/Application Support/ClaudeCode/`<br>Linux/WSL: `/etc/claude-code/`<br>Windows: `C:\Program Files\ClaudeCode\` | 组织所有用户 |
| 用户 | `~/.claude/CLAUDE.md` | 仅你（所有项目） |
| 项目 | `./CLAUDE.md` 或 `./.claude/CLAUDE.md` | 团队成员 |
| 本地 | `./CLAUDE.local.md` | 仅你（当前项目） |

加载规则：从工作目录向上遍历目录树，所有发现的文件**连接**而非覆盖。越靠近工作目录的文件越后读取（优先级越高）。同级中 `CLAUDE.local.md` 在 `CLAUDE.md` 后附加。

**编写有效指令的原则**：

- **目标 < 200 行**：过长降低遵守度
- **结构化**：markdown 标题 + 项目符号分组
- **具体可验证**："使用 2 空格缩进" 而非 "正确格式化"
- **一致性**：定期检查删除冲突/过时指令

**导入其他文件**（最大递归深度 4，首次需批准）：

```markdown
@README.md
@package.json
@docs/git-instructions.md
@~/.claude/my-project-instructions.md
```

**路径范围规则**（按需加载，读到匹配文件时才注入）：

```markdown
---
paths:
  - "src/api/**/*.ts"
---
# API 规则
- 所有端点必须输入验证
- 使用标准错误格式
```

### Layer 3：Auto Memory —— "Claude 自己学到的东西"

这是 Claude 自动提取的跨会话记忆。与 CLAUDE.md 的核心区别：

| | CLAUDE.md | 自动记忆 |
|--|-----------|----------|
| **谁编写** | 你 | Claude |
| **内容** | 指令和规则 | 学习和模式 |
| **范围** | 项目/用户/组织 | 每个工作树，跨 worktrees 共享 |
| **加载** | 每会话完整加载 | 每会话前 200 行或 25KB |
| **用途** | 编码标准、工作流、架构 | 构建命令、调试见解、偏好 |

- 默认开启，切换：`/memory` 或 `autoMemoryEnabled: false`
- 位置：`~/.claude/projects/<project>/memory/`
- 结构：`MEMORY.md`（索引）+ 主题文件（`debugging.md` 等）
- 主题文件按需读取，启动时不加载
- 纯文本，可手动编辑删除

### Layer 4：Session Memory —— "本次会话的笔记"

Session Memory 是辅助 compact 决策的会话级摘要。它在会话进行中自动维护，auto-compact 时会优先使用 Session Memory 而非重新摘要整个历史。这比每次都重新摘要更快更便宜。

### 决策：信息该放哪一层？

```
这条信息...
  ├─ 是我明确想规定的规则？
  │    └─ 写进 CLAUDE.md（Layer 2）
  │
  ├─ 是 Claude 在对话中自己发现的模式/偏好？
  │    └─ 让它自动提取到 Auto Memory（Layer 3）
  │
  ├─ 只在本次会话中有用？
  │    └─ 交给 Session Memory（Layer 4）或 Compact（Layer 1）
  │
  └─ 需要团队共享？
       └─ 写进项目级 CLAUDE.md（Layer 2）
            或托管 CLAUDE.md（组织级部署）
```

**黄金法则**：如果你纠正了 Claude 两次以上关于同一件事，**写进 CLAUDE.md**。不要依赖自动记忆来承载关键规则——auto memory 是概率性加载，CLAUDE.md 是确定性注入。

---

## 10.3 四类记忆详解

Claude Code 的记忆系统不是单一的"自动记忆"，而是按 scope 分为四类。了解它们的区别，才能判断某条信息会存在哪里、谁能看到、生命周期多长。

| 记忆类型 | Scope | 存储位置 | 触发方式 | 生命周期 |
|---------|-------|---------|---------|---------|
| **Auto Memory** | user / project | `~/.claude/memory/` | Stop hooks 后自动提取 | 跨会话持久 |
| **Session Memory** | 当前会话 | 内存 + session JSONL | Query 完成后后台任务 | 当前会话 |
| **Agent Memory** | 特定 agent 类型 | `~/.claude/agents/<name>/memory/` | Agent 完成后后台提取 | 跨会话持久 |
| **Team Memory** | 团队共享 | `~/.claude/teams/<team>/memory/` | Teammate 间共享注入 | 跨会话持久 |

### Auto Memory：你最常打交道的记忆

Auto Memory 是默认开启的跨会话记忆。它的工作方式：

1. 对话进行中，Claude 在后台识别值得记住的信息
2. 在会话停止边界（stop hooks）触发提取
3. 提取过程异步执行，不阻塞你的输入
4. 下一轮 system prompt 构建时，记忆内容被注入

**提取标准**：只保存用户偏好、反馈修正、项目决策、外部资源指针；**不保存**可从代码库推导的信息（代码模式、git 历史、调试方案）。这是设计上的刻意限制——不要把代码库已经能回答的问题浪费在记忆存储中。

**存储模型**：

```
~/.claude/projects/<project-hash>/memory/
  ├── MEMORY.md          # 索引文件（200 行 / 25KB 上限）
  ├── user-role.md
  ├── project-stack.md
  └── ...
```

- 每条记忆单独 Markdown 文件，人类可读、可手动编辑、Git 可追踪
- MEMORY.md 只维护索引，不是内容
- 硬截断保护：MEMORY.md 超 200 行截断，每条记忆超 25KB 截断

### Session Memory：本次会话的"草稿笔记"

Session Memory 在当前会话内维护，辅助 compact 决策。触发条件：token 增长（初始 10K，后续 5K 间隔）3 次工具调用。如果 Session Memory 压缩后仍超阈值，再回退到传统的完整压缩。

**关键区别**：Session Memory 不会跨会话持久。它的价值在于让 auto-compact 更快更便宜——不用每次都重新摘要整个历史。

### Agent Memory 与 Team Memory

这两类记忆的使用场景更特定：

- **Agent Memory**：当你使用特定类型的 agent（如 Explore agent、Test agent）时，该 agent 类型可以积累自己的记忆。不同 agent 类型的记忆相互隔离。
- **Team Memory**：在团队共享的 workspace 中，teammate 之间可以共享记忆注入。这需要显式的团队配置。

对大多数个人用户来说，Auto Memory 和 Session Memory 是日常打交道最多的两类。

---

## 10.4 Coalescing 优于 Debounce

记忆提取的调度机制有一个关键设计选择，直接影响"Claude 有没有漏掉该记住的东西"。

### 问题："为什么不用 Debounce？"

Debounce 是常见的异步事件合并策略：等一段时间没新事件了，再执行一次。但 Debounce 有一个致命问题：**可能丢弃中间状态**。

假设对话中有三次值得记住的修正：

```
T1: 用户纠正了缩进风格
T2: 用户指定了测试框架
T3: 用户说明了部署流程
```

如果用 Debounce，T2 和 T3 之间如果间隔很短，T2 的状态可能被丢弃，只保留 T3。结果是：Claude 记住了部署流程，但**漏掉了缩进风格和测试框架**。

### Coalescing 的设计

Claude Code 使用 Coalescing 而非 Debounce：

```python
class ExtractionCoordinator:
    _dirty: bool = False
    _in_progress: bool = False
    
    async def request_extraction(self, messages, cwd, call_model):
        self._dirty = True
        if self._in_progress:
            return  # 当前提取完成后的 dirty 检查会处理
        while self._dirty:
            self._dirty = False
            self._in_progress = True
            await self._do_extract(...)
            self._in_progress = False
```

**本质区别**：Debounce 可能丢弃中间状态；Coalescing 保证最终状态一定被扫描。

正确性优先于效率。记忆提取的成本是一次模型调用，但漏掉关键偏好的成本是用户反复纠正——后者贵得多。

### 慢通道设计

提取过程通过 `asyncio.create_task` 异步执行，不阻塞用户输入。你打完字、Claude 在回复的同时，后台可能在提取上一轮的记忆。用户等待感为零。

**Agent Harness 综述**在分析 Agent 响应延迟时指出，"任何非关键路径的操作都应该异步化"（"Any operation not on the critical response path should be async"）。记忆提取正是这类操作——它影响的是"下一轮对话的质量"，而不是"当前回复的速度"。

---

## 10.5 约束与边界：记忆不是万能的

### 多重约束门控

Memory extraction 受多重约束控制，确保提取只在合适的时间、场景触发：

- feature gate
- GrowthBook gate
- autoMemoryEnabled 用户设置
- remote mode
- subagent id
- non-interactive session
- in-progress 状态
- cursor
- 主 Agent 已写 memory 检测

这些约束的存在说明：**自动记忆不是无条件的**。在某些场景下（如子代理执行、非交互式会话、远程模式），记忆提取会被跳过。

### 硬截断保护

记忆文件有硬性上限：

- MEMORY.md 索引：200 行上限
- 单条记忆文件：25KB 上限

超过即截断。这不是 bug，是设计——防止记忆文件无限增长，避免加载时消耗过多上下文 token。

### 加载限制

Auto Memory 每会话只加载前 200 行或 25KB。主题文件按需读取，启动时不加载。这意味着：

- 你的记忆文件可能很长，但 Claude 每次只会看到开头部分
- 重要的记忆应该放在 MEMORY.md 索引的前面
- 不重要的或过时的记忆应该手动清理

---

## 10.6 实战：记忆系统的日常使用

### 场景一："Claude 总是忘记我用空格缩进"

**错误做法**：每次对话都提醒它，指望自动记忆提取。

**正确做法**：

1. 先观察：它是否真的没提取到？检查 `~/.claude/projects/<project>/memory/` 下有没有相关内容
2. 如果没提取到，直接写进 `./CLAUDE.md`：
   ```markdown
   ## 代码风格
   - 使用 2 空格缩进
   - 不使用 tab
   ```
3. 如果已经提取到了但 Claude 还是不遵守，说明 auto memory 的加载优先级不够高——这正是该用 CLAUDE.md 的信号

**决策原则**：纠正两次以上 → 写进 CLAUDE.md。一次性的偏好 → 等自动记忆。

### 场景二："我想清理 Claude 记住的过时信息"

直接编辑记忆文件：

```bash
# 查看当前项目的记忆
ls ~/.claude/projects/$(claude project-hash)/memory/

# 编辑或删除特定主题
vim ~/.claude/projects/<hash>/memory/debugging.md

# 清理整个项目的记忆
rm -rf ~/.claude/projects/<hash>/memory/
```

记忆文件是纯 Markdown，人类可读、可手动编辑、Git 可追踪（如果你选择把它们纳入版本控制）。

### 场景三："团队里每个人对项目的理解不一样"

**使用项目级 CLAUDE.md**：放在仓库根目录或 `.claude/CLAUDE.md`，团队成员共享。

**使用托管策略**（组织部署）：通过 MDM/Group Policy 分发托管 CLAUDE.md，不可被个人排除。也可用 `claudeMd` 键直接写入 `managed-settings.json`。

| 关注点 | 配置位置 |
|--------|----------|
| 阻止工具/命令/路径 | 托管设置 `permissions.deny` |
| 沙箱隔离 | 托管设置 `sandbox.enabled` |
| 代码样式/质量指南 | 托管 CLAUDE.md |
| 行为指令 | 托管 CLAUDE.md |

### 场景四："Claude 不遵循我的 CLAUDE.md"

排查清单：

1. `/memory` 验证加载——确认文件确实被读取
2. 检查文件位置——是否在正确的加载路径上
3. 使指令更具体——"使用 2 空格缩进" 比 "正确格式化" 有效得多
4. 查找冲突——多个 CLAUDE.md 文件中的指令是否矛盾
5. 检查大小——超过 200 行的 CLAUDE.md 遵守度会下降
6. 指令需要在特定点运行？改用 hooks（第 8 章）
7. 需要系统提示级指令？使用 `--append-system-prompt`

### 场景五："`/compact` 后我的指令丢了"

根目录的 CLAUDE.md 会自动重新注入，但子目录的 path-scoped rules 需要重新读取。如果 compact 后 Claude 似乎忘记了某个子目录的规则，可能是因为该规则还没被重新触发加载。

**处置**：在 compact 后主动提及相关规则，或把关键规则上提到根目录 CLAUDE.md。

---

## 10.7 关键设计决策回顾

Claude Code 的记忆系统背后有四个关键设计决策，理解它们有助于你判断系统的行为：

**1. 双轨设计**：压缩处理当前会话旧内容，记忆处理跨会话持久知识；时间尺度和持久性需求根本不同。"Dive into Claude Code"将 compact 归类为上下文缩减策略，将 memory extraction 归类为知识持久化策略，两者在架构上解耦。

**2. 四层持久化梯度**：从临时（Compact Summary）到永久（CLAUDE.md），每层有明确触发条件和写入权限。用户控制 Layer 2（手动编写），系统自动维护 Layer 1/3/4。

**3. Coalescing 而非 Debounce**：保证最终状态一定被扫描。正确性优先于效率——漏掉一条偏好的成本远高于多一次提取调用。

**4. 每条记忆单独 Markdown**：人类可读、可审计、单条更新不重写全量。与把记忆塞进一个 JSON blob 或数据库的设计相比，这种"文件即 API"的方式让用户拥有完全的可视性和控制权。
