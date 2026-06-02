# 第 3 章 需求分析

> 本章从用户视角与系统视角两条线索出发，系统化梳理 Claude ​Code 必须解决的问题。需求分析的价值不是"功能清单的完整性"，而是"理解每个功能为什么必须存在"——这为第 4 章的架构设计和第 5 章的模块实现提供需求依据。

---

## 3.1 用户角色与典型场景

### 3.1.1 个人开发者

**画像**：单人或小团队的开发者，使用 Claude ​Code 在本地开发机上完成日常编程任务。

**典型场景**：

1. *快速理解陌生代码库*：进入一个 GitHub clone 的项目，问"这个项目是干什么的？入口在哪里？"
2. *基于自然语言修改代码*："把 user.py 中的 password 字段加上 bcrypt 哈希"
3. *自动调试*：贴上错误堆栈，让 Agent 定位并修复 bug
4. *生成测试用例*："给这个函数写 5 个边界用例"
5. *重构*："把这三个文件中的重复逻辑抽到一个 helper 模块"

**对运行时的需求**：

- 启动快（用户不希望每次开终端等 2 秒）
- 流式输出（看到模型在做什么比等结果更重要）
- 文件读写要可靠（不能把用户文件破坏）
- 可中断（输错了想立刻停下）
- 不需要联网外服务（不希望工作目录数据离开本地）

### 3.1.2 团队工程师与 CI/CD

**画像**：在团队中作为生产力工具引入 Agent，用于 PR 审查、issue 分类、自动化任务、定期重构。

**典型场景**：

1. *自动 PR 描述*：在 CI 上运行 `claude -p "为这个 PR 写描述"`，输出到 GitHub
2. *issue 分类*：定时扫描新 issue，让 Agent 给出 severity / area 标签
3. *运行时迁移*：批量把 `class Component extends React.Component` 改成 hooks
4. *Code review*：作为 PR 检查器，运行 `claude -p "review this diff for security issues"`
5. *定期重构*：每周扫一次代码库，自动整理 import / 修 lint 警告

**对运行时的需求**：

- 非交互模式（CI 中没有 TTY）
- 单次执行后退出（不留进程）
- 可通过 stdin / 参数传入 prompt
- 可读 settings.json 与环境变量做配置
- 不应该在 CI 中弹出权限对话框
- 输出可以管道给下游工具

### 3.1.3 高级用户与平台工程师

**画像**：希望深度定制 Claude ​Code 的高级用户，包括内部 Agent 平台搭建者、企业 DevEx 团队、研究者。

**典型场景**：

1. *接入私有 MCP Server*：让 Agent 能查内部 Wiki、Jira、ServiceNow
2. *自定义 Skills*：把团队内部的工作流（"如何提交 PR"、"如何写 ADR"）固化为可触发的 skill
3. *Pre/Post Tool Hooks*：在工具调用前后执行审计、合规检查
4. *多 Agent 编排*：让 Coordinator Agent 拆解任务给多个 Teammate Agent
5. *自研 Subagent Definition*：定义"专门做代码审查"的 agent，限制其只能用读类工具
6. *把 Claude ​Code 当 MCP Server 嵌入其他 IDE*：让 Cursor / VS Code 复用 Claude ​Code 的工具实现

**对运行时的需求**：

- 完善的扩展点（MCP、Hooks、Skills、Subagents）
- 强大的权限模型（能精细到工具、参数、命令模式）
- 可观测（startupProfiler、OTel span、permission denials）
- 可被嵌入（MCP Server 模式、SDK 模式）
- 可热更新（hooks watcher、settings change detector）

### 3.1.4 企业 IT 与合规

**画像**：在企业环境中部署 Claude ​Code 的 IT 管理员，关注合规、安全、可审计。

**典型场景**：

1. *MDM 管理*：通过企业 MDM 推送统一的 settings（如禁用某些工具、强制使用代理）
2. *审计*：所有工具调用与 prompt 必须可追溯到具体用户
3. *数据脱敏*：不希望源代码片段被发到第三方 telemetry
4. *合规审批*：模型调用前必须经过本地合规系统
5. *沙箱隔离*：在敏感环境下让 Agent 在隔离容器内运行

**对运行时的需求**：

- 多级 settings 优先级（MDM > 全局 > 项目 > 本地）
- Trust Dialog（首次进入工作目录前确认）
- Telemetry 可关闭
- 完整的 audit log
- 可注入企业 CA 证书
- 工具调用可经 Hook 拦截

### 3.1.5 学术研究者

**画像**：研究 Agent 范式、上下文管理、多 Agent 协作的学术研究者，把 Claude ​Code 作为研究平台。

**典型场景**：

1. *评测基准对比*：在 SWE-bench / HumanEval 上跑 Claude ​Code，与其他 Agent 对比
2. *机制消融*：关闭 StreamingToolExecutor，看延迟变化
3. *Prompt 实验*：替换 system prompt 的某段，看行为变化
4. *记忆策略对照*：禁用 auto-memory，看长会话表现

**对运行时的需求**：

- `--dump-system-prompt` 之类的内省工具
- 可通过环境变量切换特性（如 `CLAUDE_CODE_SIMPLE=1`）
- 详细的 startup profiling
- 可记录完整 transcript 与 token 用量

## 3.2 功能性需求

### 3.2.1 多入口形态

**需求来源**：上述五类用户的使用形态各异，不可能用单一入口满足所有需求。

**功能描述**：Claude ​Code 必须支持多种入口形态，覆盖交互、自动化、嵌入、远程等场景。源码确认的入口形态包括：

| 入口模式 | 触发条件 | 典型用户 |
|---------|---------|---------|
| REPL（默认）| `claude` 无参数 | 个人开发者 |
| Headless / SDK | `-p` / `--print` / `--sdk` | CI/CD、脚本 |
| MCP Server | `mcp` 子命令 | 平台工程师、IDE 集成 |
| Remote / Bridge | `remote` / `bridge` 子命令 | 远程协同 |
| `--bare` 模式 | `--bare` 标志 | 性能敏感场景、CI |
| 管道模式 | stdin 非 TTY | shell 管道 |
| 脚本模式 | 命令行参数作为单次输入 | 单次任务 |
| 守护进程模式 | `daemon` / `--bg` / `runner` | 后台长任务 |
| IDE 集成 | VS Code / JetBrains 扩展 | IDE 用户 |

**关键约束**：所有入口必须**共用同一执行内核**（query_loop），否则工具行为、权限判定、记忆系统会在不同入口下出现不一致——这是平台级 Agent 必须避免的灾难。

### 3.2.2 30+ 内置工具

**需求来源**：编程 Agent 的核心能力是与代码库的交互。最小可用的工具集必须覆盖"读 / 写 / 改 / 搜 / 跑 / 问"几大类。

**功能描述**：Claude ​Code 内置 30+ 工具，按类别可分为：

**Shell 与文件类**：
- `BashTool`：执行 shell 命令
- `PowerShellTool`：Windows PowerShell
- `FileReadTool`：读取文件（文本 / 图片 / PDF / Notebook）
- `FileEditTool`：原地编辑文件
- `FileWriteTool`：写入新文件
- `NotebookEditTool`：编辑 Jupyter notebook

**搜索类**：
- `GlobTool`：文件路径 glob 匹配
- `GrepTool`：文本搜索（基于 ripgrep）

**Web 类**：
- `WebFetchTool`：抓取网页内容
- `WebSearchTool`：网页搜索

**任务管理类**：
- `TodoWriteTool`：待办事项管理
- `EnterPlanModeTool` / `ExitPlanModeV2Tool`：进入/退出计划模式
- `TaskOutputTool`：任务输出
- `TaskStopTool`：停止任务

**Agent 与扩展类**：
- `AgentTool`：创建子 agent
- `SkillTool`：调用 skill
- `AskUserQuestionTool`：向用户提问
- `ToolSearch`：在工具池中搜索可用工具

**MCP 资源类**：
- `ListMcpResourcesTool`：列出 MCP 资源
- `ReadMcpResourceTool`：读取 MCP 资源

**其它**：
- `SyntheticOutputTool`：合成输出（用于测试 / 自动化）
- ant-only、feature-gated、environment-gated 工具

**关键约束**：每个工具必须满足 Tool 协议（详见第 4.3 节），包括 schema、permission、ui rendering、安全属性等 9 个职责组的字段。

### 3.2.3 流式工具执行

**需求来源**：开发者 CLI 对"工具开始跑了没有"的等待感非常敏感。如果模型说 1000 字才在末尾调一个 30 秒命令，用户感知延迟会显著恶化。

**功能描述**：在模型流式响应期间，一旦 `tool_use` 块完整出现就立即启动工具，不等整个响应结束。

**性能数据**（源码注释提及）：可减少端到端延迟 30-50%。

**关键约束**：
- 物理执行可以提前，但**协议语义必须等价**：最终仍然要把 assistant tool_use 和 user tool_result 以正确顺序放回 messages
- 必须支持中途失败 discard（如 reactive compact 触发时，已启动工具的结果不能渗漏到新尝试）
- 必须支持 abort（生成 synthetic tool_result 保证 tool_use 配对）

### 3.2.4 权限管理

**需求来源**：本地优先的 Agent 必须解决"什么操作需要用户确认"的问题。完全自动化会导致用户误删文件，完全确认会让 Agent 慢到不可用。

**功能描述**：

**三级 PermissionMode**：

| 模式 | 读操作 | 编辑操作 | 命令执行 |
|------|--------|----------|----------|
| `BYPASS` | 自动 | 自动 | 自动 |
| `ACCEPT_EDITS` | 自动 | 自动 | 需确认 |
| `DEFAULT` | 自动 | 需确认 | 需确认 |

**规则引擎**：
- Always Allow Rules：用户明确允许的工具/命令模式
- Always Deny Rules：用户明确禁止的工具/命令模式
- Per-call Permission：每次调用前根据 mode + 规则 + 工具自定义 checkPermissions 决定

**fail-fast 策略**：非交互环境（CI / Headless）下，如果需要权限确认但没有 UI 可用，立即失败，不静默通过。

**关键约束**：
- 权限决策必须在 `tool.call()` **之前**完成（否则副作用已经发生）
- 未知工具默认 ASK（fail-closed），不允许"白名单遗漏 = 自动允许"
- 权限模式可以动态升级（`/permissions` 命令）但不能因为模型请求降级

### 3.2.5 上下文压缩

**需求来源**：Claude 模型上下文窗口最大 200K token（Sonnet）/ 1M token（Opus 4.7）。长会话或重型工具调用（Bash 命令长输出、文件全量读取）很容易把上下文塞满。如果不主动压缩，会话会因 `prompt_too_long` 错误而崩溃。

**功能描述**：

**四种压缩策略**：

| 策略 | 触发条件 | 核心机制 |
|------|---------|---------|
| 手动 compact | 用户执行 `/compact` | 立即生成摘要 + 复灌状态 |
| 自动 compact | Token 接近阈值（context window - 13K buffer） | 主动压缩，保留最近 4 轮 |
| Session Memory compact | 有 Session Memory 文件时 | 把 Session Memory 注入摘要 |
| Reactive compact | API 返回 `prompt_too_long` (413) | 应急压缩，每轮限 1 次 |

**双层防御**：
- `COMPACT_SYSTEM_PROMPT`：事后防御，指导摘要器保留关键决策、文件路径、未完成任务
- `SUMMARIZE_TOOL_RESULTS`：事前防御，引导模型把关键信息从工具结果搬到自己的回复，避免压缩时丢失

**压缩后复灌**：
- summary（摘要文本）
- 当前打开文件的 attachments
- 当前 plan / plan mode 状态
- 已调用 skills
- 当前工具能力声明
- agent listing
- MCP instructions
- session start hook messages

**关键约束**：压缩不是"保留摘要"，而是让模型压缩后仍然"坐在同一张工作台前"——文件、计划、模式、工具能力都不能丢。

### 3.2.6 记忆系统

**需求来源**：会话级压缩解决了"上下文太多"，但解决不了"下次还记得"。用户希望 Agent 跨会话保留对自己的认知（"这个用户喜欢简短回答"）、对项目的认知（"这个项目用 pytest 而非 unittest"）、以及一些反馈纠正（"上次我说过不要 mock 数据库"）。

**功能描述**：

**四层 Memory 体系**：

| 层级 | 时间范围 | 内容 | 文件位置 |
|------|---------|------|---------|
| Auto Memory | 跨会话 | 用户/项目长期协作信息 | `~/.claude/memory/` 等多个 scope |
| Session Memory | 会话级 | 当前会话摘要 | `~/.claude/projects/{project}/session_memory.md` |
| Agent Memory | 跨会话 | 某类 agent 的专属长期记忆 | `~/.claude/agents/{agent}/memory/` |
| Team Memory | 跨会话 | 团队共享知识 | `~/.claude/teams/{team}/memory/` |

**存储模型**：
- 每条记忆是一个单独的 Markdown 文件
- `MEMORY.md` 只维护索引链接和一行描述
- 硬截断保护：MEMORY.md 上限 200 行 / 25KB

**提取策略**：
- Coalescing（而非 Debounce）：设置 dirty 标记，当前提取完成后立即重跑，保证最终状态一定被扫描

**触发判定**：
- 初始化需要达到 token 阈值
- 后续需要 token 增长阈值
- tool call 次数也参与判断
- 最后一轮没有 tool call 视为自然断点

**关键约束**：
- Memory 不是黑盒数据库，必须可手动编辑、可审计、可 diff
- 写入文件夹必须 `mode: 0o700`，文件必须 `mode: 0o600`（防止信息泄露）

### 3.2.7 多 Agent 协作

**需求来源**：复杂任务（如"重构整个 auth 模块"）单 Agent 容易陷入长链推理。拆分给多个子 Agent 并行处理（"一个 agent 看 backend，一个 agent 看 frontend，一个 agent 写测试"），可以缩短端到端时间。

**功能描述**：

**三种角色**：

| 角色 | System Prompt | 工具集 |
|------|---------------|--------|
| Team Lead | 完整版（11 段拼装） | 全部工具 |
| Teammate | DEFAULT_AGENT_PROMPT + TEAMMATE_ADDENDUM | 排除 Agent/TeamCreate/TeamDelete/AskUserQuestion |
| Coordinator | COORDINATOR_SYSTEM_PROMPT + 完整版 | 全部（但 prompt 指导只用 Agent/SendMessage/TaskStop） |

**生命周期 5 步**：
1. TeamCreate：创建团队，激活 TeamContext
2. Spawn Teammates：AgentTool 派生 teammate，注册到 TeamFile
3. Teammate 执行：独立 query_loop，后台 asyncio.Task 运行
4. Lead 收结果：inbox 轮询，`<task-notification>` 注入 transcript
5. TeamDelete：清理团队目录

**通信机制**：文件系统邮箱 `~/.claude/teams/{team}/inboxes/{agent}.json`

**身份隔离**：contextvars 提供协程级身份隔离，同进程内多 Task 并发安全

**后端注册表**：
- `in-process`（默认）：同进程 asyncio.Task
- `tmux`：tmux 窗格独立运行（可视化）
- `iterm2`：iTerm2 split pane

**关键约束**：
- Teammate 必须排除会无限派生子 agent 的工具（避免递归 spawn）
- 团队邮箱必须跨进程安全（at-least-once 消息投递）
- 团队 leader 必须能优雅清理失败 / 残留 teammate

### 3.2.8 扩展机制

**需求来源**：核心运行时无法预知所有用户的需求。必须提供清晰的扩展点，让用户/企业接入私有工具、自定义工作流、审计逻辑。

**功能描述**：

**MCP**：
- 接入外部 MCP Server，把其暴露的 tools / resources 融合进内部能力池
- 工具命名：`mcp__{serverName}__{toolName}` 避免与内建工具冲突

**Hooks**：
- `PreToolUse`：工具调用前拦截，可改 input、拒绝、注入额外消息
- `PostToolUse`：工具调用后拦截，可改 result、追加消息
- `UserPromptSubmit`：用户输入提交后拦截
- `Stop`：Agent 准备结束时拦截，可强制继续
- Hook 通过 shell 命令实现，stdout 是事件流

**Skills**：
- frontmatter 定义 metadata（skillName / description / allowedTools / argumentNames / etc.）
- 三层目录（managed / user / project）
- Slash command 触发或模型自主触发
- 支持内嵌 shell 与变量替换

**Plugins**：
- 提供更高层的能力包（多 skill / 多 hook / 多 MCP server 的组合）

**Agent Definitions**：
- 自定义 agent 的 system prompt、tool set、permission mode
- 用于 AgentTool 派生时指定 agent 类型

**关键约束**：扩展机制必须**对运行时透明**——核心 query_loop 不应该因为新增 MCP server / hook / skill 而需要修改。

### 3.2.9 会话持久化与故障恢复

**需求来源**：用户中途按 Ctrl+C、机器断电、CLI 升级等场景下，已经做的工作不应该丢失。

**功能描述**：

**Session 持久化**：
- 每个会话有唯一 session id
- transcript 以 JSONL 格式流式写入磁盘
- 支持 `claude -c <session-id>` 恢复

**Transcript validation / recovery**：
- 异常退出可能导致 transcript 损坏（如最后一行不完整、tool_use 没有对应 tool_result）
- `validate_transcript()` 在加载时修复异常

**TaskRegistry**：
- 后台任务（Subagent / Background agent）的状态快照
- 在 CLI 重启后可恢复未完成的后台任务

**关键约束**：
- 用户输入一旦被接受，必须先写入 transcript 再进入模型调用（否则崩溃后无法判断输入是否已记录）
- transcript 是 Runtime 的唯一 Source of Truth，所有状态都从它推导

### 3.2.10 系统 Prompt 体系

**需求来源**：编程 Agent 的行为很大程度上由 system prompt 决定。一个稳定可控的 Agent 需要把"应该做什么"、"不应该做什么"、"工具怎么用"、"如何写代码"等用 prompt 工程化。

**功能描述**：

**11 段动态拼装**（Team Lead 的完整版 system prompt）：

1. Intro（你是谁）
2. System（系统介绍）
3. Doing tasks（任务执行的 12 条行为规则）
4. Actions（关键动作约束）
5. Using tools（工具使用规则）
6. Tone and style（语气和风格）
7. Output efficiency（输出效率）
8. Environment（环境信息：cwd / OS / shell / git status）
9. SUMMARIZE_TOOL_RESULTS（事前压缩防御）
10. Memory（记忆系统行为指导）
11. CLAUDE.md（项目级 prompt）

**特殊变体**：
- DEFAULT_AGENT_PROMPT：Teammate 用的精简版
- COORDINATOR_SYSTEM_PROMPT：Coordinator 用的编排指导
- COMPACT_SYSTEM_PROMPT：压缩摘要时用的 prompt

**关键约束**：
- 段落拼装顺序必须稳定（影响 prompt cache）
- CLAUDE.md 支持 `@include` 递归展开
- 不同入口/agent 的 prompt 装配可重用，差异通过段落组合控制

## 3.3 非功能性需求

### 3.3.1 启动性能

**需求来源**：用户每天打开 CLI 几十次。如果每次启动需要 2 秒，一天累计就是几分钟的等待。这种"持续小延迟"对工程师生产力的杀伤极强。

**性能目标**：
- `claude --version` < 50ms（fast path，零额外 import）
- `claude` 进入 REPL < 1 秒（normal path）
- `claude -p` headless 单次执行 < 1 秒（不含模型时间）

**实现策略**（详见第 7.5 节）：
- 两级入口（cli.tsx 处理 fast path，main.tsx 处理 full runtime）
- 并行 IO 预热（MDM raw read、Keychain prefetch 在 import 期间触发）
- Dynamic import 边界
- `--bare` 模式跳过非必要初始化
- Deferred prefetch 推迟到 first render 后

**性能数据**（源码注释提及）：
- MDM raw read：节省约 135ms
- Keychain prefetch：节省约 65ms
- `setup()` 与 `getCommands()` 并行：节省约 28ms

### 3.3.2 执行延迟

**需求来源**：模型流式输出已经把"看到回答"的延迟降下来，但工具执行带来的等待感（特别是 Bash 命令、网页抓取）会再次拉长端到端时间。

**性能目标**：
- 工具开始执行的延迟应当尽可能小（理想：模型一吐完 tool_use 块就开始执行）

**实现策略**：
- StreamingToolExecutor：流式期间立即启动工具
- 并发安全工具并行执行（semaphore max 10）
- 非并发安全工具串行

**性能数据**：延迟降低 30-50%

### 3.3.3 安全性

**需求来源**：本地 Agent 直接访问用户的真实工作目录与 shell。一旦出错，代价是真实的——可能误删文件、泄露 secrets、被恶意 prompt 利用、被 supply chain 攻击。

**性能目标**：
- 防止 Windows PATH hijacking
- 防止恶意 CLAUDE.md / settings / hooks 在 Trust Dialog 前生效
- 默认 Fail-Closed（未知工具 / 未声明并发安全 → 默认不安全）
- API key / OAuth token 通过系统 Keychain 存储

**实现策略**（详见第 6.4 - 6.6 节）：
- Trust Boundary 把初始化拆分为 init.ts（trust 前）和 setup.ts（trust 后）
- 安全 env var 白名单
- Tool 默认值 fail-closed
- Windows 防 PATH 劫持（`NoDefaultCurrentDirectoryInExePath=1`）

### 3.3.4 可观测性

**需求来源**：分布式系统的运维经验告诉我们：没有可观测性的系统是不可调试的。Agent 作为复杂分布式系统的本地化版本，同样需要充分的可观测性支持。

**性能目标**：
- 启动性能可分阶段测量
- 工具调用有 OTel span
- 权限拒绝有审计记录
- Token 用量跨 turn 累积

**实现策略**：
- `startupProfiler` 在关键节点打 checkpoint，可生成详细报告
- `OpenTelemetry` span 包裹关键操作（`user_prompt` interaction span 等）
- `QueryEngine.permissionDenials` 累积所有被拒绝的请求
- `QueryEngine.totalUsage` 累积 token 使用

**环境变量控制**：
- `CLAUDE_CODE_PROFILE_STARTUP=1`：详细 startup profiling
- Statsig sampling 决定是否采样

### 3.3.5 可扩展性

**需求来源**：核心运行时无法预知所有用户需求。必须有清晰的扩展点，让新需求可以以"插件"形式接入而不影响核心。

**性能目标**：
- 新增 MCP server 不需要改 query_loop
- 新增 Hook 不需要改 tool 系统
- 新增 Skill 不需要改 prompt 系统
- 新增 Agent 定义不需要改 swarm 系统

**实现策略**：
- 扩展点都在分层架构的"扩展层"（最外层），核心运行时不感知扩展
- 扩展通过协议接入（MCP / Hook / Skill / Agent Definition）
- 扩展加载有热更新机制（hooks watcher、skills auto-discovery、settings change detector）

### 3.3.6 跨平台兼容

**需求来源**：开发者使用的操作系统多样，macOS / Linux / Windows 都必须支持。

**性能目标**：
- 三大平台行为基本一致
- 平台特化逻辑（如 Keychain、Registry、PowerShell）封装清晰

**实现策略**：
- 平台特化代码分离（`keychainPrefetch.ts` darwin 专属，`mdm/rawRead.ts` 内部按平台分支）
- 工具按平台启用（`PowerShellTool` 只在 Windows / 显式请求时启用）
- 跨平台路径处理统一通过 Node.js `path` 模块

### 3.3.7 企业合规

**需求来源**：企业部署有严格的 IT 合规要求，包括统一配置、审计日志、网络代理、CA 证书。

**性能目标**：
- MDM 管理 settings
- 可配置 HTTPS 代理
- 可注入企业 CA 证书
- Telemetry 可关闭

**实现策略**：
- 多级 settings 优先级（MDM > global > project > local）
- `initializeCertificates()` 在 trust 前就加载证书
- `initializeHttpAgent()` 支持代理配置
- Telemetry 通过 settings.json 关闭

### 3.3.8 资源效率

**需求来源**：Agent 不应该把用户机器的资源（内存 / CPU / 网络 / token 成本）浪费在不必要的事情上。

**性能目标**：
- 内存占用稳定（不应该随会话长度无限增长）
- Token 用量可控（auto-compact 防止超限）
- 子进程数量可控（Hooks 不能 fork bomb）
- 文件 watcher 数量可控

**实现策略**：
- Auto-compact 主动压缩历史
- StreamingToolExecutor 限制并发上限（max 10）
- 子进程通过 AbortController 优雅终止
- 文件 watcher 按需启停

## 3.4 关键约束

### 3.4.1 协议约束（Anthropic API）

**约束**：
- messages 数组中只有 `user` / `assistant` 两种 role
- tool_use 与 tool_result 必须严格配对（同一 tool_use_id）
- 不能有连续两个 user message（API 会拒绝）
- 流式响应不能保证 stop_reason 可靠

**影响**：
- 必须有 `normalizeMessagesForAPI()` 投影层
- 错误处理必须保证 synthetic tool_result 补全
- query_loop 必须以 tool_use block 出现为准，不能信 stop_reason

### 3.4.2 上下文窗口约束

**约束**：
- 模型最大上下文 200K（Sonnet）/ 1M（Opus 4.7）token
- 单次输出最大 8K（Sonnet 默认）/ 64K（Opus extended）

**影响**：
- 必须有 auto-compact 防溢出
- 必须有 max_output_tokens 恢复机制
- Tool result budget 必须限制单工具输出占用

### 3.4.3 终端环境约束

**约束**：
- 终端 stdout / stderr 是字节流，没有结构化协议
- 终端尺寸动态变化
- 输入是 raw mode（每个键都触发事件）
- 部分终端（如 tmux / iTerm2）支持额外 control sequence

**影响**：
- TUI 必须自定义 reconciler 处理 diff
- 必须监听 SIGWINCH 处理 resize
- input handling 必须支持 multiline / paste / IME

### 3.4.4 文件系统与跨平台约束

**约束**：
- 不同平台路径分隔符不同（`/` vs `\`）
- 不同平台行结尾不同（`\n` vs `\r\n`）
- 文件权限模型不同（Unix mode vs Windows ACL）
- 部分目录默认不可写（如 macOS sealed system volume）

**影响**：
- 必须用 Node.js `path` 模块跨平台
- 写文件时必须显式处理 line ending
- 必须有跨平台一致的权限设置策略（如 `mode: 0o700` 在 Windows 上 best-effort）

### 3.4.5 模型行为约束

**约束**：
- 模型可能输出 stop_reason 与 content 不一致
- 模型可能盲目重试已失败的工具
- 模型可能过度工程（写过多防御性代码）
- 模型可能猜测未读文件内容

**影响**：
- query_loop 不信 stop_reason
- system prompt 中的 `doing_tasks` 12 条规则对抗这些倾向
- 复杂逻辑必须用 stop hooks / agent definition 强约束

### 3.4.6 安全约束

**约束**：
- 用户工作目录可能包含恶意 CLAUDE.md / hooks
- 环境变量可能包含 secrets（不能直接 telemetry）
- API key 不能写入 plain text 文件
- 部分操作（如删除文件）一旦执行无法回滚

**影响**：
- Trust Dialog 必须在敏感初始化前弹出
- env var 白名单分阶段应用
- API key 通过 Keychain / OAuth
- 高风险工具必须经过权限确认

## 3.5 需求优先级

按工程实现的优先级排序（高 > 中 > 低）：

| 需求 | 优先级 | 理由 |
|------|--------|------|
| 工具调用与权限 | 高 | 没有就不是 Agent |
| 协议正确性 | 高 | 错了会让 API 拒绝，整个会话崩溃 |
| 上下文压缩 | 高 | 不做长会话直接挂 |
| Transcript 持久化 | 高 | 不做异常退出丢失工作 |
| 错误恢复 | 高 | 真实 API / 网络不可能完美 |
| StreamingToolExecutor | 中 | 性能优化，关闭仍可用 |
| Multi-Agent | 中 | 复杂任务专属，简单场景不需要 |
| MCP / Hooks / Skills | 中 | 扩展性，第一版可不带 |
| Memory 系统 | 中 | 跨会话价值，单会话不需要 |
| 性能 profiling | 低 | 调试用，不影响功能 |

Claude ​Code 把全部这些需求都纳入了正式产品范围，这反映了 Anthropic 对编程 Agent 的定位：**不是 demo，而是要被工程师每天使用的生产力工具**。

## 3.6 需求与机制映射

下表把功能性需求映射到第 5 章会详细解析的具体机制：

| 需求 | 主要机制 | 对应章节 |
|------|---------|---------|
| 多入口 | cli.tsx 分流 + 共用 query() | §5.1, §7.3 |
| 30+ 工具 | Tool 抽象 + 三层装配 | §5.4 |
| 流式工具执行 | StreamingToolExecutor | §5.3 |
| 权限管理 | PermissionMode + 规则引擎 + Tool.checkPermissions | §5.5 |
| 上下文压缩 | Compact 四策略 + 双层防御 | §5.6 |
| 记忆系统 | 四层 Memory + Coalescing | §5.7 |
| 多 Agent | Agent Teams + Mailbox + contextvars | §5.8 |
| MCP | services/mcp/client.ts | §5.9 |
| Hooks | hooks watcher + PreToolUse/PostToolUse | §5.10 |
| Skills | loadSkillsDir + slash command 注册 | §5.11 |
| 会话持久化 | sessionStorage + transcript validation | §5.12 |
| 系统 Prompt | 11 段拼装 + 4 段控制 prompt | §4.5 |
| 启动性能 | cli.tsx fast path + 并行 IO 预热 | §7.5 |
| 协议正确性 | normalizeMessagesForAPI + 不信 stop_reason | §6.2 |
| Fail-Closed | Tool 默认值 + 未知工具 ASK | §6.4 |

---

## 本章小结

本章从用户角色与场景出发，系统化梳理了 Claude ​Code 必须解决的功能性需求与非功能性需求，并明确了关键约束。

主要发现：

- Claude ​Code 服务于至少 5 类用户（个人开发者、团队工程师、高级用户、企业 IT、研究者），多入口设计是这些场景的需求合力
- 30+ 内置工具、流式执行、三级权限、四种压缩、四层记忆、多 Agent、扩展机制、会话持久化构成了功能骨架
- 启动性能、执行延迟、安全性、可观测性、可扩展性、跨平台、企业合规、资源效率构成了非功能骨架
- Anthropic API 协议约束、上下文窗口约束、模型行为约束是 Runtime 必须遵守的硬约束
- 需求优先级反映了 Anthropic 把 Claude ​Code 定位为生产力工具而非 demo 的选择

接下来的第 4 章将基于这些需求与约束，给出 Claude ​Code 的整体架构与详细设计。
