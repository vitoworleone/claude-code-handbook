# 参考文献与附录

---

## 参考文献

### 一、官方文档与产品资料

[1] Anthropic. *Claude Documentation: Messages API*. https://docs.anthropic.com/claude/reference/messages

[2] Anthropic. *Claude Documentation: Tool Use*. https://docs.anthropic.com/claude/docs/tool-use

[3] Anthropic. *Claude Documentation: Prompt Caching*. https://docs.anthropic.com/claude/docs/prompt-caching

[4] Anthropic. *Claude Documentation: Extended Thinking*. https://docs.anthropic.com/claude/docs/extended-thinking

[5] Anthropic. *Claude ​Code Official Documentation*. https://docs.claude.com/claude-code

[6] Anthropic. *Claude ​Code GitHub Issues Tracker*. https://github.com/anthropics/claude-code/issues

[7] Anthropic. *Model Context Protocol Specification*. https://modelcontextprotocol.io/specification

[8] Anthropic. *Introducing the Model Context Protocol*. 2024-11. https://www.anthropic.com/news/model-context-protocol

[9] Anthropic. *Claude Models Overview (Opus 4.6 / Opus 4.7 / Sonnet 4.6 / Haiku 4.5)*. https://docs.anthropic.com/claude/docs/models-overview

[10] Anthropic. *Claude API Pricing*. https://www.anthropic.com/pricing

### 二、Source Map 泄露事件相关报道

[11] Shou, Chaofan. *2026-03-31 disclosure of Claude ​Code source map files in npm package*. 技术披露与初始事件报道。

[12] 各开源社区对源码内容的二次分析与镜像（2026 年 4 月起）。

### 三、Agent 与 LLM 范式学术研究

[13] Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., Cao, Y. (2022). *ReAct: Synergizing Reasoning and Acting in Language Models*. arXiv:2210.03629

[14] Schick, T., Dwivedi-Yu, J., Dessì, R., Raileanu, R., Lomeli, M., Zettlemoyer, L., Cancedda, N., Scialom, T. (2023). *Toolformer: Language Models Can Teach Themselves to Use Tools*. arXiv:2302.04761

[15] Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L., Anandkumar, A. (2023). *Voyager: An Open-Ended Embodied Agent with Large Language Models*. arXiv:2305.16291

[16] Shinn, N., Cassano, F., Berman, E., Gopinath, A., Narasimhan, K., Yao, S. (2023). *Reflexion: Language Agents with Verbal Reinforcement Learning*. arXiv:2303.11366

[17] Wu, Q., Bansal, G., Zhang, J., Wu, Y., Li, B., Zhu, E., Jiang, L., Zhang, X., Zhang, S., Liu, J., Awadallah, A. H., White, R. W., Burger, D., Wang, C. (2023). *AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*. arXiv:2308.08155

[18] Yang, J., Jimenez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K., Press, O. (2024). *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*. arXiv:2405.15793

[19] Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., Bernstein, M. S. (2023). *Generative Agents: Interactive Simulacra of Human Behavior*. arXiv:2304.03442

[20] Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., Gonzalez, J. E. (2023). *MemGPT: Towards LLMs as Operating Systems*. arXiv:2310.08560

[21] Wang, Z. Z., Mao, J., Fried, D., Neubig, G. (2024). *Agent Workflow Memory*. arXiv:2409.07429

[22] Jimenez, C. E., Yang, J., Wettig, A., Yao, S., Pei, K., Press, O., Narasimhan, K. (2024). *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* ICLR 2024. arXiv:2310.06770

### 四、行业资料与同类项目

[23] *OpenAI Function Calling Documentation*. https://platform.openai.com/docs/guides/function-calling

[24] *OpenAI Codex CLI*. https://github.com/openai/codex

[25] *Aider — AI Pair Programming in Your Terminal*. https://aider.chat/

[26] *Cline — Autonomous coding agent for VS Code*. https://github.com/cline/cline

[27] *Cognition AI: Introducing Devin, the first AI software engineer*. 2024-03. https://www.cognition-labs.com/introducing-devin

[28] *Continue: The open-source autopilot for software development*. https://continue.dev/

[29] *Cursor: The AI Code Editor*. https://cursor.sh/

### 五、本项目内部研究文档

> 以下为本项目（ClaudeCode-Runtime）在源码研究过程中产出的内部材料，按主题分类列出。所有文档位于 `00_Project-Paper/`、`03_References/` 与 `08_Skill-Outputs/`。

#### 5.1 章节级深度草稿（00_Project-Paper/02-*）

[I-1] 02-Chapter2-Knowledge-Graph.md — 第二章知识图谱速查
[I-2] 02-Report-Chapter2-Section1.md — 技术栈选择与设计原因
[I-3] 02-Report-Chapter2-Section2.md — 六层运行时骨架架构分层
[I-4] 02-Report-Chapter2-Section3-Entrypoints.md — 九种程序入口与多入口设计
[I-5] 02-Report-Chapter2-Section4-Startup-Flow.md — 启动六阶段与四原则
[I-6] 02-Report-Chapter2-Section5-QueryLoop.md — query_loop 状态机
[I-7] 02-Report-Chapter2-Section6-TUI.md — TUI 与终端渲染
[I-8] 02-Report-Chapter2-Section7-DesignDecisions.md — 关键设计决策
[I-9] 02-Report-Chapter2-Section8-CorePositioning.md — 核心定位判断
[I-10] 02-Report-Chapter2-Section9-ExecutionChain.md — 主执行链路完整流转
[I-11] 03-Research-Report-ContextCompiler-DeepDive.md — Context Compiler 深度研究

#### 5.2 系统分析与执行链路调研（08_Skill-Outputs/）

[I-12] 2026-05-16_01_System-Analysis_源码锚定深度中文研究报告.md
[I-13] 2026-05-16_03_References_完整中文研究报告.md
[I-14] 2026-05-16_code-exploration_架构全景与核心机制.md
[I-15] 2026-05-16_control-plane_agent-runtime-core_架构调研.md
[I-16] 2026-05-16_improve-architecture_section-4_key-subsystems.md
[I-17] 2026-05-16_investigate_QueryEngine_query_loop.md
[I-18] 2026-05-16_project-paper-coach_ClaudeCode-Runtime_initial-map.md
[I-19] Agent_Runtime_架构与原理综合报告.md
[I-20] Harness架构设计调研报告.md
[I-21] 上下文注入与连续性保持机制调研报告.md
[I-22] 缓存与上下文窗口优化机制调研报告.md
[I-23] 学习疑问_待深入话题调研报告.md
[I-24] 学习疑问与理解总结_二次调研报告.md
[I-25] 2026-05-16_context-continuity-compact-pdf-discussion.md

#### 5.3 第三方源码地图与参考实现（03_References/）

[I-26] claude-code-sourcemap-main/ — 反向工程的 TS 源码快照（1902 文件）
[I-27] learn-claude-code-docs/ — 官方文档归档
[I-28] learn-claude-code-skills/ — 内置 skills 归档
[I-29] learn-claude-code-agents/ — 内置 agent 定义归档
[I-30] oh-my-openagent/ — 同类开源 harness 项目参考
[I-31] agent-harness-project-reference.md — Harness 综合参考

### 六、Python 复刻实现

[I-32] 02_Source-Code/01_CC-Python-Runtime/Source/ — Python 复刻的 cc 包源码
[I-33] cc/core/query_loop.py — query_loop 复刻
[I-34] cc/core/query_engine.py — QueryEngine 复刻
[I-35] cc/tools/streaming_executor.py — StreamingToolExecutor 复刻
[I-36] tests/unit/ — 498 个单元测试

---

## 附录 A：术语表

| 术语 | 缩略 / 全称 | 解释 |
|------|-----------|------|
| Agent Loop | — | LLM 与工具交互的循环范式 |
| AsyncGenerator | — | JavaScript 中可异步迭代的生成器对象 |
| Auto-compact | — | 自动触发的上下文压缩 |
| Backend | — | Agent Teams 的执行后端（in-process / tmux / iTerm2） |
| Bridge | — | Claude ​Code 连接远程 orchestrator 的模式 |
| Cache breakpoint | — | Prompt Caching 中的缓存标记位置 |
| Coalescing | — | 提取策略：保证最终状态被处理 |
| Compact | — | 上下文压缩 |
| Compact boundary | — | 压缩前后历史的分界点 |
| Context Compiler | — | 把内部 transcript 编译为 API messages 的过程 |
| Contextvars | Context Variables | 协程级身份隔离机制 |
| Coordinator | — | Agent Teams 中的编排者角色 |
| Dead Code Elimination | DCE | 编译期死码消除 |
| Debounce | — | 事件流处理策略：只处理最后一次 |
| Fail-Closed | — | 默认拒绝的安全取向 |
| Fail-Open | — | 默认允许的安全取向 |
| feature() / ifFeature() | — | 编译期特性门控宏 |
| Function Calling | — | OpenAI 的工具调用协议 |
| Headless | — | 无 UI 的执行模式（即 `-p` / `--print`） |
| Ink | — | 把 React 渲染目标改为终端文本的库 |
| JSONL | JSON Lines | 每行一个 JSON 对象的文本格式 |
| Keychain | — | macOS 的密码存储服务 |
| MCP | Model Context Protocol | Anthropic 的外部能力接入协议 |
| MDM | Mobile Device Management | 企业设备管理（本文泛指企业策略管理） |
| Mailbox | — | Agent Teams 的文件系统邮箱 |
| messages | — | API 中的对话消息数组 |
| normalizeMessagesForAPI | — | 把内部 transcript 投影为 API messages 的函数 |
| PermissionMode | — | 权限模式（BYPASS / ACCEPT_EDITS / DEFAULT） |
| PreToolUse / PostToolUse | — | 工具执行前后的 hook 类型 |
| Prompt Cache | — | Anthropic API 的 prompt 缓存特性 |
| Pure Function | — | 不依赖外部状态的函数 |
| QueryEngine | — | 会话生命周期容器 |
| query_loop / query() | — | 单 turn 执行内核 |
| ReAct | — | Reasoning + Acting 范式 |
| REPL | Read-Eval-Print Loop | 交互式命令行 |
| Reactive compact | — | API 返回 prompt_too_long 后触发的应急压缩 |
| Skill | — | frontmatter-Markdown 格式的可触发 prompt unit |
| Source of Truth | SoT | 唯一权威状态来源 |
| Source map | — | 打包后代码与原始源码的映射 |
| Statsig | — | Feature Flag 与 A/B testing 服务 |
| Stop hook | — | Agent 准备结束时的 hook |
| StreamingToolExecutor | — | 流式期间启动工具执行的模块 |
| Subagent | — | 子 agent（通过 AgentTool 派生） |
| Swarm | — | Agent Teams 的内部代号 |
| System Prompt | — | 系统提示词（指导模型行为） |
| Teammate | — | Agent Teams 中的协作者角色 |
| Telemetry | — | 遥测数据 |
| tool_use / tool_result | — | Anthropic Tool Use 协议的两类 content block |
| Tool Use Protocol | — | Anthropic 的工具调用协议 |
| Transcript | — | 完整的会话消息历史 |
| transition.reason | — | 状态机迁移边的命名标签 |
| Trust Boundary | — | 信任建立前后的初始化分界 |
| TUI | Terminal UI | 终端用户界面 |
| useSyncExternalStore | — | React 18+ 订阅外部 store 的 hook |
| Worktree | — | git 工作树（不同分支的并存目录） |
| Zod | — | TypeScript-first 的 schema 校验库 |

---

## 附录 B：核心源码文件索引

按层级整理 Claude ​Code 源码中本论文反复引用的关键文件：

### 入口层

| 文件 | 主要内容 |
|------|---------|
| `src/entrypoints/cli.tsx` | 轻量入口，Fast Path 分流 |
| `src/main.tsx` | 总控编排（4683 行） |
| `src/entrypoints/init.ts` | trust 前初始化 |
| `src/entrypoints/mcp.ts` | MCP Server 入口 |
| `src/setup.ts` | trust 后环境初始化 |

### 控制面 / TUI

| 文件 | 主要内容 |
|------|---------|
| `src/replLauncher.tsx` | REPL 启动边界 |
| `src/screens/REPL.tsx` | 交互 UI 中枢（4500+ 行） |
| `src/state/store.ts` | 外部状态总线 |
| `src/state/AppStateStore.ts` | AppState 实现 |
| `src/ink.ts` | UI 入口 facade |
| `src/ink/ink.tsx` | 自定义 Ink renderer |
| `src/components/PromptInput/PromptInput.tsx` | 输入编排器 |
| `src/utils/processUserInput/processUserInput.ts` | 用户输入处理 |
| `src/utils/processUserInput/processTextPrompt.ts` | 普通文本路径 |
| `src/utils/processUserInput/processSlashCommand.tsx` | Slash command 路径 |

### 执行内核

| 文件 | 主要内容 |
|------|---------|
| `src/QueryEngine.ts` | 会话生命周期容器 |
| `src/query.ts` | while(true) AsyncGenerator 状态机 |
| `src/query/deps.ts` | 依赖注入对象 |
| `src/query/stopHooks.ts` | Stop hooks 处理 |
| `src/services/api/claude.ts` | 模型 API 适配 |
| `src/utils/messages.ts` | normalizeMessagesForAPI 实现 |

### 工具系统

| 文件 | 主要内容 |
|------|---------|
| `src/Tool.ts` | Tool 类型定义（9 个职责组） |
| `src/tools.ts` | 工具池装配 |
| `src/services/tools/toolOrchestration.ts` | partitionToolCalls + 并发调度 |
| `src/services/tools/toolExecution.ts` | runToolUse 四层栈 |
| `src/services/tools/StreamingToolExecutor.ts` | 流式工具执行 |

### 权限系统

| 文件 | 主要内容 |
|------|---------|
| `src/permissions/` | 权限引擎实现 |
| `src/permissions/PermissionMode.ts` | 三级模式 |
| `src/permissions/rules/` | 规则引擎 |

### 上下文压缩

| 文件 | 主要内容 |
|------|---------|
| `src/services/compact/compact.ts` | 手动 compact |
| `src/services/compact/autoCompact.ts` | 自动 compact |
| `src/services/compact/sessionMemoryCompact.ts` | Session Memory 压缩 |
| `src/services/compact/reactiveCompact.ts` | Reactive compact |
| `src/services/compact/buildPostCompactMessages.ts` | 压缩后状态复灌 |

### 记忆系统

| 文件 | 主要内容 |
|------|---------|
| `src/memdir/memdir.ts` | Memory 文件系统入口 |
| `src/services/memory/sessionMemory.ts` | Session Memory |
| `src/services/memory/autoMemory.ts` | Auto Memory |
| `src/services/memory/extractMemories.ts` | Coalescing 提取 |

### Session 持久化

| 文件 | 主要内容 |
|------|---------|
| `src/session/sessionStorage.ts` | JSONL 持久化 |
| `src/session/validateTranscript.ts` | Transcript validation |
| `src/session/taskRegistry.ts` | TaskRegistry |

### MCP

| 文件 | 主要内容 |
|------|---------|
| `src/services/mcp/client.ts` | MCP 连接 |
| `src/services/mcp/buildMcpToolName.ts` | mcp__server__tool 命名 |
| `src/services/mcp/transports/` | 多种传输实现 |

### Agent Teams / Swarm

| 文件 | 主要内容 |
|------|---------|
| `src/tools/AgentTool/AgentTool.ts` | AgentTool 实现 |
| `src/tools/AgentTool/runAgent.ts` | subagent runtime |
| `src/utils/swarm/teammate.ts` | Teammate 实现 |
| `src/utils/swarm/backends/registry.ts` | Backend 注册表 |
| `src/utils/swarm/mailbox/` | 文件系统邮箱 |
| `src/utils/swarm/teamFile.ts` | 团队成员注册 |
| `src/prompts/coordinator_prompt.ts` | Coordinator system prompt |
| `src/prompts/teammate_prompt.ts` | Teammate system prompt |

### Hooks

| 文件 | 主要内容 |
|------|---------|
| `src/hooks/hookRunner.ts` | Hook 执行 |
| `src/utils/hooks/hooksWatcher.ts` | Hook 配置热更新 |

### Skills

| 文件 | 主要内容 |
|------|---------|
| `src/skills/loadSkillsDir.ts` | Skill 文件系统发现 |
| `src/skills/createSkillCommand.ts` | Skill 注册为 slash command |
| `src/skills/bundled/index.ts` | 内置 skills |

### 启动优化

| 文件 | 主要内容 |
|------|---------|
| `src/utils/startupProfiler.ts` | 启动性能观测 |
| `src/utils/settings/mdm/rawRead.ts` | MDM 并行读取 |
| `src/utils/secureStorage/keychainPrefetch.ts` | Keychain 并行预取 |
| `src/utils/earlyInput.ts` | Early input capture |
| `src/interactiveHelpers.tsx` | First render 后启动 deferred prefetch |

### Settings

| 文件 | 主要内容 |
|------|---------|
| `src/utils/settings/` | 多级 settings 实现 |
| `src/utils/settings/changeDetector.ts` | 配置热更新 |
| `src/utils/settings/mdm/settings.ts` | MDM 优先级 |
| `src/utils/CLAUDE.md/load.ts` | CLAUDE.md 加载 + @include |

### Prompts

| 文件 | 主要内容 |
|------|---------|
| `src/prompts/sections.ts` | system prompt 段落 |
| `src/prompts/builder.ts` | 11 段动态拼装 |
| `src/prompts/coordinator_prompt.ts` | COORDINATOR_SYSTEM_PROMPT |
| `src/prompts/teammate_prompt.ts` | DEFAULT_AGENT_PROMPT + TEAMMATE_ADDENDUM |
| `src/services/compact/COMPACT_SYSTEM_PROMPT.ts` | 压缩 prompt |

---

## 附录 C：七种 transition.reason 速查

| transition.reason | 触发条件 | 状态变化 | 源码位置 |
|------------------|---------|---------|---------|
| `next_turn` | 本轮有 tool_use，工具结果已收集完 | `messages = messagesForQuery + assistantMessages + toolResults`，`turnCount + 1` | `query.ts:1714-1727` |
| `collapse_drain_retry` | prompt-too-long withheld，context collapse 可 drain | `messages = drained.messages`，不增加 turnCount | `query.ts:1085-1116` |
| `reactive_compact_retry` | prompt-too-long / media-size error 可通过 reactive compact 恢复 | `messages = postCompactMessages`，`hasAttemptedReactiveCompact = true` | `query.ts:1119-1165` |
| `max_output_tokens_escalate` | 输出截断且可提升 max output tokens 上限 | `maxOutputTokensOverride = ESCALATED_MAX_TOKENS` | `query.ts:1185-1220` |
| `max_output_tokens_recovery` | 输出截断且仍在恢复次数限制内 | 追加 continuation meta user message | `query.ts:1223-1251` |
| `stop_hook_blocking` | Stop hook 返回 blocking errors | 把 blocking error 作为 meta user message 注入 | `query.ts:1282-1306` |
| `token_budget_continuation` | TOKEN_BUDGET feature 要求继续产出 | 追加 budget nudge meta message | `query.ts:1308-1340` |

---

## 附录 D：六个 transcript 写入点速查

| # | 写入点 | 位置 | 写入内容 | 设计原因 |
|---|--------|------|---------|---------|
| 1 | 用户输入追加 | `REPL.tsx → handlePromptSubmit()` / `QueryEngine.submitMessage()` | UserMessage、attachments、slash command 输出 | 输入一旦接受就进入会话，支持 resume 与审计 |
| 2 | compact 替换 | `queryLoop() → deps.autocompact() → buildPostCompactMessages()` | compact summary、boundary、复灌附件 | 压缩旧历史，同时保留工作状态 |
| 3 | max_output_tokens 恢复注入 | `queryLoop()` 截断恢复分支 | meta UserMessage："Please continue" | 输出被截断时保持协议内 continuation |
| 4 | API 返回 assistant | streaming loop 累积后构建 | AssistantMessage，含 text/thinking/tool_use | 将流式输出物化为 transcript 事实 |
| 5 | 工具结果回流 | `runTools()` / `StreamingToolExecutor` / `toolExecution.ts` | content 为 tool_result block 的 UserMessage | Anthropic 协议要求 tool_result 作为 user message 回到下一轮 |
| 6 | 系统注入与 hook 输出 | `createSystemMessage()` / hook 系统 / `normalizeMessagesForAPI()` | system / local_command / notification / hook additional context | 内部系统事件可记录，但 API 只保留模型需要看到的部分 |

---

## 附录 E：30+ 内置工具清单

| 类别 | 工具 | 主要功能 |
|------|------|---------|
| Agent / 任务 | `AgentTool` | 创建子 agent |
| | `TaskOutputTool` | 任务输出 |
| | `TaskStopTool` | 停止任务 |
| | `TeamCreateTool` | 创建团队 |
| | `TeamDeleteTool` | 删除团队 |
| | `SendMessageTool` | 给 teammate 发消息 |
| Shell / 文件 | `BashTool` | 执行 shell 命令 |
| | `FileReadTool` | 读取文件 |
| | `FileEditTool` | 原地编辑文件 |
| | `FileWriteTool` | 写入新文件 |
| | `PowerShellTool` | PowerShell（Windows） |
| | `NotebookEditTool` | 编辑 Jupyter notebook |
| 搜索 | `GlobTool` | 文件路径 glob 匹配 |
| | `GrepTool` | 文本搜索（ripgrep） |
| 计划 | `EnterPlanModeTool` | 进入计划模式 |
| | `ExitPlanModeV2Tool` | 退出计划模式 |
| Web | `WebFetchTool` | 抓取网页 |
| | `WebSearchTool` | 网页搜索 |
| Todo | `TodoWriteTool` | 待办事项管理 |
| Skill / 用户交互 | `SkillTool` | 调用 skill |
| | `AskUserQuestionTool` | 向用户提问 |
| MCP 资源 | `ListMcpResourcesTool` | 列出 MCP 资源 |
| | `ReadMcpResourceTool` | 读取 MCP 资源 |
| 其它 | `ToolSearch` | 工具搜索 |
| | `SyntheticOutputTool` | 合成输出 |

---

## 附录 F：启动六阶段时间线速查

```text
Phase 0: process starts
  entrypoints/cli.tsx top-level env side effects

Phase 1: CLI bootstrap fast path dispatch
  parse argv
  --version zero import
  其他 fast paths 只 import target module
  normal path starts early input capture -> dynamic import main.js

Phase 2: main.tsx top-level prefetch + module evaluation
  profileCheckpoint(main_tsx_entry)
  startMdmRawRead()             ← 并行 IO #1
  startKeychainPrefetch()       ← 并行 IO #2
  load full runtime imports
  feature-gated requires
  profileCheckpoint(main_tsx_imports_loaded)

Phase 3: Commander setup + preAction
  create Commander program
  build options
  preAction awaits MDM/keychain promises  ← join 并行 IO
  init() ← trust 前
  sinks / migrations / remote managed settings
  [Trust Dialog]
  initializeTelemetryAfterTrust() ← trust 后

Phase 4: Default action preparation
  parse input prompt
  toolPermissionContext
  getTools()
  import setup.js
  setup() in parallel with getCommands() / getAgentDefinitions()
  resolve model / settings / hooks / MCP / files

Phase 5A: interactive branch
  getRenderContext
  import ./ink.js
  createRoot
  showSetupScreens
  launchRepl
  renderAndRun -> first render -> deferred prefetches

Phase 5B: headless / print branch
  build headless store
  connect MCP with bounded waits (5s)
  start selected prefetches
  dynamic import src/cli/print.js
  runHeadless
```

**关键性能数据**（源码注释提及）：

- MDM raw read 节省 ~135ms
- Keychain prefetch 节省 ~65ms
- setup() 与 getCommands/getAgentDefinitions 并行节省 ~28ms

---

## 附录 G：八条核心不变量速查

在所有时刻，Claude ​Code Runtime 必须维护以下不变量：

1. **Transcript 完整性**：每个 tool_use 必须有对应 tool_result（即使是 synthetic error）
2. **API 协议合规**：发给 API 的 messages 不能有连续 user message、孤儿 tool_use / tool_result
3. **权限决策在副作用之前**：`tool.call()` 之前必须完成 permission check
4. **transcript 持久化优先于模型调用**：用户输入必须先写 transcript，再调 API
5. **Trust 前不应用敏感配置**：env var 白名单 + telemetry skeleton + 不读 hooks
6. **流式执行不破坏顺序**：物理提前不能改变最终 transcript 中 tool_use ↔ tool_result 的相对顺序
7. **未知工具默认 ASK**：白名单遗漏不能等于自动允许
8. **Prompt cache 稳定**：影响 prompt 前缀位置的修改必须深思熟虑

---

## 附录 H：八个核心设计判断速查

1. 统一执行内核（多入口复用 `query.ts`）
2. 文件化分层 memory（Markdown + MEMORY.md 索引）
3. local-first 但无缝扩展（MCP / Bridge）
4. Tool 是能力对象而非函数映射（9 个职责组）
5. Context 是工作台状态而非聊天历史（Context Compiler）
6. Memory 是可审计的 Markdown 文件系统
7. 协议正确性优先于输出漂亮
8. 权限门控内嵌于执行流（Tool.checkPermissions）

---

## 附录 I：本论文统计

| 项 | 数值 |
|----|-----:|
| 章节数 | 9 章 + 摘要 + 参考文献 + 附录 |
| 总行数（估算） | 约 4,600+ 行 |
| 源码文件覆盖 | 1,902 个 TS/TSX 文件 |
| 源码行数覆盖 | 513,237 行 |
| 引用源码位置 | 100+ 处（含具体行号） |
| 引用内部研究文档 | 36 篇 |
| 引用学术论文 | 10 篇 |
| 引用官方文档 | 11 篇 |
| 关键设计决策 | 9 项 |
| 关键不变量 | 8 条 |
| 核心设计判断 | 8 项 |
| Python 复刻单元测试 | 498 个 |

---

## 致谢

感谢 Anthropic 工程团队在 Claude ​Code 源码中留下大量长篇代码注释。这些注释不仅解释了"是什么"，更解释了"为什么"。它们是本论文最重要的信息源。

感谢 Chaofan Shou 于 2026 年 3 月 31 日对 source map 泄露事件的负责任披露，让外界得以完整研究这套工业级 Agent Runtime。

感谢开源社区对 MCP 协议、ReAct 范式、Agent 框架等基础工作的贡献，让"理解 Claude ​Code"成为一项有上下文的研究而非孤立解读。

---

## 论文结束

本论文到此结束。九章内容、约 4,600 行文字、对 1,902 个文件 / 513,237 行代码的系统化分析，凝练为对 Agent Runtime 工程化方法的一份完整记录。

如读者发现论文中的事实错误、逻辑断裂或可改进之处，欢迎反馈以便后续修订。

—— 全文完
