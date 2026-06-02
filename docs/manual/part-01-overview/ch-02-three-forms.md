# 第2章 Claude Code 的使用形态

Claude Code 不是"打开一个聊天窗口"那么简单。它设计了**九种入口形态**，覆盖从日常编码到后台自动化的全场景。本章先给你一张全景地图，然后深入每种形态的具体用法。

---

## 2.1 九种入口全景

### 一张表看懂所有用法

| 入口形态 | 命令示例 | 适用场景 | 本章覆盖 |
|---------|---------|---------|---------|
| **REPL 交互会话** | `claude` | 持续对话，日常开发主力 | **2.2 重点** |
| **权限模式选择** | `claude --permission-mode plan` | 控制编辑/命令的审批粒度 | **2.3 重点** |
| **后台运行** | `claude --bg "run tests"` | 7×24 自动化 | **2.4 重点** |
| **管道模式** | `git diff \| claude -p "review"` | Unix 哲学，数据流驱动 | **2.5 重点** |
| **Headless / SDK** | `claude -p "task" --output-format json` | CI/CD、脚本集成 | **2.6 重点** |
| **`--bare` 快速模式** | `claude --bare -p "quick"` | 跳过初始化，极速启动 | 2.6 |
| **跨设备会话** | `claude --remote / --teleport` | 手机/浏览器/终端流转 | **2.7 重点** |
| **定时任务** | `/schedule` / `/loop` / Routines | 周期性自动化 | **2.7 重点** |
| **MCP Server** | `claude mcp serve` | 作为服务端暴露能力 | 第15章 |
| **脚本模式** | `claude script.js` | 预定义任务模板执行 | 2.8 |

### 快路径 vs 主路径

Claude Code 的启动做了**两级分流**：

**第一级：快路径（零延迟）**

以下命令在毫秒级响应，不加载完整应用：

| 命令 | 响应时间 | 用途 |
|------|---------|------|
| `claude --version` | **< 50ms** | 查看版本 |
| `claude daemon` / `ps` / `logs` / `attach` / `kill` | **< 100ms** | 守护进程管理 |
| `claude --bg "task"` | **< 100ms** | 提交后台任务 |
| `claude remote` / `bridge` / `sync` | **< 100ms** | 远程控制 |

**第二级：主路径（完整启动）**

普通 `claude` 命令走完整启动流程，但做了**并行预热优化**：

```
传统 CLI 启动：加载模型(500ms) → 读取 Keychain(200ms) → 可输入(800ms)
Claude Code 启动：加载模型(500ms) ════╦═══→ 读取 Keychain(200ms) 并行
                                    ║                              可输入(600ms) ↓ 快200ms
```

**对用户的意义**：日常 `claude` 启动后不到半秒就能输入，体验流畅。

### 交互式 vs Headless 分叉

启动完成后，Claude Code 根据是否有终端交互能力，分叉为两条路径：

- **交互式（Interactive）**：有终端 → 启动 React/Ink UI，支持权限弹窗、流式输出、虚拟滚动
- **Headless（无头）**：无终端 / `-p` 模式 → 直接输出文本结果，无 UI

---

## 2.2 交互式 REPL（日常主力）

这是最常用的形态。打开终端，输入 `claude`，进入一个持续对话的会话。

### 基本交互

```bash
$ claude
> 帮我看看这个 bug
  [Claude 开始分析代码...]
> 用 Vue 重写这个组件
  [Claude 编辑文件...]
> /clear
  [上下文已清空，继续新任务]
```

### 标准工作流：探索 → 规划 → 编码 → 提交

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  探索    │ →│  规划    │ →│  编码    │ →│  提交    │
│          │  │          │  │          │  │          │
│ Plan Mode│  │ 详细方案 │  │ 实现+验证│  │ PR       │
│ 只读不写 │  │ Ctrl+G编辑│  │ 退出Plan │  │ 描述性消息│
└──────────┘  └──────────┘  └──────────┘  └──────────┘
```

**跳过规划的情况**：范围明确的小修复（拼写错误、重命名、加日志）——直接说需求即可。

### 会话管理核心技巧

| 操作 | 命令/快捷键 | 效果 |
|------|-----------|------|
| 中途停止 | `Esc` | 保留上下文，随时继续 |
| 彻底重置 | `/clear` | 清空上下文，旧对话仍保存可恢复 |
| 压缩历史 | `/compact` | 摘要化历史，腾出上下文空间 |
| 快速提问 | `/btw "question"` | 问个问题，不进入对话历史 |
| 回退状态 | `/rewind` | 恢复到某次提示前的代码/对话状态 |
| 撤销更改 | `"撤销那个"` | 恢复刚才的文件修改 |
| 命名会话 | `/rename feature-auth` | 方便后续识别 |
| 启动命名 | `claude -n "name"` | 启动时直接命名 |
| 分支会话 | `claude --continue --fork-session` | 复制当前会话，尝试不同方案 |
| 从 PR 恢复 | `claude --from-pr 123` | 恢复与该 PR 关联的会话 |
| 不保存会话 | `claude -p --no-session-persistence` | 不写入磁盘，不可恢复 |

**黄金法则**：同一问题纠正 2 次以上 → `/clear` 并用更好的提示重新开始。不要在一个已经跑偏的上下文里死磕。

#### 会话选择器

运行 `/resume` 或 `claude --resume` 打开交互式会话选择器：

| 快捷键 | 动作 |
|--------|------|
| `↑`/`↓` | 导航 |
| `Enter` | 恢复选中会话 |
| `Space` | 预览会话内容 |
| `Ctrl+R` | 重命名 |
| `/` 或输入字符 | 搜索过滤 |
| `Ctrl+A` | 显示**所有项目**的会话 |
| `Ctrl+W` | 显示当前仓库**所有 worktree** 的会话 |
| `Ctrl+B` | 过滤当前 git 分支的会话 |

### Worktree 隔离并行

在同一个仓库里开多个并行会话，互不干扰：

```bash
# 为功能分支创建隔离会话
claude --worktree feature-auth

# 自动生成名称（如 bright-running-fox）
claude --worktree

# 从 PR 分支创建
claude --worktree "#1234"
```

Worktree 默认创建于 `.claude/worktrees/<name>/`，分支名为 `worktree-<name>`。完成且无更改时自动删除。

### Writer / Reviewer 双会话模式

在 REPL 内实现"写审分离"：

```
会话 A（Writer）
  > 实现用户认证功能
  [Claude 编码...]

会话 B（Reviewer，全新上下文）
  > 审查会话 A 的更改，对照 PLAN.md 检查需求覆盖
  [Claude 审查...发现3个问题]

会话 A（Writer）
  > 根据审查反馈修复问题 1/2/3
```

**为什么分两个会话？** 同一 AI 审查自己的代码时处于"上下文已消耗"的 Dumb Zone。用全新上下文审查，判断更准确。

---

## 2.3 权限模式选择（控制审批粒度）

Claude Code 在执行文件编辑、运行命令、网络请求前会暂停并请求批准。权限模式控制这个暂停的频率——从"每次都问"到"完全自动"。

### 六种权限模式

| 模式 | 命令 | 自动批准范围 | 最佳场景 |
|------|------|-------------|---------|
| `default` | `claude`（默认） | 只读操作 | 新手入门、敏感工作 |
| `acceptEdits` | `claude --permission-mode acceptEdits` | 读 + 文件编辑 + 常见文件系统命令（mkdir/touch/mv/cp/rm/sed等） | 迭代编码、事后用 git diff 审查 |
| `plan` | `claude --permission-mode plan` | 只读（不编辑，先规划后执行） | 探索代码库、先规划再动手 |
| `auto` | `claude --permission-mode auto` | 所有操作，经分类器后台安全检查 | 长任务、减少打扰 |
| `dontAsk` | `claude --permission-mode dontAsk` | 仅预批准的工具（`permissions.allow` 规则） | CI/CD、锁定环境 |
| `bypassPermissions` | `claude --dangerously-skip-permissions` | 所有操作，无检查 | **仅**隔离容器/VM |

> **安全提示**：`bypassPermissions` **拒绝在 root/sudo 下启动**。仅在无网络访问的隔离环境中使用。`auto` 模式需要 Anthropic 订阅、指定模型和管理员启用。

### 模式行为差异

- **default**：最安全，每次编辑/命令都弹窗确认
- **acceptEdits**：自动批准工作目录内的文件编辑和常见文件操作，其他命令仍弹窗
- **plan**：Claude 只读不写，先出方案供你审查（可用 `Ctrl+G` 在编辑器中修改方案）
- **auto**：分类器审查每个操作，阻止危险行为（curl \| bash、生产部署、IAM 修改等），正常操作自动通过
- **dontAsk**：未预批准的操作**直接拒绝**而非弹窗，适合无人工值守的 CI
- **bypassPermissions**：完全无拦截，包括受保护路径也可写

### 切换方式

**会话中切换**：按 `Shift+Tab` 循环 `default` → `acceptEdits` → `plan`。可选模式（auto、bypassPermissions）启用后插在 `plan` 之后。

**启动时指定**：
```bash
claude --permission-mode plan
claude --permission-mode acceptEdits
claude --dangerously-skip-permissions     # 等同于 bypassPermissions
```

**持久默认**：`~/.claude/settings.json` 中设置：
```json
{
  "permissions": {
    "defaultMode": "acceptEdits"
  }
}
```

### 受保护路径（所有模式都保护，除 bypassPermissions）

以下路径的写入永不自动批准，防止意外破坏：

| 类型 | 路径 |
|------|------|
| 目录 | `.git`、`.vscode`、`.idea`、`.husky`、`.claude`（除 commands/agents/skills/worktrees 子目录） |
| 文件 | `.gitconfig`、`.bashrc`、`.zshrc`、`.profile`、`.mcp.json`、`.claude.json` |

### 权限相关的 CLI flags

| Flag | 说明 | 示例 |
|------|------|------|
| `--permission-mode <mode>` | 启动时指定权限模式 | `claude --permission-mode plan` |
| `--dangerously-skip-permissions` | 完全跳过权限检查 | `claude --dangerously-skip-permissions` |
| `--allow-dangerously-skip-permissions` | 把 bypassPermissions 加入 Shift+Tab 循环，但不激活 | `claude --permission-mode plan --allow-dangerously-skip-permissions` |
| `--allowedTools` | 预批准特定工具 | `claude --allowedTools "Read,Edit,Bash"` |
| `--disallowedTools` | 显式拒绝特定工具 | `claude --disallowedTools "Bash(rm *)"` |

---

## 2.4 后台运行（7×24 自动化）

把验证过的流程变成后台任务，主会话继续干别的。

### 启动后台任务

```bash
# 简单后台任务
claude --bg "investigate the flaky test"

# 指定 Agent 类型的后台任务
claude --agent code-reviewer --bg "address review comments on PR 1234"

# 批量启动（一次十几个）
claude --bg "refactor auth module"
claude --bg "update dependencies"
claude --bg "write tests for utils"
```

### Agent View 管理后台会话

```bash
claude agents
```

打开可视化后台会话管理界面，可以看到：
- 每个后台任务的进度
- 任务输出摘要
- 失败/成功状态
- 随时切换前台继续交互

### Agent Teams 自动协调

多个后台会话自动协作，无需人工调度：
- **Team Lead**：分配任务
- **Teammate**：执行子任务（在独立 worktree 中运行）
- **Coordinator**：汇总结果

通信通过**文件系统邮箱**完成——零外部依赖，跨进程持久化。

### 后台任务的典型场景

| 场景 | 命令示例 |
|------|---------|
| 夜间批量重构 | `claude --bg "refactor all class components to hooks"` |
| 持续审查 | `claude --agent code-reviewer --bg "monitor PRs"` |
| 文档同步 | `claude --bg "update API docs from source"` |
| 测试修复 | `claude --bg "fix failing tests on main"` |

---

## 2.5 管道模式（Unix 哲学）

Claude Code 被设计成命令行管道中的一个智能节点。

### 基本用法

```bash
# 日志异常检测
tail -200 app.log | claude -p "Slack me if you see any anomalies"

# 生成 PR 描述
git diff main | claude -p "write a PR description"

# 安全审查
git diff main -- src/auth src/billing | claude -p "review for security risks"

# 错误分析
cat error.log | claude -p "diagnose the root cause and suggest fixes"
```

### 跨文件批量处理

```bash
# 从文件列表批量迁移
for file in $(cat files.txt); do
  claude -p "Migrate $file from React to Vue" \
    --allowedTools "Edit,Bash(git commit *)"
done
```

### 管道模式的特点

- **数据进，结果出**：stdin 作为上下文输入，stdout 作为结果输出
- **非交互**：无需人工确认，适合脚本
- **可组合**：可以放在任何 Unix 管道中间节点

---

## 2.6 Headless / SDK / `--bare`（自动化与集成）

### 非交互模式（`-p`）

```bash
# 基础用法
claude -p "List all API endpoints"

# JSON 输出（方便程序解析）
claude -p "List API endpoints" --output-format json

# 流式 JSON（实时获取进度）
claude -p "Analyze log" --output-format stream-json --verbose
```

### `--bare` 快速模式

跳过 hooks、skills、plugins、MCP servers、auto memory、CLAUDE.md、OAuth、keychain 读取，极速启动：

```bash
claude --bare -p "quick question"
```

适用场景：CI 脚本、需要跨机器一致结果、不需要项目上下文的快速任务。

**Bare 模式下显式加载上下文**：

| 要加载的内容 | 使用的 flag |
|-------------|------------|
| 系统提示补充 | `--append-system-prompt` 或 `--append-system-prompt-file` |
| 设置文件 | `--settings <file-or-json>` |
| MCP 服务器 | `--mcp-config <file-or-json>` |
| 自定义 Agent | `--agents <json>` |
| 插件 | `--plugin-dir <path>` 或 `--plugin-url <url>` |

### 结构化输出（JSON Schema）

用 `--json-schema` 让输出符合特定结构，方便程序解析：

```bash
# 提取函数名，返回数组
claude -p "Extract main function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \
  | jq '.structured_output'
```

### 流式输出

实时获取生成中的 token，适合长任务监控：

```bash
claude -p "Write a poem" \
  --output-format stream-json \
  --verbose \
  --include-partial-messages | \
  jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text'
```

流式事件类型：

| 事件 | 说明 |
|------|------|
| `system/init` | 会话元数据（模型、工具、MCP、插件） |
| `system/api_retry` | API 重试事件（attempt、max_retries、retry_delay_ms） |
| `system/plugin_install` | 插件安装进度 |
| `stream_event` + `text_delta` | 生成的文本 token |

### 预算与回合控制

| Flag | 说明 | 示例 |
|------|------|------|
| `--max-budget-usd` | API 花费上限（美元），超出即停止 | `claude -p --max-budget-usd 5.00 "task"` |
| `--max-turns` | 最大交互轮数，超出报错退出 | `claude -p --max-turns 3 "task"` |

### 继续对话

```bash
# 第一次请求
claude -p "Review this codebase for performance issues"

# 继续同一会话
claude -p "Now focus on the database queries" --continue

# 获取 session ID 后精确恢复
session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
claude -p "Continue that review" --resume "$session_id"
```

### 权限与工具控制（Headless 中）

```bash
# 预批准工具（不弹窗）
claude -p "Run tests and fix failures" --allowedTools "Bash,Read,Edit"

# 配合权限模式
claude -p "Apply lint fixes" --permission-mode acceptEdits

# 限制可用工具集
claude -p "task" --tools "Bash,Edit,Read"
```

`--allowedTools` 使用权限规则语法，支持前缀匹配：`Bash(git diff *)` 允许任何以 `git diff` 开头的命令（` *` 前的空格很重要）。

### SDK 嵌入程序

将 Claude Code 作为库嵌入 Python/TypeScript 程序：

```python
# 伪代码示例
from claude_code import Agent

agent = Agent(workspace="./my-project")
result = agent.run("Refactor auth module")
print(result.summary)
```

适用场景：CI/CD 自动分析、飞书/钉钉机器人、代码审查平台集成、定时扫描任务。

> **优先级建议**：先学会 CLI 交互 → 再学 Headless 脚本 → 最后考虑 SDK 嵌入。

---

## 2.7 跨设备会话与定时任务

### 跨设备会话流转

Claude Code 的会话不绑定到单一设备，可以在终端、浏览器、手机之间切换：

| 命令 | 功能 | 场景 |
|------|------|------|
| `claude --remote "Fix the login bug"` | 在云端（claude.ai）创建新会话 | 本地没环境，或要跑长任务 |
| `claude --teleport` | 把 web/手机上的会话拉回到本地终端 | 在手机上启动，回电脑前继续 |
| `claude --remote-control "My Project"` | 启动可被手机/浏览器远程控制的本地会话 | 离开座位，用手机继续操控 |
| `/desktop` | 把当前终端会话传送到 Desktop app | 需要可视化 diff 审查 |

**Remote Control 服务器**：
```bash
claude remote-control --name "My Project"
```
在服务器模式下运行（无本地交互），等待从 claude.ai 或 Claude app 连接控制。

### 定时任务

Claude Code 支持三种定时执行方式：

**1. Routines（云端定时任务）**
- 在 Anthropic 托管的基础设施上运行
- 电脑关机也继续执行
- 可触发于：定时 cron、API 调用、GitHub 事件
- 创建方式：web 界面、Desktop app、或 CLI 中运行 `/schedule`

**2. Desktop 定时任务**
- 在本地机器上运行
- 直接访问本地文件和工具
- 适合需要本地环境的任务

**3. `/loop`（CLI 内轮询）**
- 在当前 CLI 会话中重复执行某个提示
- 适合快速轮询检查（如每 5 分钟检查部署状态）
- 可自设间隔，或让模型自适应 pacing

```bash
# 示例：每 5 分钟检查 CI 状态
/loop 5m check if the CI pipeline on PR #123 has finished

# 自适应间隔（模型自己决定何时再检查）
/loop check for new PRs and review them
```

---

## 2.8 其他入口（简要说明）

| 入口 | 一句话说明 | 详见 |
|------|-----------|------|
| **MCP Server** | `claude mcp serve` —— 让其他程序通过 MCP 协议调用 Claude Code 的能力 | 第15章 |
| **脚本模式** | 执行预定义的任务模板 | 官方文档 |

---

## 2.9 如何选择使用形态？

| 你的场景 | 推荐形态 | 命令 |
|---------|---------|------|
| 日常开发，边想边做 | **REPL 交互** | `claude` |
| 探索代码库，先规划再编码 | **Plan 模式** | `claude --permission-mode plan` |
| 长任务，减少频繁确认 | **Auto 模式** | `claude --permission-mode auto` |
| 大任务后台跑，不影响主会话 | **后台运行** | `claude --bg "task"` |
| 处理日志/diff/命令输出 | **管道模式** | `... \| claude -p "..."` |
| CI/CD、自动化脚本 | **Headless / SDK** | `claude -p "..." --output-format json` |
| 快速提问，不需要项目上下文 | **`--bare` 模式** | `claude --bare -p "..."` |
| 需要审查自己的代码 | **REPL + 双会话** | 开两个 `claude` 会话 |
| 同一仓库多个并行任务 | **REPL + Worktree** | `claude --worktree` |
| 手机启动，电脑继续 | **跨设备** | `claude --teleport` |
| 定时重复任务 | **定时任务** | `/schedule` 或 Routines |
