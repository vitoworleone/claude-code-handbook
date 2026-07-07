# 第十二章：面向 Vibe Coding 与 Skills 设计的阅读路线

[返回总目录](../README.md)

---

## 1. 这篇文档解决什么问题

如果你的目标不是“完整理解 Claude Code 的全部工程细节”，而是：

1. 借 Claude Code 改进自己的 **vibe coding** 工作流
2. 学会设计更有效的 **skills / agent 提示体系**

那么研究重点必须重排。

很多人直觉上会优先去看：

- 多 Agent 编排
- swarm / remote / bridge
- 复杂控制面 UI

这些方向当然重要，但对“提高日常 coding agent 的实际使用效果”并不是最高杠杆。

**结论先行**：如果你的目标是提升 vibe coding 和 skills 设计，应该优先研究：

1. **Context 是如何被装配的**
2. **Skill 是如何被发现、解析、触发和注入的**
3. **Tool 调用是如何纳入统一执行闭环的**
4. **用户输入是如何在进入模型前被编译的**
5. **长会话中上下文压缩后，系统如何重建工作状态**

换句话说，先研究“单 Agent 怎么稳定地像一个工作台一样运转”，再研究“多 Agent 如何协作”。

---

## 2. 先调整研究视角

如果从产品宣传的角度看 Claude Code，很容易被这些标签吸引：

- agent
- background tasks
- teammate
- swarm
- MCP

但如果从 **vibe coding** 的角度看，真正决定体验好坏的，通常不是这些平台标签，而是下面几个更底层的问题：

1. 模型每一轮到底看到了什么上下文
2. 哪些信息是自动注入的，哪些不是
3. 技能是如何被触发的，是否会污染主上下文
4. 工具调用的边界在哪里，何时允许并发，何时必须审批
5. 会话变长后，系统如何压缩而不丢工作状态

因此，这份路线图的核心原则是：

> **把 Claude Code 当作“上下文编排器 + 技能运行时 + 工具闭环系统”来研究，而不是先把它当成“多 Agent 平台”来研究。**

---

## 3. 第一优先级：Context 装配

### 3.1 为什么最先看这个

对 vibe coding 来说，最值钱的不是“模型更聪明”，而是“模型每一轮都站在正确的工作台上”。

Claude Code 明显不是把用户输入原样丢给模型，而是在系统层面先拼好一套高价值上下文，例如：

- 当前日期
- `git status` 快照
- 最近提交
- `CLAUDE.md`
- memory 文件
- 当前已激活的能力边界

这比单纯堆更多 prompt 更重要。

### 3.2 重点文档

- [`04f-context-management.md`](./04f-context-management.md)
- [`01-architecture-overview.md`](./01-architecture-overview.md)

### 3.3 重点源码

- [`../src/context.ts`](../src/context.ts)
- [`../src/services/compact/compact.ts`](../src/services/compact/compact.ts)
- [`../src/services/compact/autoCompact.ts`](../src/services/compact/autoCompact.ts)
- [`../src/bootstrap/state.ts`](../src/bootstrap/state.ts)

### 3.4 你要提炼什么设计原则

- 上下文不是聊天历史，而是工作环境描述
- 自动注入的内容必须高价值、低噪音
- 长会话系统必须预留压缩预算，不能把窗口用满后才处理
- 压缩后要做状态重建，而不是简单截断

### 3.5 对 skills 设计的直接启发

如果 skill 只是“加一大段说明文字”，很快就会污染主上下文。Claude Code 的思路更接近：

- 平时只注入稳定的全局上下文
- 真正需要的临时信息，在 skill 或工具调用时局部补充
- 压缩后继续保留“当前正在做的事”而不是所有历史细节

---

## 4. 第二优先级：Skills 机制

### 4.1 为什么这是 skills 设计的核心

如果你想借鉴 Claude Code 来设计自己的 skills，最重要的不是复制语法，而是理解：

- skill 从哪里来
- skill 如何被发现
- skill 如何被转换成可执行的 prompt 单元
- skill 的作用域如何被控制
- skill 如何携带实时环境信息

Claude Code 对 skill 的处理，本质上不是“收藏若干 prompt”，而是把 skill 做成一种 **上下文编译单元**。

### 4.2 重点文档

- [`04c-skills-implementation.md`](./04c-skills-implementation.md)

### 4.3 重点源码

- [`../src/skills/loadSkillsDir.ts`](../src/skills/loadSkillsDir.ts)
- [`../src/skills/bundledSkills.ts`](../src/skills/bundledSkills.ts)
- [`../src/utils/promptShellExecution.ts`](../src/utils/promptShellExecution.ts)
- [`../src/commands.ts`](../src/commands.ts)

### 4.4 重点观察点

#### 1. Skill 的元数据不只是装饰

尤其要关注这些字段：

- `description`
- `allowed_tools`
- `model`
- `effort`
- `user_invocable`
- `paths`
- `context`
- `agent`
- `shell`

这些字段共同决定了：这个 skill 何时出现、谁能调用、调用时带什么能力、是否和文件变化绑定。

#### 2. `paths` 是高杠杆设计

这是非常值得借鉴的一点。

它意味着：

- skill 不一定靠用户显式输入触发
- skill 可以和文件路径 / 文件变化绑定
- skill 可以变成“精准介入”的上下文补丁，而不是一直常驻

这对 vibe coding 很关键，因为你需要的是 **在正确时机出现的能力**，而不是一堆永远挂着的重 prompt。

#### 3. Skill 可以携带实时环境信息

Markdown 里的 Shell 执行让 skill 不再只是静态说明，而能把：

- 当前 git 状态
- 项目结构
- 最新日志
- 运行结果

动态编译进 prompt。

这相当于把 skill 从“提示模板”升级为“提示生成器”。

### 4.5 你要提炼什么设计原则

- skill 不是 prompt 仓库，而是运行时注入单元
- skill 应该尽量短、局部、精准触发
- skill 的价值不在长，而在触发条件与环境适配
- 能从环境实时求值的内容，不要预先写死

---

## 5. 第三优先级：Tool 调用闭环

### 5.1 为什么这比“会不会调工具”更重要

vibe coding 体验强不强，不取决于“有没有工具”，而取决于：

- 模型敢不敢稳定调用工具
- 调工具后系统能不能继续推进
- 工具边界是否足够清晰
- 用户是否能安全地给出权限

Claude Code 的强点在于，工具不是外挂，而是执行主循环的一部分。

### 5.2 重点文档

- [`04b-tool-call-implementation.md`](./04b-tool-call-implementation.md)
- [`05-differentiators-and-comparison.md`](./05-differentiators-and-comparison.md)

### 5.3 重点源码

- [`../src/Tool.ts`](../src/Tool.ts)
- [`../src/tools.ts`](../src/tools.ts)
- [`../src/services/tools/toolOrchestration.ts`](../src/services/tools/toolOrchestration.ts)
- [`../src/utils/permissions/permissionSetup.ts`](../src/utils/permissions/permissionSetup.ts)

### 5.4 重点观察点

#### 1. Tool 有安全语义

重点看这些接口语义：

- 是否并发安全
- 是否只读
- 是否破坏性
- 是否需要用户交互
- 如何做 permission matching

这说明 Claude Code 不是“模型输出工具名 -> 程序就执行”，而是把工具当成带安全元数据的能力对象。

#### 2. Permission 是主干，不是补丁

这对你自己的 skill 设计很重要。

如果你未来设计 skill 时只想着“让模型能做更多事”，会很快失控。Claude Code 的思路是：

- 能力增强必须跟 permission system 一起设计
- 用户审批是工具闭环的一部分
- auto mode 也不是完全裸奔，而是会剥离危险规则

### 5.5 你要提炼什么设计原则

- tool 设计要先写能力边界，再写功能
- 让模型多用工具，不等于让工具更随意
- 用户越容易理解工具风险，越愿意给 agent 放权

---

## 6. 第四优先级：输入编排

### 6.1 为什么这个方向容易被低估

很多人设计 coding agent 时，只关注“系统 prompt 怎么写”。但 Claude Code 很明显在用户输入进入模型之前做了大量编排。

也就是说，真正送给模型的并不是原始输入，而是经过处理的任务对象。

### 6.2 重点源码

- [`../src/utils/processUserInput/processUserInput.ts`](../src/utils/processUserInput/processUserInput.ts)
- [`../src/utils/processUserInput/processTextPrompt.ts`](../src/utils/processUserInput/processTextPrompt.ts)
- [`../src/utils/processUserInput/processSlashCommand.tsx`](../src/utils/processUserInput/processSlashCommand.tsx)

### 6.3 重点观察点

- slash command 与普通 prompt 的分流
- pasted content、IDE selection、attachment 的注入
- hook 对输入的拦截和补充
- `allowedTools`、`model`、`effort` 如何在输入阶段被设定

### 6.4 你要提炼什么设计原则

- 输入框不是文本框，而是任务入口编排器
- prompt 设计不应该只放在 system prompt 里
- 真正高质量的 agent 交互，很多结构化约束发生在“送模前”

---

## 7. 第五优先级：长会话稳定性

### 7.1 为什么这对 vibe coding 至关重要

你真正使用 coding agent 时，最痛的往往不是“第一轮回答差”，而是：

- 会话一长就开始跑偏
- 技能调用后上下文变脏
- 正在做的任务在压缩后丢失
- 刚刚打开的文件、计划、工具状态全忘了

Claude Code 在这方面投入很深，这也是它值得研究的地方。

### 7.2 重点文档

- [`04f-context-management.md`](./04f-context-management.md)

### 7.3 重点源码

- [`../src/services/compact/compact.ts`](../src/services/compact/compact.ts)
- [`../src/services/compact/autoCompact.ts`](../src/services/compact/autoCompact.ts)
- [`../src/bootstrap/state.ts`](../src/bootstrap/state.ts)

### 7.4 重点观察点

- 自动压缩阈值怎么定
- 为什么要为 summary 预留 token 预算
- 压缩后如何 reinject：
  - 文件附件
  - 当前 plan
  - 当前 skill
  - 已激活的 tools / MCP 声明

### 7.5 你要提炼什么设计原则

- 长会话能力本质上是“状态重建能力”
- 压缩不是丢历史，而是重组工作台
- 如果你的 skill 会严重放大上下文体积，就必须同步考虑压缩后的恢复策略

---

## 8. 第二层再看：Query / Agent 主循环

等你把前面五个方向看明白后，再看这一层会更有收益。

### 8.1 重点文档

- [`01-architecture-overview.md`](./01-architecture-overview.md)
- [`05-differentiators-and-comparison.md`](./05-differentiators-and-comparison.md)
- [`07-code-evidence-index.md`](./07-code-evidence-index.md)

### 8.2 重点源码

- [`../src/query.ts`](../src/query.ts)
- [`../src/QueryEngine.ts`](../src/QueryEngine.ts)

### 8.3 这一层到底在帮你理解什么

它会告诉你：

- 为什么 Claude Code 能让 REPL、subagent、background agent 共用同一执行内核
- 为什么 tool / permission / compact / hooks 都能挂进一个统一主循环
- 为什么 skill 设计不能脱离主循环思考

但这层适合放在第二阶段看，因为如果你先看它，很容易被架构复杂度吸走，反而忽略对你最有直接帮助的内容。

---

## 9. 暂时不要优先研究的方向

如果你的目标是提升 vibe coding 与 skills 设计，下面这些方向可以先降优先级：

- `swarm`
- `remote`
- `bridge`
- `teams`
- 大量控制面 UI 细节
- `voice`
- `vim`
- `mobile`
- telemetry / feedback / growthbook 等运营与平台支撑模块

不是这些不重要，而是它们对你当前目标的杠杆不如：

- context
- skills
- tool loop
- input processing
- compaction

---

## 10. 最值得带走的五个判断

### 10.1 对 vibe coding

1. 上下文不是历史记录，而是工作台
2. 输入框不是聊天框，而是任务编排入口
3. 长会话能力不是靠更大窗口，而是靠更好的压缩与重建

### 10.2 对 skills 设计

1. skill 不是长 prompt，而是可触发、可组合、可运行时求值的上下文单元
2. skill 设计必须和 tool / permission / compaction 一起考虑，不能孤立设计

---

## 11. 建议阅读顺序

如果只看一轮，我建议按这个顺序读：

1. [`04f-context-management.md`](./04f-context-management.md)
2. [`../src/context.ts`](../src/context.ts)
3. [`04c-skills-implementation.md`](./04c-skills-implementation.md)
4. [`../src/skills/loadSkillsDir.ts`](../src/skills/loadSkillsDir.ts)
5. [`../src/utils/promptShellExecution.ts`](../src/utils/promptShellExecution.ts)
6. [`04b-tool-call-implementation.md`](./04b-tool-call-implementation.md)
7. [`../src/Tool.ts`](../src/Tool.ts)
8. [`../src/services/tools/toolOrchestration.ts`](../src/services/tools/toolOrchestration.ts)
9. [`../src/utils/processUserInput/processUserInput.ts`](../src/utils/processUserInput/processUserInput.ts)
10. [`../src/services/compact/compact.ts`](../src/services/compact/compact.ts)
11. [`../src/query.ts`](../src/query.ts)
12. [`../src/QueryEngine.ts`](../src/QueryEngine.ts)

---

## 12. 最终建议

如果你之后要把这些研究结果变成自己的方法论，不要只记录“Claude Code 做了什么”，而要记录下面三类内容：

1. **它解决了什么真实问题**
2. **它为什么用这种方式解决**
3. **这套做法在你自己的 vibe coding / skills 系统里应该被保留、缩减还是改造**

真正有价值的，不是把 Claude Code 复刻一遍，而是把它背后的设计判断提炼出来。
