# Agent Harness 项目参考：OpenCode / Codex / DeerFlow

更新时间：2026-05-19

这份文档用于学习和对照不同 Agent Harness 的设计。重点不是“哪个项目最好”，而是拆出可复用的工程部件：agent loop、工具系统、权限与沙箱、上下文管理、任务状态、subagent、插件边界、运行时恢复。

## 1. 什么是 Harness

在这里，harness 不是模型本身，也不只是 CLI/TUI。它是包在模型外面的运行时系统，负责把一个 LLM 变成可以持续执行工作的 agent。

核心职责：

- **Agent Loop**：把用户消息、模型输出、tool call、tool result 串成循环。
- **Tool Registry**：定义工具、参数 schema、执行器、权限、结果格式。
- **Policy / Sandbox**：限制文件、命令、网络、外部服务访问。
- **Context Manager**：控制上下文窗口、压缩、摘要、恢复关键状态。
- **Task / Memory State**：保存跨轮、跨会话、跨 agent 的任务与记忆。
- **Subagent / Background Work**：把大任务拆给子 agent 或后台任务。
- **Extension Surface**：插件、MCP、skills、custom tools、provider adapter。
- **Observability / Recovery**：日志、事件、追踪、失败恢复、断点续跑。

## 2. 项目定位对照

| 项目 | 定位 | 开源范围 | 最值得研究的 Harness 点 |
| --- | --- | --- | --- |
| OpenCode | 开源 AI coding agent，含 TUI、server、desktop、plugin SDK | `anomalyco/opencode` 公开，MIT | 插件边界、provider 抽象、session/tool pipeline、TUI/server 分层 |
| Codex CLI | OpenAI 本地 coding agent，偏 canonical local agent loop | `openai/codex` 公开，Apache-2.0；云端 Codex Web/服务端不完整开源 | 本地 agent loop、审批模式、diff/命令执行、sandbox、Responses API 接入 |
| DeerFlow 2.0 | ByteDance 的 long-horizon SuperAgent harness | `bytedance/deer-flow` 公开，MIT | subagent 编排、skills、memory、sandbox filesystem、Gateway/API、长任务运行时 |
| oh-my-openagent | 基于 OpenCode plugin API 的“外挂式”增强 harness | 本地已有源码：`./oh-my-openagent` | OpenCode 插件如何扩展成复杂 harness：hooks、tools、background manager、task system |

## 3. OpenCode

OpenCode 的核心价值是：它本身就是一个完整 coding agent 产品，同时提供插件面。对我们学习 harness 来说，它更像“宿主运行时”。

官方仓库中的关键目录：

- `packages/opencode`：核心业务逻辑与 server。
- `packages/opencode/src/cli/cmd/tui`：终端 UI。
- `packages/app`：共享 Web UI。
- `packages/desktop`：桌面端。
- `packages/plugin`：`@opencode-ai/plugin` 源码。

值得探索的问题：

- 一个 coding agent 的 session 如何保存、恢复、压缩。
- tool call 在 host 内部如何从模型输出进入真实执行器。
- provider/model 如何被抽象成可替换后端。
- plugin hook 在什么时机能拦截：chat params、message transform、tool before/after、event。
- 内置 `build`、`plan`、`general` agent 如何影响权限和行为边界。

与 oh-my-openagent 的关系：

- OpenCode 是 host；oh-my-openagent 是 plugin/harness layer。
- oh-my-openagent 不需要复制 OpenCode 的完整 loop，而是通过 OpenCode 插件 API 注入工具、hooks、任务系统、背景 agent、skills。
- 这也是它功能多但不必完全“打架”的原因：理论上都从 OpenCode 的标准扩展点进入；风险在于 hook 太多、工具名冲突、状态机复杂。

本地参考入口：

- `./oh-my-openagent/src/testing/create-plugin-module.ts`
- `./oh-my-openagent/src/plugin-interface.ts`
- `./oh-my-openagent/src/plugin/tool-registry.ts`
- `./oh-my-openagent/src/create-hooks.ts`
- `./oh-my-openagent/src/plugin-config.ts`

## 4. Codex CLI

Codex CLI 更适合学习“本地 coding agent 的主循环”。它不是插件层，而是直接实现一个本地 agent controller。

公开范围要区分清楚：

- 公开：Codex CLI 源码、安装、构建、贡献文档、主要本地运行时。
- 未完整公开：Codex Web、ChatGPT 后端、云端任务系统、模型服务、训练代码、计费与权限后端。

从 OpenAI 工程文章看，Codex CLI 的推理请求会进入 Responses API 或 ChatGPT Codex endpoint。本地 CLI 负责控制 loop、上下文、工具和执行，模型推理仍在服务端或本地兼容 endpoint。

最值得研究：

- 模型输出如何被解析成 tool call。
- shell/file/diff 工具如何接入审批流。
- “读、写、执行命令、联网”这些权限如何分级。
- sandbox 如何限制 full-auto 模式的破坏面。
- 长会话如何 compact，上下文如何保留任务状态。
- CLI/TUI 如何让用户看到每一步工具调用和 diff。

适合借鉴的设计：

- **审批优先**：把权限作为 harness 的一等公民，而不是工具执行器里的边角逻辑。
- **本地文件系统主权**：agent 读写发生在本机，用户能直接审查 diff。
- **小而硬的 agent loop**：先把 loop、tool result、错误恢复做可靠，再堆复杂功能。

## 5. DeerFlow 2.0

DeerFlow 明确把自己定位为 open-source super agent harness。它与 Codex/OpenCode 的差异在于：目标不是只做交互式 coding，而是支持分钟到小时级的长任务，包含研究、代码执行、文件产出、内容生成、subagent 协作。

官方 README 中的关键设计点：

- 基于 LangGraph / LangChain。
- 2.0 是重写版本，与 v1 不共享代码。
- 内置 skills、tools、sub-agents、memory、sandbox、filesystem。
- skills 渐进加载，只在任务需要时放进上下文。
- 支持 Docker / Kubernetes sandbox，也支持本地模式但默认不把 host bash 当安全边界。
- Gateway API 内运行 agent runtime，并支持 IM channel。
- 支持 OpenAI-compatible providers，也出现了 Codex CLI / Claude Code OAuth 这类 CLI-backed provider 配置。

最值得研究：

- lead agent 如何规划并 spawn sub-agents。
- subagent 的隔离上下文、终止条件、结果回传如何设计。
- sandbox 文件系统如何组织 uploads、workspace、outputs。
- skills 如何作为 Markdown 工作流包被发现、加载、组合。
- memory 如何跨 session 保存偏好、事实、工作上下文。
- 长任务如何通过 Gateway、thread、run、artifact 暴露给外部 UI/渠道。

设计启发：

- DeerFlow 更像“agent 操作系统”或“agent 工作站”，不是单纯 CLI。
- 它适合研究长任务、多产物、多 agent 的运行时，但复杂度明显高于 Codex CLI。
- 如果我们只是做 ClaudeCode-Runtime 的学习/增强，不应一开始照搬完整 DeerFlow；更合理的是拆借 sandbox、skills、memory、subagent protocol。

## 6. oh-my-openagent 的参考价值

本地 `oh-my-openagent` 是当前最贴近“OpenCode harness 最佳实践/激进实践”的样本。它展示了一个插件如何把 host 扩展成复杂运行时。

关键模块：

- 启动装配：`./oh-my-openagent/src/testing/create-plugin-module.ts`
- 插件接口：`./oh-my-openagent/src/plugin-interface.ts`
- 工具注册：`./oh-my-openagent/src/plugin/tool-registry.ts`
- hooks 聚合：`./oh-my-openagent/src/create-hooks.ts`
- 背景 agent：`./oh-my-openagent/src/features/background-agent/manager.ts`
- 任务系统：`./oh-my-openagent/src/features/claude-tasks/storage.ts`
- delegate task：`./oh-my-openagent/src/tools/delegate-task/tools.ts`
- skills：`./oh-my-openagent/src/plugin/skill-context.ts`
- compaction：`./oh-my-openagent/src/plugin/session-compacting.ts`
- team mode：`./oh-my-openagent/src/features/team-mode/`

可以学习的地方：

- 用 plugin boundary 接管复杂能力，而不是 fork host。
- tools、hooks、managers 分层清楚。
- 大量功能都用 config gate / disabled list 控制，降低与 host 冲突概率。
- task system、background manager、skills、compaction 是完整 vertical slice。

需要警惕：

- 功能密度过高，hooks 之间会形成隐性耦合。
- background agent、auto continuation、compaction、fallback 都会主动推动 session，状态机容易复杂。
- team mode 与 worktree 部分要区分“文档愿景”和当前实现。

## 7. 与 learn-claude-code-docs/zh 的对应关系

本地 `./learn-claude-code-docs/zh` 更像一套从零实现 agent harness 的教学拆解。可以把它当 glossary 和设计 checklist：

- `s01-the-agent-loop.md`：agent loop。
- `s02-tool-use.md`：tool dispatch。
- `s03-todo-write.md`：短期 todo。
- `s04-subagent.md`：子 agent。
- `s05-skill-loading.md`：skills 双层加载。
- `s06-context-compact.md`：上下文压缩。
- `s07-task-system.md`：持久任务图。
- `s08-background-tasks.md`：后台任务。
- `s09-agent-teams.md`、`s10-team-protocols.md`、`s11-autonomous-agents.md`：团队/自治 agent。
- `s12-worktree-task-isolation.md`：task 与 worktree 绑定。

对照关系：

- Codex CLI 最接近 s01/s02/s03/s06 的本地 agent 核心。
- OpenCode 最接近“host runtime + plugin extension surface”。
- oh-my-openagent 把 s04-s11 的很多概念放进 OpenCode plugin。
- DeerFlow 对应 s04/s05/s07/s08/s11/s12 的“长任务、多 agent、sandbox、memory”方向。

## 8. 值得借鉴的工程原则

### 8.1 先做边界，再做能力

不要一开始就堆几十个工具。先确定：

- host adapter 边界在哪里；
- tool registry 如何声明 schema、权限、输出；
- hook/middleware 顺序如何确定；
- session state 谁拥有；
- background task 如何通知父 session。

### 8.2 把权限和沙箱变成核心对象

Coding agent 的危险不在“会不会写代码”，而在“能不能读写执行得过宽”。需要明确：

- 哪些工具只读；
- 哪些工具写 workspace；
- 哪些工具可执行命令；
- 哪些工具能联网；
- 哪些操作必须用户批准；
- 哪些 agent 永远不能获取某些工具。

### 8.3 Task 不等于 Todo

Todo 适合当前 session 的短期步骤；Task 适合持久化、可阻塞、可分配、可恢复的工作单元。

推荐模型：

- Todo：当前对话内的轻量 progress view。
- Task：跨 session、background、subagent、team 的 coordination backbone。

### 8.4 Skills 应该渐进加载

DeerFlow 和 oh-my-openagent 都体现了一个方向：不要把所有技能正文塞进 system prompt。更好的结构是：

- 第一层：只暴露 skill name、description、适用条件。
- 第二层：模型显式调用 skill 时，再返回完整 `SKILL.md` 和资源。

这能降低 token 压力，也能减少无关技能污染决策。

### 8.5 Background Agent 要有父会话协议

后台任务不是简单开线程。需要协议：

- task id / session id；
- parent session id；
- status lifecycle；
- completion notification；
- error / abort / stale recovery；
- 并发限制；
- 结果摘要如何注入父 session。

### 8.6 Compaction 必须保留工作状态

压缩不能只摘要聊天记录。至少要保留：

- 当前目标；
- 已完成和未完成任务；
- 打开的关键文件；
- background task 状态；
- model/tool/agent 配置；
- 用户决策和约束；
- 下一步明确动作。

## 9. 推荐阅读顺序

### 第一阶段：建立 harness 地图

1. 读 `./learn-claude-code-docs/zh/s01-the-agent-loop.md` 到 `s08-background-tasks.md`。
2. 读 Codex 的官方 README 与 OpenAI agent loop 文章。
3. 读 OpenCode README、plugins/docs、agents/docs。
4. 读 DeerFlow README 中的 “From Deep Research to Super Agent Harness” 与 “Core Features”。

### 第二阶段：按 vertical slice 深挖

1. **Tool Pipeline**
   - OpenCode：plugin/tool 执行前后 hook。
   - Codex：tool call 到 shell/file/diff 执行。
   - DeerFlow：tools + MCP + sandbox file tools。
   - oh-my-openagent：`plugin/tool-registry.ts`、`plugin/tool-execute-before.ts`。

2. **Context / Compaction**
   - Codex：长会话压缩。
   - OpenCode：session summarization / compaction 相关实现。
   - oh-my-openagent：`plugin/session-compacting.ts` 与 compaction hooks。

3. **Task / Background / Subagent**
   - DeerFlow：lead agent + subagents。
   - oh-my-openagent：`delegate-task`、`BackgroundManager`、`claude-tasks`。
   - OpenCode：内置 general subagent 与 session model。

4. **Skills / Memory**
   - DeerFlow：skills/public、custom skills、memory。
   - oh-my-openagent：`skill-context.ts`、`tools/skill`、`skill_mcp`。
   - 本地 docs：`s05-skill-loading.md`。

5. **Security / Sandbox**
   - Codex：approval modes 和 sandbox。
   - DeerFlow：Docker/Kubernetes sandbox 与安全警告。
   - OpenCode：permissions、tools、enterprise/internal gateway。

### 第三阶段：形成自己的最小 harness

建议先做一个窄版本：

- 一个 provider adapter；
- 一个 session loop；
- 一个 tool registry；
- 一个 permission layer；
- 一个 task store；
- 一个 skill loader；
- 一个 compaction checkpoint；
- 一个 background runner；
- 一个 observability log。

不要一开始实现 team mode。team mode 应该在 task、background、message、worktree isolation 都稳定后再做。

## 10. 对 ClaudeCode-Runtime 的可借鉴架构

可以按以下分层设计：

```text
User Interface / CLI / API
  -> SessionRuntime
    -> ProviderAdapter
    -> ContextManager
    -> ToolRegistry
      -> PolicyEngine
      -> SandboxRunner
    -> TaskGraph
    -> SkillLoader
    -> BackgroundRunner
    -> EventLog / Telemetry
```

每层的职责：

- **SessionRuntime**：拥有 agent loop，不直接知道具体工具实现。
- **ProviderAdapter**：隐藏 OpenAI/Anthropic/OpenCode/Codex CLI 等差异。
- **ToolRegistry**：集中声明工具 schema、执行器、权限需求。
- **PolicyEngine**：决定是否允许执行、是否需要用户确认。
- **SandboxRunner**：隔离 shell、filesystem、network。
- **ContextManager**：维护 prompt、summary、checkpoint。
- **TaskGraph**：持久化任务、依赖、owner、状态。
- **SkillLoader**：渐进式加载技能正文和资源。
- **BackgroundRunner**：运行子任务并回传结果。
- **EventLog**：保存可恢复、可审计的运行轨迹。

## 11. 当前判断

如果只选一个项目学习“本地 coding agent loop”，先看 **Codex CLI**。

如果要学习“可扩展 coding agent host”，看 **OpenCode**。

如果要学习“插件如何把 host 扩展成复杂 harness”，看 **oh-my-openagent**。

如果要学习“长任务、多 agent、sandbox、memory、skills 的完整工作站”，看 **DeerFlow**。

对我们当前目标，最实用的组合是：

1. 用 Codex 学 loop 和权限。
2. 用 OpenCode 学 host/plugin 边界。
3. 用 oh-my-openagent 学 OpenCode 插件式增强。
4. 用 DeerFlow 学 long-horizon runtime、skills、memory、sandbox。

## 12. 资料来源

官方与一手来源：

- OpenCode GitHub：<https://github.com/anomalyco/opencode>
- OpenCode Docs：<https://opencode.ai/docs>
- OpenCode Providers：<https://opencode.ai/docs/providers>
- OpenCode Enterprise：<https://opencode.ai/docs/enterprise>
- Codex GitHub：<https://github.com/openai/codex>
- Codex Docs：<https://developers.openai.com/codex>
- Codex CLI Help Center：<https://help.openai.com/en/articles/11096431>
- OpenAI Codex Agent Loop：<https://openai.com/index/unrolling-the-codex-agent-loop/>
- DeerFlow GitHub：<https://github.com/bytedance/deer-flow>
- DeerFlow Website：<https://deerflow.tech>

本地参考：

- `./oh-my-openagent`
- `./learn-claude-code-docs/zh`

