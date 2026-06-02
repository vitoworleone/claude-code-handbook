# 第19章 并行流水线（四角色模型）

> "怎么让 Claude 不只帮我写代码，而是帮我管理整个开发流程？""审查和实现由不同 Agent 做，会比同一个 Agent 自审更可靠吗？""从需求到合并，能全自动吗？"

第17章讲了多任务并行的能力——子代理调度、后台运行、权限隔离。第18章铺好了物理隔离的基础设施——worktree 文件系统隔离、文件锁协调。本章把这些能力**组装成一套可复用的生产流水线**。

四角色模型（Planner → Implementer → Reviewer → Merger）不是理论构想，而是 Claude Code 社区在实践中演化出的工作模式。Matt Pocock 的并行流水线方法论、Ralph Loop 的演进路线、以及 *Agent Harness Engineering: A Survey* 中的 Multi-Agent Orchestration Patterns，共同构成了这套模型的理论基础。

---

## 19.1 四角色模型概览

### 问题：我一个人用 Claude，为什么需要"四角色"？这不是团队才有的分工吗？

**背景**：当你用 Claude Code 处理复杂任务时（例如"实现用户认证系统"），一个会话通常要经历：理解需求 → 设计方案 → 写代码 → 自审 → 修 bug → 跑测试 → 合并。这些步骤在同一上下文中串行执行，导致：
- 上下文迅速膨胀（设计 + 实现 + 审查混在一起）
- 自审有盲点（写了代码的模型很难发现自己的错误）
- 失败时需要从头重来（没有 checkpoint）

**为什么是问题**：人类开发者不会"一边写代码一边做最终审查"——审查是独立活动，需要 fresh eyes。让同一个 Agent 既实现又审查，相当于让作者自己校对——能发现拼写错误，但很难发现逻辑漏洞。

**四角色模型的设计**：把复杂任务的开发流程拆分为四个独立角色，每个角色由专门的子代理执行：

```
┌─────────┐    ┌─────────────┐    ┌──────────┐    ┌────────┐
│ Planner │───→│ Implementer │───→│ Reviewer │───→│ Merger │
└─────────┘    └─────────────┘    └──────────┘    └────────┘
   规划            实现              审查            合并
```

| 角色 | 职责 | 输出 | 典型代理类型 | 隔离模式 |
|------|------|------|-------------|---------|
| **Planner** | 读取需求/看板，输出可并行执行的任务批次 | 任务分配表（DAG） | Plan | in-process |
| **Implementer** | 在隔离 worktree 中实现代码 | 功能代码 + 测试 | General-purpose | **worktree** |
| **Reviewer** | 审查代码，对照规范检查 | 审查报告 + 修改建议 | Verification / 自定义 | **worktree** |
| **Merger** | 合并分支、解决冲突、跑全量测试 | 合并后的主分支 | General-purpose | **worktree** |

**为什么拆成四个角色而非两个或六个**：

- **少于四个**：如果只有"实现 + 审查"，需求拆解和合并冲突仍然混在实现 Agent 里，上下文还是膨胀
- **多于四个**：协调成本超过收益。四个角色覆盖了"规划→执行→验证→集成"的完整闭环，没有冗余
- **四个是经验值**：Matt Pocock 的并行流水线和 Ralph Loop 实践都收敛到这个数字

**结论**：四角色模型让复杂开发任务从"单一会话硬扛"变成"流水线分工协作"。每个角色专注一件事，上下文更小，质量更高。

**怎么做**：
- 简单任务（改一个函数、修一个 bug）→ 不需要四角色，主会话直接处理
- 中等任务（实现一个模块、添加一个 API）→ 用 Implementer + Reviewer 两角色
- 复杂任务（重构系统、实现大功能）→ 完整四角色流水线

---

## 19.2 Planner：任务拆解与批次分配

### 问题：怎么让 Agent 知道"先做哪件、后做哪件、哪些可以并行"？

**背景**：复杂任务天然有依赖关系。例如"实现用户认证"可能包含：设计数据库表 → 实现注册 API → 实现登录 API → 实现 JWT 验证 → 写前端表单 → 写测试。这些子任务中，"设计数据库表"必须在"实现注册 API"之前，但"写前端表单"和"实现登录 API"可以并行。

**为什么是问题**：如果没有明确的任务拆解和依赖分析，并行执行会变成"同时做一堆事，最后发现顺序错了"。人类的项目管理工具（看板、Jira）解决了这个问题，但 Agent 需要结构化的输入才能理解依赖关系。

**Planner 的工作机制**：

Planner 是一个专门的 Plan 类型子代理（第17章 17.4），它的职责不是写代码，而是**分析依赖、规划路径、分配批次**。

**输入**：
- 看板数据（`blocked_by` 关系、优先级、标签）
- 用户自然语言描述的需求
- 代码库当前状态（已有什么、还需要什么）

**输出**：任务分配表（DAG 格式）

```json
{
  "batches": [
    {
      "batch_id": 1,
      "tasks": ["设计数据库表", "定义 API 接口"],
      "parallel": true,
      "deliverables": ["schema.sql", "api-spec.md"]
    },
    {
      "batch_id": 2,
      "depends_on": [1],
      "tasks": ["实现注册 API", "实现登录 API"],
      "parallel": true,
      "deliverables": ["src/api/auth.ts"]
    },
    {
      "batch_id": 3,
      "depends_on": [2],
      "tasks": ["写前端表单", "写集成测试"],
      "parallel": true,
      "deliverables": ["src/components/Login.tsx", "tests/auth.spec.ts"]
    }
  ]
}
```

**Planner 的关键决策**：

| 决策 | 考量 | 示例 |
|------|------|------|
| **哪些任务可以并行** | 无依赖关系的任务 | 注册 API 和登录 API 可以并行 |
| **哪些必须串行** | 有数据/逻辑依赖 | 数据库表设计必须在 API 实现之前 |
| **每个批次多大** | Smart Zone 限制（第12章） | 单个 Implementer 的上下文不超过 100K |
| **验收标准是什么** | Reviewer 需要明确的通过条件 | "所有测试通过 + Reviewer 无 blocking 意见" |

**结论**：Planner 是流水线的"调度器"。它的输出质量决定了后续 Implementer 会不会做无用功——如果 Planner 漏了依赖，Implementer 会卡在半成品状态；如果 Planner 批次太大，Implementer 会进入 Dumb Zone。

**怎么做**：
- 给 Planner 明确的看板格式（`blocked_by`、`priority`、`estimated_effort`）
- 让 Planner 输出具体的验收标准（而非模糊的"完成开发"）
- 审查 Planner 的输出后再启动 Implementer——修正规划比修正实现便宜得多

---

## 19.3 Sandbox：独立 worktree 中的实现与审查

### 问题：Implementer 和 Reviewer 运行在哪里？它们会互相干扰吗？

**背景**：第18章讲了 worktree 隔离，但四角色流水线是 worktree 的**规模化应用**——一个复杂任务可能需要 3~5 个 Implementer 同时运行，每个在独立 worktree 中实现不同模块。

**为什么是问题**：如果没有统一的 Sandbox 管理，worktree 会散乱创建、命名混乱、难以追踪哪个 worktree 属于哪个批次。

**Sandbox 的设计**：每个 Sandbox 是一个**独立管理的 worktree**，有统一的命名规范和生命周期。

```
.claude/worktrees/
├── sandbox-planner/          # Planner 的工作目录（in-process，无需隔离）
├── sandbox-impl-batch1-1/    # Batch 1 的第 1 个 Implementer
├── sandbox-impl-batch1-2/    # Batch 1 的第 2 个 Implementer（并行）
├── sandbox-review-batch1-1/  # Batch 1 第 1 个任务的 Reviewer
├── sandbox-review-batch1-2/  # Batch 1 第 2 个任务的 Reviewer
├── sandbox-impl-batch2-1/    # Batch 2 的第 1 个 Implementer
└── sandbox-merger/           # Merger 的合并工作区
```

**Sandbox 的生命周期管理**：

```
Planner 输出批次计划
  ↓
为 Batch N 的每个并行任务创建 sandbox-impl-batchN-X
  ↓
Implementer 在各自 sandbox 中运行（文件锁协调共享资源访问）
  ↓
Implementer 完成 → 创建对应 sandbox-review 启动 Reviewer
  ↓
Reviewer 通过 → 标记为 ready-to-merge
  ↓
所有 Reviewer 通过 → Merger 在 sandbox-merger 中合并所有分支
```

**结论**：Sandbox 是 worktree 的"工厂化封装"——统一管理命名、生命周期、协调，让四角色流水线从概念变成可重复执行的工作流。

**怎么做**：
- 不要让 Implementer 自己决定 sandbox 名称 → Planner 统一分配，避免冲突
- 每个 sandbox 只容纳一个角色的一个任务 → 不要把实现和审查混在同一个 worktree
- Reviewer 的 sandbox 基于对应 Implementer 的分支创建 → 确保审查的是最新代码

---

## 19.4 Implementer vs Reviewer：模型分工与规范传递

### 问题：实现和审查用同一个模型吗？审查真的能发现问题吗？

**背景**：四角色模型中，Implementer 和 Reviewer 是两个独立角色。但"独立"不只是"不同的 Agent 实例"——它们可以是**不同的模型**，用不同的策略传递规范。

**模型分工：Sonnet（快）实现，Opus（聪明）审查**

| | Implementer | Reviewer |
|--|-------------|----------|
| **模型选择** | Sonnet 4.6（快、便宜） | Opus 4.8（聪明、贵） |
| **目标** | 快速生成符合规范的代码 | 发现 Implementer 遗漏的问题 |
| **上下文需求** | 需要读取相关代码、生成修改 | 需要全局视角、对照规范检查 |
| **为什么这样分** | 实现是"有明确目标的生产活动" | 审查是"无明确目标的探索活动" |

**为什么是这种分工**：

实现任务有明确的输入（需求描述 + 相关代码）和输出（修改后的代码），Sonnet 的速度优势能显著缩短实现时间。审查任务需要"发现 Implementer 没想到的问题"——这是一种需要更高推理能力的活动，Opus 的智商优势在这里更有价值。

更重要的是经济账：
- Sonnet 比 Opus 便宜约 5~10 倍（按 token 计价）
- 实现消耗的 token 通常比审查多（实现要读写大量代码，审查只需读 + 写审查意见）
- 用 Sonnet 实现 + Opus 审查，总成本低于用 Opus 做两件事

**规范传递：PULL vs PUSH**

Matt Pocock 的并行流水线方法论提出了两种规范传递模式：

| 模式 | 机制 | 适用场景 | 类比 |
|------|------|---------|------|
| **PUSH** | Planner 把完整规范注入 Implementer 的上下文 | 规范明确、变化少的任务 | 瀑布模型：需求文档一次性下发 |
| **PULL** | Implementer 按需从规范源读取 | 规范复杂、需要局部查询的任务 | 敏捷开发：需要时才查文档 |

**为什么需要两种模式**：

- **PUSH 的优势**：Implementer 一开始就拥有全部上下文，不需要中途停下来查规范，执行流畅
- **PUSH 的劣势**：规范太长会占用上下文预算（第12章），Implementer 可能进入 Dumb Zone
- **PULL 的优势**：规范不常驻上下文，只在需要时读取，保持 Smart Zone
- **PULL 的劣势**：Implementer 需要知道"什么时候该查规范"，增加了决策负担

**结论**：规范传递模式影响上下文效率。简单任务用 PUSH（规范短），复杂任务用 PULL（规范长）。

**怎么做**：
- 规范 < 500 字 → PUSH，直接写进 delegation prompt
- 规范 > 500 字或有多个独立模块 → PULL，让 Implementer 通过 Read 工具按需读取
- 审查比实现更需要"聪明"→ 审查用 Opus，实现用 Sonnet
- 审查只有一次机会 → 用你能负担得起的最好模型做审查

---

## 19.5 Merger：合并、冲突解决与集成验证

### 问题：多个 Implementer 并行完成的代码，怎么合并到一起？

**背景**：四角色流水线的最终目标是把多个并行实现的模块合并成可用的主分支。但并行开发必然带来合并冲突——两个 Implementer 修改了同一文件的不同部分，或者一个的修改依赖另一个的接口。

**为什么是问题**：如果 Merger 只是机械地 `git merge`，冲突会频繁失败，需要人工干预。Merger 需要理解**为什么**两个修改冲突，以及如何**智能地**解决冲突。

**Merger 的工作机制**：

Merger 是一个 General-purpose 类型的子代理，运行在独立的 sandbox-merger worktree 中。它的职责：

1. **收集所有通过的实现**：从各 sandbox-review 分支拉取已通过审查的代码
2. **按依赖顺序合并**：根据 Planner 的 DAG，先合并基础模块，再合并依赖模块
3. **解决合并冲突**：当自动合并失败时，理解两个修改的意图，生成兼容的合并结果
4. **跑全量测试**：合并后运行完整的测试套件，验证集成没有破坏现有功能
5. **输出最终分支**：一个干净的可合并到 main 的分支

**合并冲突的智能解决**：

机械合并（`git merge`）只比较文本差异，不理解语义。Merger Agent 的优势在于：

```
冲突场景：
  Implementer A 在 auth.ts 中添加了 register() 函数
  Implementer B 在同一文件的同一区域添加了 login() 函数

机械合并结果：CONFLICT（同一区域两个修改）

Merger Agent 的结果：
  - 理解两个函数都是 auth 模块的 API
  - 按字母顺序或逻辑顺序排列两个函数
  - 检查共享依赖（如 JWT 工具）的一致性
  - 生成一个包含两个函数的兼容版本
```

**全量测试的必要性**：

每个 Implementer 在实现时只运行了相关测试（单元测试），但模块集成后可能出现：
- 接口不匹配（A 模块输出的格式 B 模块不识别）
- 资源竞争（两个模块同时写同一配置文件）
- 性能退化（并行模块叠加后的总开销超标）

Merger 跑全量测试是最后的质量关卡。

**结论**：Merger 是流水线的"集成测试 + 发布经理"。它的成功标准不是"合并没有冲突"，而是"合并后的代码能通过所有测试"。

**怎么做**：
- Merger 的 sandbox 基于 main 分支创建，确保合并起点是最新代码
- 给 Merger 明确的合并顺序（来自 Planner 的 DAG）
- 全量测试失败时，Merger 应该回滚到上一个稳定状态，而不是在冲突代码上修修补补

---

## 19.6 子代理权限两阶段模型

### 问题：四角色流水线的子代理权限怎么管？Implementer 能删主分支吗？

**背景**：第17章 17.7 讲了子代理与主会话的权限隔离。四角色流水线是子代理的**规模化应用**——一个复杂任务可能同时运行 5~10 个子代理，每个有不同的权限需求。

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 8.2）定义了**权限覆盖逻辑（Permission Override Cascade）**：

```
阶段一：SDK-level permissions（全局硬边界）
  └── 由启动参数或托管设置定义
  └── 应用于所有 agent，不可被覆盖
  
阶段二：Session-level rules（会话级规则）
  └── 由当前会话的权限配置定义
  └── 子代理可继承，也可被 AgentTool 的 permission overrides 替换
```

**四角色流水线的权限映射**：

| 角色 | 需要的工具 | 需要禁止的工具 | 权限模式 |
|------|-----------|---------------|---------|
| **Planner** | Read, Grep, Glob | Write, Edit, Bash | restricted |
| **Implementer** | Read, Write, Edit, Bash | 无（但受 SDK-level 限制） | standard |
| **Reviewer** | Read, Grep, Glob, Bash | Write, Edit | restricted |
| **Merger** | Read, Write, Edit, Bash, Git | 无（但限制在 sandbox 内） | standard |

**为什么 Reviewer 不给写权限**：审查应该只产生审查意见，不直接修改代码。如果 Reviewer 能直接改，它就变成了"隐式 Implementer"，审查的独立性被破坏。

**为什么 Merger 有 Git 权限但受 sandbox 限制**：Merger 需要执行 `git merge`、`git branch` 等操作，但它的工作目录被限制在 sandbox-merger worktree 内，无法影响主仓库的其他分支。

**异步 Agent 的权限提示处理**：

四角色流水线中的 Implementer 和 Reviewer 通常是后台运行的异步 Agent。当它们需要权限审批时：

| 场景 | 处理方式 | 原因 |
|------|---------|------|
| Implementer 请求执行 Bash 命令 | Bubble 到主会话审批 | 涉及系统命令，需人工确认 |
| Reviewer 请求读取敏感文件 | 如果 Read 在 allowed_tools 内，自动允许 | 只读操作风险低 |
| Merger 请求 git push | 默认拒绝，需显式授权 | 直接影响远程仓库 |

**结论**：四角色流水线的权限管理遵循"最小权限 + 角色差异化"原则。每个角色只能做它该做的事，SDK-level 硬边界防止任何角色的权限溢出。

**怎么做**：
- 启动四角色流水线前，用 `--allowedTools` 设置全局硬边界
- Planner 和 Reviewer 显式设置 `disallowedTools: [Write, Edit]`
- Merger 的 Git 权限限制在本地操作（merge、commit），禁止 `git push`
- 审查所有子代理的 `allowed_tools` 列表——"为什么 Planner 需要 Bash？"

---

## 19.7 侧链 Transcript 设计

### 问题：四角色并行运行时，主会话的上下文不会爆炸吗？

**背景**：四角色流水线可能同时运行多个子代理，每个子代理都有自己的对话历史、工具调用、文件读写记录。如果所有这些信息都回传到主会话，主会话的上下文会迅速超过 Smart Zone（第12章）。

**为什么是问题**：第11章已证明上下文窗口是最稀缺的资源。四角色流水线的价值在于并行，但如果并行导致主会话上下文爆炸，反而降低了整体效率。

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 8.3）的 **Sidechain Transcript（侧链转录）** 设计解决了这个问题：

> "Each subagent writes independent .jsonl + .meta.json transcript files. Only final response text + metadata returns to the parent session. Complete subagent history never enters parent context."

翻译：每个子代理写独立的 `.jsonl` + `.meta.json` 转录文件。只有最终响应文本 + 元数据返回父会话。完整的子代理历史**永不进入**父级上下文。

**侧链 Transcript 的文件结构**：

```
.claude/worktrees/sandbox-impl-batch1-1/
├── .claude/
│   └── transcripts/
│       ├── session.jsonl          # 完整的对话历史（不返回父会话）
│       └── session.meta.json      # 元数据（返回父会话）
```

**返回父会话的内容**：

```json
{
  "agent_id": "impl-batch1-1",
  "agent_type": "General-purpose",
  "status": "succeeded",
  "summary": "实现了用户注册 API，包括输入验证、密码哈希、JWT 生成。修改了 src/api/auth.ts（+120 行），添加了 tests/auth.register.spec.ts（+85 行）。所有单元测试通过。",
  "files_modified": [
    "src/api/auth.ts",
    "tests/auth.register.spec.ts"
  ],
  "test_results": {
    "passed": 12,
    "failed": 0
  }
}
```

**summary-only return 的上下文节省**：

假设一个 Implementer 运行了 50 轮对话，每轮包含工具调用和文件读写：
- 完整历史可能占用 30K~50K tokens
- summary-only 返回只占用 500~1K tokens
- **节省比例：30~50 倍**

四角色流水线中，如果有 5 个并行子代理：
- 完整历史回传：5 × 40K = 200K tokens（远超 Smart Zone）
- summary-only 回传：5 × 800 = 4K tokens（可忽略）

**结论**：侧链 Transcript 让四角色并行不会压垮主会话。子代理的完整历史存储在侧链文件中，你随时可以通过 `claude logs` 查看，但主会话只接收摘要。

**怎么做**：
- 默认使用 summary-only 模式——不需要额外配置
- 如果某个子代理的结果有疑问 → `claude logs <agent-id>` 查看完整历史
- 如果子代理失败需要调试 → attach 到子代理会话查看详细上下文

---

## 19.8 演进路线：从手动到并行工厂

### 问题：四角色流水线听起来很复杂，我应该一步到位吗？

**背景**：Matt Pocock 的并行流水线方法论和 Ralph Loop 实践都指出，**并行化是渐进演进的**，不是非黑即白的选择。

**为什么是问题**：如果一开始就尝试完整四角色流水线，配置复杂、调试困难、失败时难以定位问题。从简单模式开始，逐步增加复杂度，是更稳妥的路径。

**四阶段演进路线**：

```
阶段一：手动（Manual）
  你：理解需求 → 写 prompt → 和 Claude 对话 → 自己审查 → 自己合并
  并行度：0
  适用：简单任务、学习阶段

阶段二：顺序 Ralph Loop（Sequential Ralph Loop）
  你：给需求 → Planner Agent 输出计划 → 你确认 → Implementer Agent 实现 → Reviewer Agent 审查 → 你合并
  并行度：1（角色串行，但由不同 Agent 执行）
  适用：中等任务、建立信任

阶段三：带审查的 Ralph Loop（Ralph Loop with Review）
  Planner → 多个 Implementer 并行 → 多个 Reviewer 并行 → Merger 合并
  并行度：N（Implementer 和 Reviewer 并行）
  适用：复杂任务、团队标准化

阶段四：并行工厂（Parallel Factory）
  全自动：Planner 自动读取看板 → 自动分配 → 自动实现 → 自动审查 → 自动合并
  并行度：N（全流程自动）
  适用：标准化重复任务、大规模开发
```

**各阶段的瓶颈和跃迁条件**：

| 阶段 | 当前瓶颈 | 跃迁到下一阶段的条件 |
|------|---------|---------------------|
| 手动 | 你的时间 | 同一类任务做过 3 次以上，流程稳定 |
| 顺序 Ralph | Planner 输出质量 | Planner 的计划 3 次无需修改即可执行 |
| 带审查 Ralph | Reviewer 发现率 | Reviewer 能发现 >80% 的明显问题 |
| 并行工厂 | 异常处理 | 全自动流程的成功率 >90% |

**结论**：不要试图一步到位。从你能控制的最小并行开始，验证每个角色的可靠性，再逐步增加自动化程度。

**怎么做**：
- 第一次用四角色 → 只用一个 Implementer + 一个 Reviewer，Planner 和 Merger 由你手动做
- Planner 输出稳定后 → 让 Planner 自动运行，你只做最终确认
- Reviewer 发现率达标后 → 增加并行 Implementer 数量
- 全流程成功率 >90% 后 → 尝试全自动流水线

---

## 19.9 扩展视角：Full Lifecycle Pipeline

### 问题：四角色流水线能扩展到什么规模？能从 Issue 到 PR 全自动吗？

**背景**：*Agent Harness Engineering: A Survey*（Section 6.4）描述了从 Issue 到 Pull Request 的 **Full Lifecycle Pipeline（全生命周期流水线）**。四角色模型是这个流水线的核心，但可以扩展到更大的范围。

**Full Lifecycle Pipeline 的完整链路**：

```
Issue 创建（人工或自动触发）
  ↓
Requirement Parser → 解析需求，提取 acceptance criteria
  ↓
Planner → 拆解任务，输出 DAG
  ↓
Implementer(s) → 并行实现
  ↓
Reviewer(s) → 并行审查
  ↓
Merger → 合并、解决冲突
  ↓
CI/CD 触发 → 自动跑测试、构建、部署到 staging
  ↓
Human Approval → 人工最终确认（关键节点）
  ↓
Auto-merge to main → 自动合并到主分支
  ↓
PR 自动生成 → 包含完整变更描述和测试报告
```

**与传统 CI/CD 的结合点**：

四角色流水线不是替代 CI/CD，而是**前置增强**：

| 阶段 | 四角色流水线 | 传统 CI/CD | 关系 |
|------|-------------|-----------|------|
| 需求分析 | Planner 拆解 | 无 | 流水线新增 |
| 代码实现 | Implementer | 人工开发 | 流水线替代 |
| 代码审查 | Reviewer | 人工 PR Review | 流水线预审查 |
| 合并 | Merger | `git merge` | 流水线增强（智能冲突解决） |
| 测试 | 单元测试 | CI 测试流水线 | 互补 |
| 部署 | 无 | CD 部署流水线 | CI/CD 负责 |

**为什么还需要人工最终确认**：

即使全自动流水线的成功率超过 90%，关键节点（合并到 main、部署到生产）仍然需要人工审批。这不是技术限制，是**责任边界**——AI Agent 可以推荐，但人类对最终决策负责。

**结论**：四角色流水线是 Full Lifecycle Pipeline 的核心引擎。它可以扩展到从 Issue 到 PR 的全自动，但关键决策节点保留人工审批。

**怎么做**：
- 从四角色开始，验证每个角色的可靠性
- 逐步接入 CI/CD（先跑单元测试，再接入构建，再接入部署）
- 保留人工审批节点（合并到 main、生产部署）
- 监控流水线成功率，低于 90% 时回退到更高人工介入的模式
