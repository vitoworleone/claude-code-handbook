# 第 1 章 绪论

## 1.1 研究背景

### 1.1.1 编程 Agent 的兴起

过去十余年间，AI 辅助编程经历了三个阶段的演化：从最初的关键词补全（IntelliSense / Tabnine），到基于神经网络的 token 级补全（GitHub Copilot 2021），再到 2023 年之后以大语言模型为推理引擎、配合多轮对话与文件操作的"对话式编程助手"（ChatGPT Code Interpreter、GitHub Copilot Chat）。这三个阶段的共同特征是：模型对开发者的辅助仍以"建议"为主，最终操作由人类完成。

进入 2024 年以后，编程 AI 的形态发生了一次质变。Aider、Cursor、Cline、Devin、Continue、Codex CLI、Claude ​Code 等一系列产品先后出现，其共同特征是：**模型不再只是建议者，而成为执行者**。它直接读取仓库文件、修改源代码、运行 shell 命令、查询 Web、调用 MCP 工具，并在多个回合的"调用工具—接收结果—再次决策"的循环中推进任务。开发者从"接受/拒绝建议"的角色转变为"审核 Agent 的 patch"的角色。这一转变学术上常被称为 *coding agent* 或 *autonomous coding system* 的兴起。

支撑这一转变的关键技术是**Tool Use 协议**与**长上下文模型**。Anthropic 在 2024 年 3 月正式发布的 Claude 3 系列，提供了原生的 Messages API 与 `tool_use` / `tool_result` 块协议；OpenAI 在 2024 年也提供了 Function Calling 与 Assistants API；Google Gemini 提供了类似的 function declaration。这些 API 让模型能够以结构化的方式调用外部工具，并以结构化的方式接收工具结果。配合上下文窗口从早期的 8K / 32K 扩展到 200K（Claude 3 Opus）、1M（Claude Opus 4.7 / Gemini 1.5 Pro）的能力，单次会话能容纳整个中小型代码仓库的完整阅读历史。

技术能力具备后，工程化挑战随之浮现。能用 API 调用工具不等于做出一个稳定的 Agent。一个真正可用的编程 Agent 必须解决以下问题：

- **状态管理**：Agent 在多轮对话中如何记住自己已经做了什么？
- **错误恢复**：模型上下文溢出、API 暂时不可用、工具执行失败时如何继续？
- **权限边界**：什么操作需要用户确认？什么操作可以自动执行？
- **并发安全**：多个工具可以并发执行时，如何确保不会破坏状态一致性？
- **上下文压缩**：当对话超出上下文窗口时，如何在保留关键信息的前提下压缩历史？
- **持久化与恢复**：会话中途崩溃时如何恢复？
- **多 Agent 协作**：复杂任务如何拆分给子 Agent 并汇总结果？
- **扩展性**：如何让用户/企业接入私有工具与数据源？

这些问题没有教科书答案。每个产品都在以自己的方式探索。其中 Claude ​Code 因为来自 Anthropic 自身（同时也是 Claude 模型的提供者），代表了"模型团队对自己的模型应当如何被工程化使用"的一手观点；也正因如此，外界长期对其内部架构充满好奇。

### 1.1.2 Anthropic Claude ​Code 在工业界的定位

Claude ​Code 是 Anthropic 在 2024 年 12 月正式发布的官方编程 Agent CLI（同期推出的还有 Web 端的 claude.ai/code 与 IDE 扩展，但 CLI 是底层执行内核所在）。它的产品定位与同类产品相比有几个明显特征：

第一，**本地优先（local-first）**。绝大多数同类编程 Agent 把执行环境托管在云端沙箱（Devin、Replit Agent、Lovable）或浏览器内（v0.app、bolt.new）。Claude ​Code 选择在用户自己的开发机上运行，能直接访问用户的真实工作目录、git 历史、shell 环境、IDE 选区。这意味着：（1）它可以接管用户的实际项目而非演示项目；（2）权限模型必须做得更细，否则误删用户文件的代价是真实的；（3）它必须考虑 macOS / Windows / Linux 之间的差异。

第二，**终端 REPL 优先**。Cursor 是 IDE，Cline 是 VS Code 插件，Devin 是 Web Dashboard，Aider 也是终端但更接近一次性命令调用。Claude ​Code 以 React + Ink 在终端中渲染了一个完整的交互式 TUI，包括权限弹窗、流式输出、对话历史、状态栏、配置面板。这表明 Anthropic 认为**编程 Agent 不应该绑定到某个 IDE 厂商**，但又需要比裸命令行更丰富的交互。

第三，**模型团队自营的运行时**。Anthropic 既提供模型也提供 Agent CLI，让团队可以围绕同一个模型同时优化"模型如何调用工具"和"调用工具的 Agent 如何被工程化"。这与 OpenAI 在 2024 年推出 ChatGPT Code Interpreter 但把 Agent 的 CLI 留给社区构建（如 SWE-agent、auto-gpt）的路径截然不同。这种"双轨"路径让 Claude ​Code 中的某些机制——例如对 `stop_reason` 的不信任、对 `tool_use` 协议的严格执行、对 cache breakpoint 在 prompt 中的精确位置控制——体现了"内行人写运行时"的深度。

第四，**面向工程师的生产力工具，而非"通用聊天机器人 + 写代码模式"**。Claude ​Code 从一开始就 ship 了大量编程场景专属机制：CLAUDE.md 项目记忆、Skill 系统、Subagent、Plan 模式、Slash command、Hooks、Worktree 集成、tmux 集成。这些不是"加给聊天机器人的 Power User 功能"，而是构成产品骨架的一等公民。换句话说，Claude ​Code 是先回答"工程师在终端里需要一个什么样的 Agent"，再决定底层用什么模型；而不是先有一个聊天 API，再套一个写代码的壳。

到 2026 年初，Claude ​Code 在编程 Agent 领域已经成为讨论度最高的本地 Agent 产品之一。但因为 Anthropic 一直没有开源其实现，外界对其内部架构的认识只能来自三个渠道：官方 blog 与文档、Anthropic 工程师在公开演讲中的零散描述、用户基于黑盒行为的猜测。这种状态在 2026 年 3 月 31 日发生了根本性变化。

### 1.1.3 源码 Source Map 泄露事件

2026 年 3 月 31 日，安全研究者 Chaofan Shou 在分析 npm registry 上的 `@anthropic-ai/claude-code` 包时发现：包内的 `.js` 产物文件携带了未删除的 `.js.map` source map 文件。Source map 是现代 JavaScript 打包工具（如 Webpack、Rollup、Bun bundler）为方便调试而生成的额外文件，它记录了打包后代码与原始 TypeScript 源码之间的逐行映射。在开发环境中，source map 是必备调试工具；但在生产发布物中，它会让任何人都能反向还原原始源码——这是 npm 包发布的安全常识，几乎所有商业包都会在发布前剔除 source map。

Anthropic 在打包流程中显然遗漏了这一步。借助 source map，外界很快还原出了 Claude ​Code 的完整原始 TypeScript 工程，规模为：

| 维度 | 数据 |
|------|------|
| TypeScript 源文件（`.ts`） | 约 1,332 个 |
| TSX 源文件（`.tsx`） | 约 552 个 |
| JavaScript 文件（`.js`） | 约 18 个（stub / 兼容性命令） |
| **合计源文件** | **1,902 个** |
| **总代码行数** | **513,237 行** |

文件分布反映了系统的功能划分：

- `entrypoints/` —— 5 个入口文件（cli, init, mcp, sdk types, sandbox types）
- `tools/` —— 30+ 工具实现目录（BashTool, FileEditTool, AgentTool 等）
- `commands/` —— 50+ 命令实现目录（config, mcp, login, plan 等）
- `services/` —— 服务层（API 调用、MCP、压缩、分析、遥测）
- `components/` —— React 组件（Messages, PromptInput, PermissionRequest 等）
- `screens/` —— 顶级界面组件（REPL.tsx 约 4500+ 行，200+ 导入）
- `utils/` —— 工具函数（permissions, model, messages, swarm 等）

由于打包过程中 `package.json` 未被嵌入 source map，技术栈的确认主要依赖 import 语句与文件结构的静态推断。从大量 `import` 语句可以看到：

- 语言：TypeScript / TSX
- 运行时：Bun + Node 兼容（部分文件含 `import { feature } from 'bun:bundle'`，同时存在 Node API 与 Bun WebSocket 分支）
- CLI 框架：`@commander-js/extra-typings`
- TUI：React 19 + Ink 风格组件（自定义 reconciler）
- Schema：Zod
- 模型 API：`@anthropic-ai/sdk`
- MCP：`@modelcontextprotocol/sdk`

事件经过：Anthropic 在被通知后于次日修复了打包流程并发布了不带 source map 的新版本，但泄露的源码已经被多方下载并镜像。这次事件让外界**首次得以完整审视一个工业级编程 Agent 的内部架构**，对学术界与工业界都是空前的资料宝藏。

### 1.1.4 本论文的研究对象

本论文以 2026 年 3 月 31 日泄露的 Claude ​Code TypeScript 源码快照（共 1902 个文件、513,237 行）为研究对象。研究范围聚焦于 **Agent Runtime 内核**，即：

- 程序入口与启动链路
- 控制面 / TUI 层
- 执行内核（query_loop 状态机）
- 工具系统（Tool 抽象、StreamingToolExecutor、并发调度）
- 权限系统（PermissionMode、规则引擎、Fail-Closed 默认）
- 上下文管理（Compact 四策略、normalizeMessagesForAPI、context collapse）
- 记忆系统（四层 Memory、MEMORY.md 索引、Coalescing 提取）
- 多 Agent 协作（Agent Teams、Mailbox、后端注册表）
- 扩展机制（MCP、Hooks、Skills、Plugins）
- 会话持久化与故障恢复

不在研究范围内的部分：

- 模型本身的训练、推理、对齐
- claude.ai/code Web 端实现
- VS Code / JetBrains 等 IDE 扩展的前端实现
- Anthropic 内部的发布流程、合规体系
- 商业策略与定价

## 1.2 研究意义

### 1.2.1 学术意义：填补对工业级 Agent Runtime 的研究空白

学术界关于 LLM Agent 的研究在 2022–2025 年蓬勃发展，代表性工作包括 ReAct（Yao et al., 2022）、Toolformer（Schick et al., 2023）、Voyager（Wang et al., 2023）、AutoGen（Wu et al., 2023）、SWE-agent（Yang et al., 2024）、Agent Workflow Memory（Wang et al., 2024）。这些工作或聚焦在 prompt 设计与推理范式，或聚焦在多 Agent 编排框架，或聚焦在评测基准（SWE-bench、HumanEval、AgentBench）。

但学术界对 **"如何把一个 Agent 从原型工程化成产品"** 的研究相对缺失。这种缺失的根源是：

1. 工业级 Agent 产品大多闭源（Devin、Cursor、GitHub Copilot Workspace、Cody）；
2. 已开源的 Agent 实现（如 AutoGen 早期版本、LangGraph、SWE-agent）多为研究原型，距离生产级稳定性还有距离；
3. 工业产品的设计决策与权衡很少被系统化披露。

源码的意外泄露提供了一个稀有的"产品级 Agent Runtime 解剖样本"。对其架构、机制、设计权衡的系统化研究，可以填补学术界对工业级 Agent 工程化方法的认识空白，为后续相关研究提供可参照的基线。

### 1.2.2 工程意义：为自研 Agent 团队提供参考骨架

许多公司与团队正在尝试自研基于 LLM 的 Agent 系统。常见需求包括：内部代码助手、自动化运维 Agent、客服 Agent、销售 Agent、研究 Agent。在自研过程中工程团队经常遇到的问题，如：

- "Agent loop 怎么写才不会陷入无限循环？"
- "工具调用并发到底安不安全？"
- "上下文超过 200K 后怎么处理？"
- "记忆怎么管理？"
- "多 Agent 怎么协作？"
- "什么时候该用 streaming？"
- "权限怎么实现？"

这些问题在 Claude ​Code 源码中都能找到经过生产验证的答案。论文对这些机制的系统化解析，能让自研团队不必从零开始踩坑，而是站在 Anthropic 工程团队的肩膀上做选择。需要强调的是：Claude ​Code 的设计也不是唯一正确解，但作为已被广泛验证的工业级实现，它至少代表了**一个走得通的工程基线**。

### 1.2.3 研究价值：源码注释提供的设计动机

Claude ​Code 源码中存在大量长篇代码注释，详细解释设计动机、性能数据、安全考量、历史教训。例如：

- `cli.tsx:28-32` 注释解释了为什么 `--version` fast path 必须零 import；
- `main.tsx:1-8` 注释列出了三类必须在其它 imports 之前执行的 top-level side effects；
- `keychainPrefetch.ts:1-22` 注释精确给出了 65ms 的优化数据；
- `query.ts:552-557` 注释明确说明"不信任 `stop_reason === 'tool_use'`"；
- `tools.ts:354-359` 注释解释了 prompt cache 稳定性如何影响工具池的排序策略。

这些注释不是事后补充的文档，而是工程师在写代码时同步写下的设计 rationale。它们对理解"为什么这样设计"远比单看代码本身更有价值。本论文在引用源码时会刻意保留这些注释信息，使读者不仅能看到机制本身，也能看到机制背后的工程动机。

## 1.3 研究目标

### 1.3.1 还原 Claude ​Code 的整体架构

第一个研究目标是：以可读、可追溯的方式还原 Claude ​Code 的整体架构，包括：

- 程序启动的全链路（cli.tsx → main.tsx → init.ts / setup.ts → REPL.tsx → query.ts）
- 六层职责架构与运行时数据流
- 九种程序入口模式如何共用同一执行内核
- 关键组件之间的接口与边界

这一目标的产出是第 4 章的"详细设计"——以源码为锚，给出一份对 Claude ​Code 架构的系统化描述。

### 1.3.2 解析关键机制的设计思想

第二个研究目标是：对 Claude ​Code 中最具特色的关键机制做深度解析，包括：

- `query_loop` 六阶段循环与七种 transition.reason
- `StreamingToolExecutor` 边收流边执行的延迟优化
- 上下文压缩（Compact）的四种策略与双层防御
- 记忆系统的四层结构与 Coalescing 提取
- 多 Agent 协作的文件系统邮箱与 contextvars 身份隔离
- MCP 协议的接入方式
- Tool 能力对象的 9 个职责组与 Fail-Closed 默认

每个机制都从"是什么"、"为什么"、"怎么做"、"代价是什么"四个角度展开。这一目标的产出是第 5 章的"核心模块"——把抽象机制落到源码细节。

### 1.3.3 提炼可复用的工程原则

第三个研究目标是：从 Claude ​Code 的具体实现中提炼可被其他 Agent 项目复用的**工程原则**。例如：

- **协议正确性优先于输出漂亮**：在面对 stop_reason 不可靠、tool_result 必须配对等场景时，宁可牺牲代码简洁也要保证协议层正确
- **Fail-Closed 默认**：所有 Tool 默认 `isConcurrencySafe: false`、`isReadOnly: false`、`isDestructive: false`，未覆盖即视为不安全
- **Transcript 是 Source of Truth**：所有可恢复性、压缩、工具回流都依赖一份完整的 transcript，而不是分散在多处的状态
- **Coalescing 优于 Debounce**：在 memory 提取、文件 watcher 等场景下，保证最终状态一定被处理比追求执行次数最少更重要
- **Prompt Engineering 作为缺陷补丁**：12 条 doing_tasks 规则不是"最佳实践"，而是"已知缺陷的补丁列表"，每条对抗模型的一个具体默认倾向

这些原则的产出是第 9 章的"总结与展望"——把对单一系统的研究上升为对 Agent 工程化方法的总结。

## 1.4 研究方法

### 1.4.1 源码静态分析

主要方法是从 source map 还原后的 TypeScript 源码出发，进行静态分析。具体技术包括：

- **import 链分析**：从顶层 import 推断模块依赖图与初始化顺序
- **类型签名分析**：从函数签名、interface、type 推断数据流
- **注释挖掘**：提取设计动机、性能数据、历史教训
- **关键路径追踪**：从入口逐步追到执行内核，标记每个分支点的源码位置
- **不变量识别**：找出代码中反复出现的约束（如"Transcript 在 query 前写入"、"tool_use 必须有对应 tool_result"）

### 1.4.2 Python 复刻验证

为了验证对 Claude ​Code 机制的理解，我们用 Python（约 1.5 万行）等价实现了核心 Agent Runtime（位于 `src/cc-python-runtime/`）。该复刻覆盖了：

- query_loop 六阶段循环
- StreamingToolExecutor
- 22 个内置工具
- 三级权限模式
- 四种 Compact 策略
- 四层 Memory 系统
- 文件系统 Mailbox 与 Agent Teams
- MCP 协议客户端
- Hooks 与 Skills

复刻不是为了产品化，而是为了"通过手写来验证理解"。当某个机制只有读源码无法说清时，尝试用 Python 还原它就能暴露理解中的盲点。复刻完成后通过 498 个单元测试与若干集成测试验证行为对齐。**本论文以 Claude ​Code TS 原版为论述主体**，Python 复刻仅在需要给出可运行示例时被引用。

### 1.4.3 行为对比

对一些通过源码难以确认的细节（例如某个 transition.reason 的实际触发条件、某种错误下的具体重试次数），我们通过运行官方 CLI 并收集 transcript / log 进行行为验证。这种"源码假设 + 行为验证"的回环让结论更可靠。

### 1.4.4 设计决策溯源

对每个关键设计决策，我们尽量从三个来源溯源其动机：

1. **源码注释**：工程师同步写下的 rationale
2. **commit 信息或 changelog**：版本演化中体现的修复方向（可用部分有限，因为泄露的是 build 产物，没有 git 历史）
3. **官方公开材料**：Anthropic blog、技术演讲、产品发布说明

无法溯源的设计决策会被明确标注"推测"，避免把对源码的解读包装成原作者的意图。

## 1.5 论文结构

本论文按 9 章组织，每章主题如下：

| 章节 | 主题 | 核心问题 |
|------|------|---------|
| 第 1 章 | 绪论 | 为什么研究 Claude ​Code？怎么研究？ |
| 第 2 章 | 技术基础 | Claude ​Code 站在哪些技术肩膀上？ |
| 第 3 章 | 需求分析 | Claude ​Code 要解决什么问题？ |
| 第 4 章 | 详细设计 | Claude ​Code 的整体架构是什么样？ |
| 第 5 章 | 核心模块 | 每个关键模块怎么实现？ |
| 第 6 章 | 稳定性保障 | 怎么保证系统不崩？ |
| 第 7 章 | 部署实践 | 怎么把它打包、发布、运行？ |
| 第 8 章 | 测试与评估 | 怎么验证它能用？怎么和竞品比？ |
| 第 9 章 | 总结与展望 | 我们学到了什么？未来怎么走？ |

章节之间的逻辑关联是：

```
第1章（动机）
    ↓
第2章（外部技术基础） ← 第3章（用户与场景需求）
    ↓                     ↓
第4章（整体设计）  ←─────────┘
    ↓
第5章（核心模块逐一深入）
    ↓
第6章（稳定性视角横切） + 第7章（部署视角横切）
    ↓
第8章（评估与对照） → 第9章（结论与展望）
```

第 4 章给出系统的整体骨架；第 5 章逐一深入每个核心模块；第 6 章与第 7 章分别从稳定性与部署两个横切视角再次审视系统；第 8 章对系统做评估并与竞品对比；第 9 章总结。这种安排让读者既能纵向深入某个模块，也能横向理解系统在不同维度的取舍。

附录提供术语表、源码索引、七种 transition.reason 速查、六个 transcript 写入点速查、30+ 内置工具清单等参考材料。

---

## 本章小结

本章交代了论文的研究背景、研究意义、研究目标、研究方法与章节结构。核心要点是：

- 2026 年 3 月 31 日 source map 泄露事件让 Claude ​Code 的 TypeScript 源码（1902 文件、513,237 行）首次完整公开
- 这是工业界首次完整暴露生产级 Agent Runtime 的全部实现
- 本论文以这份源码为对象，系统化解析 Agent Runtime 的工程化方法
- 研究方法以源码静态分析为主，辅以 Python 复刻验证与官方行为对比
- 9 章结构覆盖从动机到结论的完整论述链路

接下来的第 2 章将先梳理 Claude ​Code 所依赖的技术基础——Anthropic Tool Use 协议、MCP 协议、React+Ink、Bun、Zod 等——为后续章节奠定语境。
