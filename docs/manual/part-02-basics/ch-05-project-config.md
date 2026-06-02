# 第5章 项目级配置（`.claude/` 目录）

Claude Code 的配置是分层、分级的。理解 `.claude/` 目录的结构和加载规则，是高效使用的前提。本章基于官方文档与源码分析，详细说明每个文件的位置、作用、加载顺序和最佳实践。

---

## 5.1 `.claude/` 目录完整地图

### 项目级配置（`your-project/`）

| 文件 | 位置 | 是否入 git | 作用 |
|------|------|-----------|------|
| `CLAUDE.md` | 项目根或 `.claude/` | 是 | 每会话加载的项目指令 |
| `.mcp.json` | 项目根 | 是 | 团队共享的 MCP 服务器配置 |
| `.worktreeinclude` | 项目根 | 是 | 复制 gitignored 文件到新 worktrees |
| `settings.json` | `.claude/` | 是 | 权限、hooks、环境变量、模型等设置 |
| `settings.local.json` | `.claude/` | 否（gitignored） | 个人本地覆盖，如 API key、个人偏好 |
| `rules/*.md` | `.claude/rules/` | 是 | 按主题或路径范围组织的指令 |
| `skills/<name>/SKILL.md` | `.claude/skills/` | 是 | `/name` 调用的可重用提示能力 |
| `commands/*.md` | `.claude/commands/` | 是 | 单文件轻量命令（旧机制） |
| `output-styles/*.md` | `.claude/output-styles/` | 是 | 自定义系统提示风格 |
| `agents/*.md` | `.claude/agents/` | 是 | 子代理定义 |
| `agent-memory/<name>/MEMORY.md` | `.claude/agent-memory/` | 是 | 子代理持久记忆 |

### 全局级配置（`~/.claude/`）

| 文件 | 作用 |
|------|------|
| `CLAUDE.md` | 所有项目的个人偏好（如你习惯用单引号还是双引号） |
| `settings.json` | 所有项目的默认设置 |
| `keybindings.json` | 自定义快捷键 |
| `themes/*.json` | 自定义颜色主题 |
| `skills/` | 个人 skills（全局可用，所有项目都能调用） |
| `rules/` | 个人规则（全局可用） |
| `commands/` | 个人命令（全局可用） |
| `agents/` | 个人子代理（全局可用） |
| `output-styles/` | 个人输出样式 |
| `projects/<project>/memory/` | 自动记忆（Claude 自写，每次停止边界触发） |
| `agent-memory/<name>/` | 跨项目子代理记忆 |

### 应用数据（`~/.claude/`）

| 路径 | 内容 | 自动清理 |
|------|------|----------|
| `projects/<project>/<session>.jsonl` | 完整对话记录 | 默认 30 天 |
| `file-history/<session>/` | 文件编辑前的快照 | 是 |
| `plans/` | Plan Mode 生成的计划文件 | 是 |
| `debug/` | 调试日志 | 是 |
| `history.jsonl` | 提示历史（用于上箭头回忆） | 保留 |
| `stats-cache.json` | 令牌和成本统计 | 保留 |
| `remote-settings.json` | 托管设置缓存 | 保留 |

> **Windows 路径**：`~/.claude` 解析为 `%USERPROFILE%\.claude`。

---

## 5.2 配置优先级链

Claude Code 按以下优先级读取设置（**后覆盖前**）：

```
全局 settings (~/.claude/settings.json)
    ↓ 项目 settings (./.claude/settings.json)
    ↓ 本地 settings (./.claude/settings.local.json)
    ↓ 托管设置（企业/团队管理员配置，MDM/Group Policy 分发）
    ↓ CLI 标志 (--model, --permission-mode 等)
```

**注意**：托管设置的优先级在 local 和项目 settings 之上，但低于 CLI 标志。用户无法覆盖托管设置中的 `permissions.deny`、`sandbox.enabled` 等安全策略。

**清除项目数据**：

```bash
claude project purge <path>          # 交互式确认删除
claude project purge <path> --dry-run # 预览要删什么
claude project purge <path> --yes     # 跳过确认
```

会删除：transcripts、task lists、debug logs、file-edit history、prompt history、项目 entry。

---

## 5.3 CLAUDE.md 项目说明书

CLAUDE.md 是 Claude Code 最重要的项目级配置。每次启动会话时，它会被完整加载到上下文中，作为系统提示的一部分。

### CLAUDE.md 加载位置（按顺序）

| 范围 | 位置 | 共享对象 |
|------|------|---------|
| 托管策略 | macOS: `/Library/Application Support/ClaudeCode/`<br>Linux/WSL: `/etc/claude-code/`<br>Windows: `C:\Program Files\ClaudeCode\` | 组织所有用户 |
| 用户级 | `~/.claude/CLAUDE.md` | 仅你（所有项目） |
| 项目级 | `./CLAUDE.md` 或 `./.claude/CLAUDE.md` | 团队成员 |
| 本地级 | `./CLAUDE.local.md` | 仅你（当前项目） |

**加载规则**：
- 从工作目录**向上遍历目录树**，所有发现的文件**连接**而非覆盖
- 越靠近工作目录的文件越后读取（优先级越高）。论文指出这是一个关键设计：**"Files closer to the current directory have higher priority (loaded later)."** 因为更晚加载的内容会覆盖模型对更早加载内容的注意力
- 同级中 `CLAUDE.local.md` 在 `CLAUDE.md` 之后附加
- **子目录的 CLAUDE.md 和 rules 是 lazy-load**——只有当 Agent 实际读取该目录中的文件时才会加载。论文原话："for nested directories below CWD, even unconditional rules are loaded lazily when the agent reads files in matching directories." 这意味着模型的指令集可以**随着对话中探索新的代码库区域而动态演化**

### CLAUDE.md 的关键架构事实：它是 Guidance，不是 Enforcement

论文分析揭示了一个对日常使用至关重要的架构细节：

> CLAUDE.md 的内容是作为**用户上下文（user context）**而非**系统提示（system prompt）**交付给模型的。

这意味着什么？

- **系统提示**（system prompt）是模型几乎不可能违反的硬约束——它直接影响模型的输出分布
- **用户上下文**（user context）是对话的一部分——模型对它的遵守是**概率性的而非确定性的**

论文原话：
> "CLAUDE.md content is delivered as user context (a user message), not as system prompt content. This architectural choice has a significant implication: model compliance with these instructions is probabilistic rather than guaranteed."

翻译：**模型对 CLAUDE.md 指令的遵守是概率性的，而非有保证的。**

这就是为什么你会经常遇到 Claude "不听" 的情况——它读到了你的 CLAUDE.md 规则，但在某些上下文中选择性地忽略或遗忘了它们。

**确定性执行层在哪里？** 在**权限规则**（`permissions.deny` / `permissions.allow`）中。权限规则以 deny-first 顺序评估（详见第 7 章和第 20 章），它们提供了确定性的执行层。论文明确指出：

> "Permission rules evaluated in deny-first order provide the deterministic enforcement layer. This creates a deliberate separation between guidance (CLAUDE.md, probabilistic) and enforcement (permission rules, deterministic)."

这是一个**故意设计的分离**，不是 bug：

| | CLAUDE.md 指令 | 权限规则 |
|--|---------------|---------|
| 性质 | Guidance（引导） | Enforcement（强制执行） |
| 可靠性 | 概率性 | 确定性 |
| 适用范围 | "请用 2 空格缩进" | "永远禁止 force push" |
| 失效模式 | 模型可能忽略 | 权限系统硬拦截 |

**对你的意义**：
- 如果某件事"建议这样做更好"——写进 CLAUDE.md
- 如果某件事"绝对不能做"——写进 `permissions.deny` 规则、PreToolUse Hook、或 GitHub 分支保护规则
- 不要把 CLAUDE.md 当成安全机制来用——它不是为此设计的

### CLAUDE.md 写什么、不写什么

**应该写的内容**：
- 技术栈和版本（"使用 TypeScript 5.0 + React 18"）
- 与默认不同的代码风格（"使用 2 空格缩进""使用单引号"）
- 构建、测试、lint 命令（"运行 `npm test` 而非 `npm run test:unit`"）
- 仓库礼仪（PR 模板、commit message 格式）
- 架构决策（"使用 CQRS 模式""不要直接操作数据库，走 Repository"）
- 环境怪癖（"Windows 下需要用 cross-env"）
- 常见陷阱（"不要修改 legacy/ 目录下的文件"）

**不应该写的内容**：
- 标准语言约定（Claude 已经知道）
- 详细 API 文档（会变，导致 Doc Rot）
- 经常变化的信息
- 长解释（浪费上下文）

### 长度控制

**目标 < 200 行**。过长会降低遵守度——Claude 会忽略或遗忘尾部的指令。

如果超过 200 行，拆分为 `.claude/rules/` 下的路径范围规则（见 5.4 节）。

### 自动生成

```bash
/init
```

Claude 会分析项目结构，自动生成初版 CLAUDE.md。之后随时间精化。

### 导入其他文件

CLAUDE.md 中可以用 `@文件名` 导入外部文件内容：

```markdown
@README.md
@package.json
@docs/git-instructions.md
@~/.claude/my-project-instructions.md
```

- 最大递归深度 4
- 首次遇到外部导入需用户批准

### 排除无关文件

如果项目中有多个团队的 CLAUDE.md（如 monorepo），可用 `claudeMdExcludes` 排除：

```json
{
  "claudeMdExcludes": [
    "**/monorepo/CLAUDE.md",
    "/home/user/monorepo/other-team/.claude/rules/**"
  ]
}
```

### CLAUDE.md vs 自动记忆

| | CLAUDE.md | 自动记忆 |
|--|-----------|----------|
| **谁编写** | 你 | Claude |
| **内容** | 指令和规则 | 学习和模式 |
| **范围** | 项目/用户/组织 | 每个工作树，跨 worktrees 共享 |
| **加载** | 每会话完整加载 | 每会话前 200 行或 25KB |
| **用途** | 编码标准、工作流、架构 | 构建命令、调试见解、偏好 |

### 编写有效指令的要点

- **结构化**：用 markdown 标题 + 项目符号分组
- **具体可验证**："使用 2 空格缩进" 而非 "正确格式化"
- **一致性**：定期检查删除冲突/过时指令
- **维护原则**：第二次纠正同一件事 → 写进 CLAUDE.md；已经正确执行的 → 删除或转 hooks

---

## 5.4 `.claude/rules/` 路径范围规则

当 CLAUDE.md 太长或规则只适用于特定目录时，拆分为 `.claude/rules/` 下的文件。

### 基本结构

```
.claude/
├── CLAUDE.md
└── rules/
    ├── code-style.md
    ├── testing.md
    └── security.md
```

### 路径范围（按需加载）

用 frontmatter 指定规则适用的文件路径：

```markdown
---
paths:
  - "src/api/**/*.ts"
---

# API 规则
- 所有端点必须输入验证
- 使用标准错误格式
- 返回 401 时包含 `www-authenticate` 头
```

Claude 只会在编辑匹配路径的文件时加载该规则，节省上下文。

### 路径模式

| 模式 | 匹配 |
|------|------|
| `**/*.ts` | 所有 TypeScript 文件 |
| `src/**/*` | `src/` 下所有文件 |
| `src/**/*.{ts,tsx}` | 多扩展名 |

支持符号链接跨项目共享规则。

---

## 5.5 `.claude/skills/`、`agents/`、`commands/`

这三个目录分别存放可复用的能力单元。第 5 章只讲位置和加载规则，详细用法见后续章节。

### Skills（`.claude/skills/`）

```
.claude/skills/
└── my-skill/
    ├── SKILL.md          # 必须
    └── support-files/    # 可选
```

- 通过 `/skill-name` 调用
- 支持 bundling（SKILL.md + 支持文件一起加载）
- 优于 Command（官方推荐）
- 全局 skills 在 `~/.claude/skills/`，项目级在 `./.claude/skills/`
- 优先级：企业 > 个人 > 项目 > 插件

### Agents（`.claude/agents/`）

```
.claude/agents/
└── reviewer/
    └── AGENT.md
```

- 子代理定义，用于隔离上下文处理专门任务
- 支持 frontmatter 控制权限、模型等
- 全局 agents 在 `~/.claude/agents/`，项目级在 `./.claude/agents/`

### Commands（`.claude/commands/`）

```
.claude/commands/
└── deploy.md
```

- 单文件轻量命令，旧机制
- 功能比 Skills 有限，建议优先用 Skills

### 加载优先级

同名 skills/agents/commands 的覆盖顺序：

```
企业级（托管）> 个人（~/.claude/）> 项目（./.claude/）> 插件
```

---

## 5.6 自动记忆系统

自动记忆是 Claude 自己编写的持久化学习记录，与你写的 CLAUDE.md 互补。

### 位置与结构

```
~/.claude/projects/<project>/memory/
├── MEMORY.md          # 索引文件
├── debugging.md       # 调试见解
├── build-commands.md  # 构建命令
└── preferences.md     # 你的偏好
```

### 工作原理

- **默认开启**，可通过 `/memory` 切换或设置 `autoMemoryEnabled: false`
- 每次停止边界（你停止输入、Claude 完成任务）触发提取
- 主题文件按需读取，启动时不加载全部
- 纯文本，可手动编辑删除

### 与 CLAUDE.md 的配合

| 放 CLAUDE.md | 放自动记忆 |
|-------------|-----------|
| 编码标准 | 构建命令 |
| 架构决策 | 调试技巧 |
| 代码风格 | 环境怪癖 |
| 仓库礼仪 | 你的个人偏好 |

---

## 5.7 `.worktreeinclude`

worktree 创建时会自动复制 `.gitignored` 文件，但你可以用 `.worktreeinclude` 额外指定需要复制的文件：

```
.env.local
config/private.yml
scripts/local-setup.sh
```

这样新 worktree 创建时，这些文件会被复制过去，避免重新配置。

---

## 5.8 Token 经济性与上下文维护

理解 `.claude/` 目录的加载机制，需要了解 Token 经济性。根据源码分析，上下文中的主要 token 消耗来源如下：

| 内容类型 | Token 占比 | 任务贡献度 | 说明 |
|----------|-----------|------------|------|
| 工具 schema | 30-60% | 高（必须） | MCP 工具 schema 质量参差不齐 |
| 旧 tool_result | 20-40% | 低（已过时效） | 长会话中累积 |
| Skill 描述 | 5-15% | 中（按需才有价值） | 全部 skill 索引常驻 |
| Git status | 1-5% | 中（项目感知） | session 级快照不随文件变化更新 |
| 已读文件缓存 | 5-10% | 高（避免重复读取） | LRU 100 条限制可能导致频繁替换 |

CLAUDE.md 通常只占 1K-10K tokens，但它位于用户上下文（user context）中，与系统提示（10K-20K tokens）和工具 schema（15K-30K tokens）共享有限的上下文窗口。这就是为什么：

- **CLAUDE.md 要精简**——每多一行都在挤占对话历史的空间
- **rules 要按需加载**——路径范围规则只在需要时注入，不常驻
- **Skill 索引要控制**——全部 skill 描述常驻上下文，项目级 skill 应精简 frontmatter

Skill 加载采用三级策略：

| 级别 | 加载时机 | 内容 |
|------|----------|------|
| Tier 1 | 每次 query 前 | 核心 skill（Bash、Read、Edit 等） |
| Tier 2 | 按需加载 | 项目特定 skill（根据文件类型推断） |
| Tier 3 | 运行时发现 | MCP 工具、动态加载的 skill |

上下文维护采用四层防御体系：

| 层级 | 触发时机 | 策略 | 是否 lossy |
|------|----------|------|-----------|
| Microcompact | 单轮工具结果过多 | 删除已消费的 tool result | 否 |
| Cached Microcompact | 同上，利用 `cache_edits` API | 删除不 invalidate 缓存前缀 | 否 |
| Snip | 消息体过大 | 截断长文本，保留前后文 | 是 |
| Autocompact | Token 接近阈值（200K - 13K buffer） | 保留最近 4 轮，摘要更早历史 | 是 |
| Context Collapse | API 返回 prompt_too_long | 应急压缩，每轮限 1 次 | 是 |

**对你的配置意义**：
- 把最重要的指令放在 CLAUDE.md 前 50 行——compact 后重新注入时，前面的内容更容易被保留
- 子目录规则文件在 compact 后需要重新读取——如果关键规则只在子目录 rules 中，compact 后可能暂时失效
- 控制 skill 数量——每个 skill 的 frontmatter description 都会占用常驻上下文

---

## 5.9 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| Claude 不遵循 CLAUDE.md | 未加载 / 指令冲突 / 太抽象 | `/memory` 验证加载；检查文件位置；使指令更具体；查找冲突 |
| 指令必须在特定时刻运行 | CLAUDE.md 是静态加载 | 改用 hooks（PreToolUse / PostToolUse） |
| 需要系统提示级别的指令 | CLAUDE.md 在上下文后加载 | 使用 `--append-system-prompt` |
| CLAUDE.md 太大 | 超过 200 行 | 用路径范围规则按需加载；修剪非必要内容 |
| `/compact` 后指令丢失 | 压缩只保留对话摘要 | 根目录 CLAUDE.md 自动重新注入；子目录规则文件需重新读取 |
