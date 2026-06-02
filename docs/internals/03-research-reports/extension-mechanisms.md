# Claude Code 扩展机制与基础设施分析

> 源码来源：`claude-code-sourcemap-main/claude code analysis/src/`
> 分析日期：2026-05-20

---

## 1. Memory 系统

### 1.1 四层记忆架构

Claude Code 的 Memory 系统从架构上分为四个层级，每层对应不同的作用域和持久化策略：

| 层级 | 位置 | 作用域 | 关键文件 |
|------|------|--------|----------|
| **Auto Memory** | `~/.claude/projects/<slug>/memory/` | 跨 Session（同项目） | `memdir/memdir.ts` |
| **Session Memory** | Session 工作目录下 | 当前 Session | `services/SessionMemory/sessionMemory.ts` |
| **Agent Memory** | 子 Agent 独立上下文 | 单次 Agent 调用 | `memdir/memdir.ts` buildMemoryPrompt |
| **Team Memory** | `<autoMemPath>/team/` | 跨 Session 团队共享 | `memdir/teamMemPaths.ts` |

### 1.2 MEMORY.md 构建与截断保护

**核心常量定义**（`memdir/memdir.ts`）：

```typescript
export const MAX_ENTRYPOINT_LINES = 200
export const MAX_ENTRYPOINT_BYTES = 25_000
```

`truncateEntrypointContent()` 函数实现双重截断策略：

1. **行数截断优先**：若超过 200 行，取前 200 行
2. **字节数截断兜底**：若截断后仍超过 25000 字节，在最后一个 `\n` 处裁切（不切半行）
3. **截断警告注入**：截断后在 MEMORY.md 尾部注入 `> WARNING: MEMORY.md is ...` 提示

**构建流程**（`buildMemoryPrompt` 函数）：
1. 读取 `{memoryDir}/MEMORY.md` 文件（同步读取，prompt 构建是同步的）
2. 调用 `buildMemoryLines()` 生成行为指令（4 种记忆类型、保存规则、访问时机）
3. 将截断后的 MEMORY.md 内容拼接到末尾 `## MEMORY.md` 节
4. 如文件为空，提示 "Your MEMORY.md is currently empty"

**记忆保存的两步流程**（index 模式）：
- **Step 1**：写入独立主题文件（如 `user_role.md`），带 frontmatter（type/path/title/description）
- **Step 2**：在 `MEMORY.md` 中添加一行指针 `- [Title](file.md) -- one-line hook`

> 另有 skipIndex 模式（GrowthBook flag `tengu_moth_copse`），跳过 Step 2 的 index 要求。

### 1.3 KAIROS 每日日志模式

当 `feature('KAIROS')` 启用且 `getKairosActive()` 时，Assistant 模式采用**追加式每日日志**替代直接编辑 MEMORY.md：

- 日志路径：`<memDir>/logs/YYYY/MM/YYYY-MM-DD.md`
- 模型被指示往当日日志追加带时间戳的条目
- 夜间 `/dream` skill 负责将日志蒸馏为 topic 文件并更新 MEMORY.md
- Prompt 使用路径模式而非具体日期（避免日期变更导致 prompt cache 失效）

### 1.4 `isAutoMemoryEnabled()` 五层优先级控制

定义在 `memdir/paths.ts`，按优先级从高到低：

```
1. CLAUDE_CODE_DISABLE_AUTO_MEMORY 环境变量
   - 1/true → 关闭
   - 0/false → 开启
2. CLAUDE_CODE_SIMPLE (--bare) → 关闭
3. CLAUDE_CODE_REMOTE 且无 CLAUDE_CODE_REMOTE_MEMORY_DIR → 关闭
4. settings.json 中的 autoMemoryEnabled → 跟随用户设定
5. 默认 → 开启
```

**路径解析优先级**（`getAutoMemPath`）：
1. `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` 环境变量（完整路径覆盖，Cowork SDK 使用）
2. `settings.json` 中 `autoMemoryDirectory`（仅限 policy/local/user 源，排除 project）
3. `<memoryBase>/projects/<sanitized-git-root>/memory/`

> 安全设计：`validateMemoryPath()` 拒绝相对路径、根目录、Windows 盘符、UNC 路径和含空字节的路径。projectSettings（`.claude/settings.json`）的 `autoMemoryDirectory` 被**故意排除**，防止恶意仓库写入 `~/.ssh` 等敏感目录。

### 1.5 记忆注入 System Prompt 的时机

`loadMemoryPrompt()` 在系统 prompt 构建时调用（通过 `systemPromptSection` 缓存），分派逻辑：

- **KAIROS + auto 启用** → `buildAssistantDailyLogPrompt()`
- **TEAMMEM + auto 启用** → `teamMemPrompts.buildCombinedMemoryPrompt()`
- **auto 启用** → `buildMemoryLines()` 结果
- **auto 禁用** → `null`（日志 `tengu_memdir_disabled`）

---

## 2. Skills 系统

### 2.1 Skill 文件格式

Skill 文件使用 **frontmatter + markdown body** 格式：

```markdown
---
name: "display name"
description: "what the skill does"
when_to_use: "when this skill should be suggested"
argument-hint: "[args...]"
allowed-tools: [Bash, Read, Write, Edit, Grep, Glob]
user-invocable: true
disable-model-invocation: false
model: "claude-sonnet-4-5"
effort: high
context: fork
agent: "worker"
hooks:
  PreToolUse:
    - matcher: ""
      command: "echo 'before tool'"
paths:
  - "src/**/*.py"
---

# Skill body (markdown instructions for the model)
```

**核心 frontmatter 字段**（见 `parseSkillFrontmatterFields`）：
- `name`: 显示名称
- `description`: 技能描述（为空时自上抽自动提取 markdown 第一段）
- `whenToUse`: 注入系统 prompt 的触发描述
- `allowed-tools`: skill 执行时可使用的工具白名单
- `user-invocable`: 用户能否通过 `/skill-name` 调用（默认 true）
- `disable-model-invocation`: 禁止模型自动调用
- `context: fork`: 要求 skill 在独立 fork 上下文中运行
- `agent: "worker"`: 指定 skill 由工作 Agent 执行
- `effort`: 推理深度（low/medium/high 或整数）
- `paths`: 条件激活路径（gitignore 模式匹配，仅在触及匹配文件时激活）
- `hooks`: Skill 级别的 PreToolUse/PostToolUse 等 hook 配置

### 2.2 加载流程

**完整加载链路**（`skills/loadSkillsDir.ts`）：

```
磁盘扫描 → Frontmatter 解析 → 创建 Command 对象 → 注册为 slash 命令 → Prompt 注入
```

**扫描源**（`getSkillDirCommands`）：

1. **managed skills**: `<managedPath>/.claude/skills/`（企业策略）
2. **user skills**: `~/.claude/skills/`
3. **project skills**: 从 CWD 向上遍历各目录的 `.claude/skills/`
4. **additional dirs** (`--add-dir`): 额外目录的 `.claude/skills/`
5. **legacy commands**: `.claude/commands/`（旧目录格式）

> `--bare` 模式下**完全跳过**自动扫描，仅加载 `--add-dir` 显式指定的 skills。

**技能目录格式**：必须是 `skill-name/SKILL.md` 的目录结构（单 `.md` 文件不被 `/skills/` 支持）。

**去重机制**：通过 `realpath` 获取文件规范路径，同一文件不同路径只加载一次（最大 overlapping 父目录问题）。

### 2.3 动态技能发现

支持两种动态机制：

1. **路径发现**（`discoverSkillDirsForPaths`）：当模型 Read/Write/Edit 某个文件时，从文件所在目录向上层遍历，检查是否存在 `.claude/skills/` 目录。跳过被 `.gitignore` 忽略的目录。
2. **条件激活**（`activateConditionalSkillsForPaths`）：带 `paths` frontmatter 的 skill 在匹配到操作文件路径时才被激活（使用 gitignore 模式库匹配）。

### 2.4 Bundled Skills 内置技能列表

定义在 `skills/bundledSkills.ts`，注册类型 `BundledSkillDefinition`：

| Skill | 功能 | 关键特性 |
|-------|------|----------|
| `/batch` | 批量并行分派 | agent: "worker", context: "fork" |
| `/claude-api` | Claude API 应用构建/调试 | 含 reference files 懒提取 |
| `/debug` | 系统性调试 | — |
| `/init` | 初始化 CLAUDE.md | — |
| `/loop` | 定时循环执行 | — |
| `/remember` | 显式记忆保存 | — |
| `/simplify` | 代码简化/质量评审 | — |
| `/verify` | 验证代码变更 | agent: "worker" |
| `/skillify` | Skill 创作 | — |
| `/update-config` | 配置 settings.json | — |
| `/keybindings` | 键位绑定 | — |
| `/stuck` | 卡住时求助 | — |
| `/review` | PR 审查 | — |
| `/lorem-ipsum` | 占位文本生成 | — |

**Bundled Skill 注册流程**：
1. 调用 `registerBundledSkill(definition)` 注册进全局 `bundledSkills[]`
2. 带 `files` 字段的 skill 在首次调用时懒提取到 `~/.claude/bundled-skills/<name>/`
3. 提取使用 `O_NOFOLLOW|O_EXCL` 安全写入，防止 symlink 攻击
4. Prefix 注入 `Base directory for this skill: <dir>` 使模型可 Read/Grep 引用文件

### 2.5 MCP Skills

通过 `fetchMcpSkillsForClient`（feature gate `MCP_SKILLS`），MCP 服务器可暴露 prompts 作为 skills。MCP skills 的安全限制：**禁止内联 shell 命令执行**（不通过 `executeShellCommandsInPrompt`），因为 MCP skills 来自远程不受信任源。

### 2.6 Command 对象结构

`createSkillCommand()` 返回的 `Command` 对象核心属性：

- `type: 'prompt'` — 提示型命令
- `getPromptForCommand(args, ctx)` — 模板解析 + 参数替换 + `${CLAUDE_SKILL_DIR}` / `${CLAUDE_SESSION_ID}` 变量注入
- `loadedFrom` — 来源标记（bundled/skills/commands_DEPRECATED/plugin/managed/mcp）
- `source` — 设置源（userSettings/projectSettings/policySettings）
- `isEnabled()` — Feature gate 动态开关
- `paths` — 条件路径列表
- `context: 'fork'` — 要求 fork 执行

---

## 3. Hooks 系统

### 3.1 核心架构

Hooks 是**用户定义的外部脚本**，在特定事件点由 Claude Code 调用。实现在 `utils/hooks.ts`（主执行管线）和 `services/tools/toolHooks.ts`（工具层包装）。

**核心执行模式**：
- Shell 命令被 spawn 为子进程
- stdin 接收 JSON 输入（hook 上下文）
- stdout 返回 JSON 或纯文本
- stderr 输出给用户
- exit code 控制行为

### 3.2 全部 Hook 事件（`getHookEventMetadata`，`hooksConfigManager.ts`）

| Hook 事件 | 触发时机 | exit code 2 行为 |
|-----------|----------|------------------|
| **PreToolUse** | 工具执行前 | 阻止工具执行，stderr 给模型 |
| **PostToolUse** | 工具执行成功后 | stderr 立即显示给模型 |
| **PostToolUseFailure** | 工具执行失败后 | stderr 立即显示给模型 |
| **PermissionDenied** | 自动模式拒绝工具后 | 不适用（retry 通过 JSON 返回） |
| **Notification** | 权限提示/空闲/认证等通知 | 不适用 |
| **UserPromptSubmit** | 用户提交 prompt 时 | 阻止处理，清除原始 prompt |
| **SessionStart** | Session 启动（含 resume/clear/compact） | 阻止错误被忽略 |
| **Stop** | Agent 结束响应前 | stderr 给模型，继续对话 |
| **StopFailure** | 因 API 错误结束 turn（fire-and-forget） | 忽略 |
| **SubagentStart** | 子 Agent 启动时 | 阻止错误被忽略 |
| **SubagentStop** | 子 Agent 结束响应前 | stderr 给子 Agent |
| **PreCompact** | 上下文压缩前 | 阻止压缩 |
| **PostCompact** | 上下文压缩后 | 仅 stderr 给用户 |
| **TeammateIdle** | Teammate 空闲时 | — |
| **TaskCreated/Completed** | 任务创建/完成 | — |
| **ConfigChange** | 配置变更 | — |
| **CwdChanged** | 工作目录变更 | — |
| **FileChanged** | 文件变更 | — |
| **InstructionsLoaded** | 指令加载后 | — |
| **PermissionRequest** | 权限请求时（MCP 代理模式） | — |
| **Elicitation/ElicitationResult** | MCP elicitation 交互 | — |

### 3.3 Hook 配置加载

Hook 配置来自多个源（按加载顺序）：

1. **Policy/Managed settings** — 企业级 hook（`managed-settings.json`）
2. **User settings** — `~/.claude/settings.json` 中的 `hooks` 字段
3. **Local settings** — `.claude/settings.local.json`
4. **Project settings** — `.claude/settings.json`（项目级）
5. **插件 hooks** — `loadPluginHooks.ts`
6. **Skill hooks** — skill frontmatter 中的 `hooks` 字段

**Matcher 优先级排序**：`sortMatchersByPriority()` 确保精确匹配优先于通配符匹配。

### 3.4 PreToolUse 拦截逻辑详解

`runPreToolUseHooks()` 的完整决策链（`services/tools/toolHooks.ts`）：

```
executePreToolHooks() 返回 generator →
  ├─ permissionBehavior === 'allow' → 跳过交互提示，但 deny/ask 规则仍检查
  │   └─ checkRuleBasedPermissions() 检查 settings.json 的规则
  │       ├─ deny 规则覆盖 hook 的 allow
  │       └─ ask 规则弹出 dialog
  ├─ permissionBehavior === 'deny' → 拒绝工具执行
  ├─ permissionBehavior === 'ask' → 强制弹出 dialog，显示 hook 的 ask message
  ├─ blockingError (exit code 2) → behavior: 'deny'
  ├─ updatedInput → 修改工具参数但不改变权限决策
  └─ preventContinuation → 阻止后续工具调用
```

**关键不变式**：Hook 的 'allow' **不能**绕过 `settings.json` 中的 deny/ask 规则（与 inc-4788 保持一致）。

### 3.5 Hook JSON 输出协议

Hook 可返回 JSON（`hookJSONOutputSchema`）控制行为：

- **同步响应**（`isSyncHookJSONOutput`）：
  - `{decision: "block"}` — 阻止工具执行
  - `{decision: "allow"}` — 允许（跳过提示）
  - `{hookSpecificOutput: {hookEventName, permissionDecision: "allow"/"deny"/"ask"}}` — 权限决策
  - `{additionalContext: "..."}` — 附加上下文
  - `{async: false}` — 同步完成
  - `{continue: false}` — 停止继续
  - `{stopReason: "..."}` — 停止原因
  - `{suppressOutput: true}` — 隐藏输出
  - `{systemMessage: "..."}` — 注入系统消息

- **异步响应**（`isAsyncHookJSONOutput`）：
  - `{async: true, asyncTimeout: 60000}`
  - hook 在后台继续，结果通过 `hook_async_response` 附件消息异步投递

---

## 4. MCP 集成

### 4.1 作为 Client：连接 MCP Server

MCP Client 实现在 `services/mcp/client.ts`，核心职责：

**支持4种传输协议**：
- **stdio** — 子进程 stdin/stdout JSON-RPC
- **SSE (Server-Sent Events)** — HTTP 长连接
- **Streamable HTTP** — 流式 HTTP（MCP 规范新标准）
- **WebSocket** — 双向 WebSocket

**连接生命周期**：
1. 从 `getAllMcpConfigs()` 加载配置（`.mcp.json` + settings.json）
2. 检查 `isMcpServerDisabled()` — 禁用的 server 跳过
3. 创建对应的 Transport 客户端
4. 通过 OAuth/认证流程（`ClaudeAuthProvider`）
5. 列表工具（`ListToolsResultSchema`）、Prompts、Resources
6. 注册工具名 `mcp__{server}__{tool}`（通过 `buildMcpToolName()`）
7. 缓存连接的 MCP Server 实例

**401 重试机制**：`createClaudeAiProxyFetch()` 在收到 401 时调用 `handleOAuth401Error` 强制刷新 token 并重试一次。`needs-auth` 状态缓存 15 分钟避免重复 401 风暴。

**认证缓存**：`mcp-needs-auth-cache.json` 文件持久化 needs-auth 条目（TTL=15 分钟）。

**Session 过期检测**：`isMcpSessionExpiredError()` 同时检查 HTTP 404 + JSON-RPC code -32001，防止与普通 404 混淆。

### 4.2 作为 Server：暴露内部工具

通过 `SDK Control Server`（`SdkControlClientTransport`），Claude Code 作为 MCP Server 暴露：

- 内部工具（Bash/Read/Write/Edit/Grep/Glob 等）
- 支持外部客户端连接并调用这些工具
- 权限控制通过 `PermissionContext` 转发

### 4.3 工具命名规则

MCP 工具统一命名为 `mcp__{server}__{tool}`（`mcpStringUtils.ts` 中的 `buildMcpToolName`），确保：

- 命名空间隔离：不同 MCP Server 的同名工具不冲突
- 可追溯性：工具名即标识其来源
- 权限规则匹配：`permissions.allow/deny` 可精确匹配到 server 级别

### 4.4 配置来源

MCP Server 配置来自多个源：

1. **`.mcp.json`** — 项目级 MCP server 配置（优先级最高）
2. **`~/.claude/.mcp.json`** — 用户级 MCP server 配置
3. **`settings.json` 中的 `mcpServers` 字段** — 兼容旧格式
4. **Flag settings** — 通过 GrowthBook 远程下发的 MCP 配置
5. **Plugins** — 插件可注册 MCP server

**MCP Skills**（`skills/mcpSkills.ts`）：MCP Server 的 prompts 列表可被注册为 skills（feature gate `MCP_SKILLS`），为 MCP 工具提供自然语言操作指南。

---

## 5. 多 Agent 协作

### 5.1 Coordinator Mode（协调器模式）

实现于 `coordinator/coordinatorMode.ts`，通过环境变量 `CLAUDE_CODE_COORDINATOR_MODE=1` 激活。

**核心概念**：
- **Coordinator**：主控制者，不直接执行代码，通过 AgentTool 分派 Worker
- **Worker**：执行实际任务的异步 Agent
- **通信**：Worker 完成时发送 `<task-notification>` XML 格式的消息给 Coordinator
- **Session 恢复**：`matchSessionMode()` 检测 session 持久化的 mode 与当前 mode 是否匹配，不匹配时自动翻转环境变量

**工具集**：
- `AgentTool` — 启动新 Worker
- `SendMessage` — 向已有 Worker 发送追加指令
- `TaskStop` — 停止运行中的 Worker
- `TeamCreate/TeamDelete` — 管理 Worker 团队

**Worker 能力**（`getCoordinatorUserContext`）：
- Normal mode：标准工具 + MCP tools + Skills
- Simple mode（`--bare`）：仅 Bash + Read + Edit

### 5.2 Teammate / Swarm 系统

实现于 `utils/swarm/`，提供并行多 Agent 的终端级管理。

**后端注册表**（`backends/registry.ts`）：

| 后端 | 类型 | 使用场景 |
|------|------|----------|
| **InProcess** | `in-process` | 非交互 session / 无终端多路复用器时 |
| **TmuxBackend** | `tmux` | tmux 内 / tmux 可用时 |
| **ITermBackend** | `iterm2` | macOS iTerm2 原生分屏 |

**检测优先级**：
```
1. 已在 tmux 内 → 使用 TmuxBackend
2. 在 iTerm2 且 it2 CLI 可用 → 使用 ITermBackend
3. 在 iTerm2 但无 it2 → 回退 tmux（如 tmux 可用）
4. 均不在 tmux/iTerm2 但 tmux 已安装 → 创建外部 tmux session
5. 无可用后端 → 回退 InProcessBackend
```

**回退策略**：`inProcessFallbackActive` 标志，一旦设置整个 session 生命周期保持 in-process。

**环境变量**：
- `CLAUDE_CODE_TEAMMATE_COMMAND` — spawn 子 teammate 的命令
- `CLAUDE_CODE_AGENT_COLOR` — teammate 颜色标记
- `CLAUDE_CODE_PLAN_MODE_REQUIRED` — 要求 teammate 先 plan 后执行

**Mailbox 通信**（`useMailboxBridge.ts`）：teammate 间通过共享 mailbox 文件交换消息的异步通信机制。

---

## 6. Bridge / Remote 系统

### 6.1 架构概览

Bridge 系统实现了 Claude Code 的**远程会话桥接**（CCR - Claude Code Remote），核心定义在 `bridge/` 目录。

**核心组件**（`bridge/bridgeMain.ts`）：
- **BridgeApiClient**：与远程 Bridge 服务器的 HTTP/WS API 客户端
- **SessionSpawner**：远程创建 claude code session 的 spawner
- **CapacityWake**：按需唤醒休眠 session 的机制
- **TokenRefreshScheduler**：JWT token 定期刷新

**Bridge Loop**（`runBridgeLoop`）：
```
connect → create session → poll for messages → dispatch to session → mark done
```

### 6.2 WebSocket 通信

`bridge/replBridge.ts` 和 `replBridgeTransport.ts` 实现基于 WebSocket 的 REPL 通信传输层，支持双向消息传递、流式输出和断线重连。

**Backoff 策略**（`DEFAULT_BACKOFF`）：
- Connection retry: 2s 起始，120s 上限，10 分钟放弃
- General retry: 500ms 起始，30s 上限，10 分钟放弃

### 6.3 Session Runner

`bridge/sessionRunner.ts` 中的 `createSessionSpawner` 负责在远程环境 spawn claude code 子进程。Multi-session spawn 通过 GrowthBook gate `tengu_ccr_bridge_multi_session` 控制。

---

## 7. 基础设施

### 7.1 Session 持久化（JSONL 格式）

实现于 `utils/sessionStorage.ts`。

**格式**：每行一个 JSON 对象（JSONL），支持增量追加。

**消息类型**（`isTranscriptMessage`）：
- `user` — 用户消息
- `assistant` — 模型响应
- `attachment` — 附件（hook 结果、通知等）
- `system` — 系统消息

> Progress 消息**不被持久化**（非 transcript），避免在 resume 时产生孤儿消息链（#14373/#23537）。

**Tombstone 重写**：超过 50MB 的 session 文件在加载时走慢路径（读取+重写），防止 OOM。

**读取优化**：`readHeadAndTail`/`readTranscriptForLoad` 使用 select-read 策略跳过预压缩消息，仅加载有意义的内容。

### 7.2 Settings 配置系统

实现于 `utils/settings/`。

**Settings 源层级**（`constants.ts` 中的 `SETTING_SOURCES`）：

| 源 | 路径 | 用途 |
|----|------|------|
| `policySettings` | `managed-settings.json` + `managed-settings.d/*.json` | 企业 IT 策略 |
| `userSettings` | `~/.claude/settings.json` | 用户全局偏好 |
| `localSettings` | `.claude/settings.local.json` | 本地开发覆盖 |
| `projectSettings` | `.claude/settings.json` | 项目级共享 |
| `flagSettings` | `--setting` CLI flag 内联 | 一次性会话 |
| `mdmSettings` | macOS MDM / Windows GPO | 企业设备管理 |

**加载/合并**：`loadManagedFileSettings()` 使用 `lodash-es/mergeWith` 按优先级合并所有源（drop-in 文件按字母序排序，后加载的覆盖前加载的）。

**验证**：通过 Zod Schema（`SettingsSchema`）在加载时验证，无效字段被过滤并报告警告。

#### ChangeDetector 热更新

`changeDetector.ts` 使用 **chokidar** 监听以下文件的变更：

- 各源对应的 settings.json 文件
- `managed-settings.d/*.json` drop-in 目录
- macOS MDM 注册表（30 分钟轮询间隔）

**内部写入区分**：通过 `markInternalWrite()` + `INTERNAL_WRITE_WINDOW_MS`（5 秒）窗口避免 Claude Code 自己的配置写入触发通知。

**删除 Grace Period**：文件删除后 1.7 秒窗口内如果重新创建（更新模式常见），视为 change 而非 delete。

**配置变更 Hook**：检测到变更后触发 `executeConfigChangeHooks()`，通知所有监听者。

### 7.3 Slash 命令注册机制

`commands.ts` 定义了 50+ 内置命令（模块级 import），每个命令模块通过注册表被索引。Skills 系统将所有文件/内置/MCP skills 统一注册为相同格式的 `Command` 对象（`type: 'prompt'`），通过 `useMergedCommands` hook 合并到用户可用命令列表。

**内置命令清单**（部分）：
`/clear` `/compact` `/commit` `/config` `/cost` `/diff` `/doctor` `/help` `/ide` `/init` `/login` `/logout` `/mcp` `/memory` `/pr-comments` `/release-notes` `/resume` `/review` `/security-review` `/status` `/tasks` `/theme` `/usage` `/vim` `/voice`

**命令实现目录**：各命令的实现在 `commands/<name>/index.ts`

### 7.4 Feature Gate 编译时条件开关

通过 `bun:bundle` 的 `feature('FLAG_NAME')` 函数实现**编译时 dead code elimination**：

```typescript
if (feature('TEAMMEM')) {
  // 这段代码仅在 TEAMMEM feature flag 启用时被打包
}
```

**关键 feature flags**：
- `TEAMMEM` — Team 记忆
- `KAIROS` — Assistant 模式
- `COORDINATOR_MODE` — 协调器模式
- `BRIDGE_MODE` — 远程桥接
- `MCP_SKILLS` — MCP prompts 作为 skills
- `CHICAGO_MCP` — Computer Use MCP
- `VOICE_MODE` — 语音输入
- `EXTRACT_MEMORIES` — 后台记忆提取

**GrowthBook 运行时门控**：除编译时 flag 外，还有 `getFeatureValue_CACHED_MAY_BE_STALE` / `checkGate_CACHED_OR_BLOCKING` 等运行时门控函数，实现远程控制的 feature rollout。

---

## 8. 与 Python 复刻版的对应

| TypeScript 扩展模块 | 源文件 | Python 复刻对应模块建议 |
|---------------------|--------|-------------------------|
| **Memory System** | `memdir/memdir.ts` + `paths.ts` | `memory/memory_builder.py`, `memory/paths.py` |
| **Session Memory** | `services/SessionMemory/sessionMemory.ts` | `services/session_memory.py` |
| **Skills Loader** | `skills/loadSkillsDir.ts` + `bundledSkills.ts` | `skills/loader.py`, `skills/registry.py` |
| **Hooks System** | `utils/hooks.ts` + `hooks/hooksConfigManager.ts` | `hooks/executor.py`, `hooks/config_manager.py` |
| **MCP Client** | `services/mcp/client.ts` | `mcp/client.py` |
| **MCP Auth** | `services/mcp/auth.ts` | `mcp/auth.py` |
| **Coordinator** | `coordinator/coordinatorMode.ts` | `coordinator/mode.py` |
| **Swarm/Teammate** | `utils/swarm/` | `swarm/backends/`, `swarm/registry.py` |
| **Bridge/Remote** | `bridge/bridgeMain.ts` | `bridge/client.py`, `bridge/transport.py` |
| **Session Storage** | `utils/sessionStorage.ts` | `storage/session.py` (JSONL) |
| **Settings** | `utils/settings/settings.ts` + `changeDetector.ts` | `config/settings.py`, `config/watcher.py` |
| **Commands** | `commands.ts` | `commands/registry.py` |
| **Feature Gates** | `feature('...')` → `services/analytics/growthbook.ts` | `features/gates.py` |

### 关键接口对照

| 接口名 | TS 签名 | Python 等价 |
|--------|---------|-------------|
| `buildMemoryLines` | `(displayName, memoryDir, extraGuidelines?, skipIndex?) => string[]` | `def build_memory_lines(...) -> list[str]` |
| `truncateEntrypointContent` | `(raw: string) => EntrypointTruncation` | `def truncate_entrypoint(content: str) -> TruncationResult` |
| `loadMemoryPrompt` | `async () => Promise<string \| null>` | `async def load_memory_prompt() -> Optional[str]` |
| `createSkillCommand` | `({...params}) => Command` | `def create_skill_command(**params) -> Command` |
| `executePreToolHooks` | `async generator` | `async def execute_pre_tool_hooks() -> AsyncIterator` |
| `getHookEventMetadata` | `(toolNames) => Record<HookEvent, HookEventMetadata>` | `def get_hook_event_metadata(tool_names) -> dict` |
| `getAllMcpConfigs` | `() => McpServerConfig[]` | `def get_all_mcp_configs() -> list[McpServerConfig]` |

### 架构关键约束（Python 复刻需特别注意）

1. **Safe write 安全写入**：`safeWriteFile` 使用 `O_NOFOLLOW|O_EXCL`（Windows 上用 `wx` 标志），防止 symlink 攻击
2. **Memory path 验证**：`validateMemoryPath()` 拒绝的路径类别需要在 Python 中等价实现
3. **Hook 异步模型**：JS 的 async generator 模式在 Python 中需对应为 `AsyncIterator[Result]`
4. **Settings merge 语义**：`lodash-es/mergeWith` 的 deep merge 在 Python 中需用自定义 deep merge 函数
5. **Feature flip 三层**：编译时 feature-flag（不可变）→ GrowthBook runtime gate（缓存+可过期）→ env var（最高优先）
