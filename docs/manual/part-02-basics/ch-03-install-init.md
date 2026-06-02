# 第3章 安装与初始化

Claude Code 的安装很简单，但首次启动时的几个选择会影响你接下来几周的使用体验。本章带你完成从安装到跑通第一个任务的完整流程，并给出新手的配置建议。

---

## 3.1 安装方式

### 推荐安装：原生安装脚本

**macOS、Linux、WSL：**

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

原生安装会自动在后台更新，无需手动干预。

**Windows PowerShell：**

```powershell
irm https://claude.ai/install.ps1 | iex
```

**Windows CMD：**

```batch
curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd
```

> **注意**：如果看到 `The token '&&' is not a valid statement separator`，说明你当前在 PowerShell 里。如果看到 `'irm' is not recognized`，说明你在 CMD 里。Windows 终端的提示符 `PS C:/` 表示 PowerShell，不带 `PS` 的是 CMD。

Windows 上建议安装 [Git for Windows](https://git-scm.com/downloads/win)，这样 Claude Code 可以使用 Bash 工具。如果没有安装，Claude Code 会回退到 PowerShell。

### 包管理器安装

| 方式 | 命令 | 更新方式 |
|------|------|---------|
| **Homebrew** | `brew install --cask claude-code` | 手动 `brew upgrade` |
| **WinGet** | `winget install Anthropic.ClaudeCode` | 手动 `winget upgrade` |

> **差异**：`claude-code` 跟踪稳定版（约晚一周，跳过有严重回归的版本），`claude-code@latest` 跟踪最新版（发布后即刻更新）。包管理器安装**不会自动更新**。

### Linux 包管理器

Debian、Fedora、RHEL、Alpine 也支持 `apt`、`dnf`、`apk` 安装，详见官方文档。

---

## 3.2 首次启动

安装完成后，在任意项目目录启动：

```bash
cd your-project
claude
```

第一次使用会提示登录。Claude Code 支持两种认证方式：

| 方式 | 适用场景 | 命令 |
|------|---------|------|
| **Claude 订阅** | 个人日常使用 | 浏览器 OAuth 登录 |
| **Anthropic Console** | API 用量计费、企业环境 | `claude auth login --console` |

验证登录状态：

```bash
claude auth status        # JSON 输出
claude auth status --text  # 人类可读格式
```

退出登录：

```bash
claude auth logout
```

### 生成长期 Token（CI/脚本用）

```bash
claude setup-token
```

打印一个长期有效的 OAuth token，不会保存到本地。适合 CI/CD 或自动化脚本，需要 Claude 订阅。

---

## 3.3 新手权限模式选择指南

Claude Code 有六种权限模式（详见第 2 章 2.3 节），新手不需要全部了解。按使用阶段选择即可：

| 阶段 | 推荐模式 | 启动命令 | 为什么 |
|------|---------|---------|--------|
| **第 1 天** | `default` | `claude` | 每次编辑/命令都确认，建立对工具的信任 |
| **一周后** | `acceptEdits` | `claude --permission-mode acceptEdits` | 自动批准文件编辑，事后用 `git diff` 审查 |
| **探索新仓库** | `plan` | `claude --permission-mode plan` | 先出方案，你审查后再执行 |
| **跑脚本/CI** | `dontAsk` | `claude --permission-mode dontAsk` | 只执行预批准的工具，不弹窗 |

> **不要一开始就开 `auto` 或 `bypassPermissions`**。`auto` 需要特定模型和订阅条件，`bypassPermissions` 只在隔离容器中使用。

**一个帮你理解权限模式的数据**：Anthropic 官方报告，引入沙箱能力后，Claude Code 的权限弹窗减少了 **84%**，同时保持了安全性。沙箱定义了一个有界区域，在此区域内 Agent 被授权自由行动——把权限从"每次操作都要问"变成了"会话开始时配置一次"。

自动批准和沙箱不是让你"更不安全"，而是让你少做无意义的重复决策。有研究（Hughes, 2026）表明用户批准了约 **93%** 的权限弹窗——这意味着大多数弹窗不是真正在"保护"你，而是在浪费你的注意力。正确的策略是：用沙箱划定安全边界，在边界内授予 AI 最大自由。

**切换方式**：
- 会话中：按 `Shift+Tab` 循环切换（`default` → `acceptEdits` → `plan`）
- 持久默认：`~/.claude/settings.json` 中设置 `permissions.defaultMode`

---

## 3.4 基础个性化配置

### 模型选择

```bash
claude --model sonnet    # 默认，平衡速度与质量
claude --model opus      # 最强推理，攻坚复杂问题
```

也可以在 `~/.claude/settings.json` 中设置默认模型：

```json
{
  "model": "sonnet"
}
```

### 配置文件位置与优先级

Claude Code 的配置按以下优先级读取（后覆盖前）：

1. **全局设置**：`~/.claude/settings.json`
2. **项目设置**：`your-project/.claude/settings.json`
3. **本地设置**：`your-project/.claude/settings.local.json`（不入 git）
4. **CLI flags**：`claude --model opus` 等命令行参数

> **建议**：个人偏好放全局，项目规范放项目目录，敏感信息（如 API key）放本地设置。

### 常用设置项

```json
{
  "model": "sonnet",
  "permissions": {
    "defaultMode": "acceptEdits"
  },
  "theme": "dark",
  "viewMode": "detailed"
}
```

完整设置项参考见第 5 章和官方 Settings 文档。

---

## 3.5 推荐 CLI 工具链

Claude Code 不是孤岛，它与成熟的 CLI 工具配合效果更好。以下工具是官方和实战社区高频推荐的组合：

| 工具 | 作用 | 与 Claude Code 的配合 |
|------|------|---------------------|
| **fzf** | 模糊搜索文件/历史 | Claude 读取的文件列表可用 fzf 快速过滤 |
| **ripgrep (rg)** | 极速代码搜索 | Claude Code 内部也用 rg，你也可以在对话中让它用 |
| **fd** | 人性化文件查找 | 替代 `find`，语法更直观 |
| **bat** | 带语法高亮的文件查看 | 预览 Claude 编辑的文件 |
| **jq** | JSON 解析 | 解析 Claude Headless 输出的 JSON |
| **git** | 版本控制 | 必备，Claude Code 深度集成 git |
| **tig** / **lazygit** | 交互式 git 浏览 | 审查 Claude 生成的 commit |
| **delta** | 语法高亮的 diff | 审查 Claude 的文件修改 |

**安装示例（macOS + Homebrew）：**

```bash
brew install fzf ripgrep fd bat jq git lazygit delta
```

---

## 3.6 验证安装

跑一个端到端的小任务，确认安装和认证都正常：

```bash
# 基础交互测试
claude -p "What files are in this directory?"

# JSON 输出测试
claude -p "List the top 3 largest files" --output-format json | jq '.result'

# 文件编辑测试（acceptEdits 模式）
claude --permission-mode acceptEdits -p "Create a hello.txt with 'Hello Claude Code'"

# 验证文件确实被创建了
cat hello.txt
```

如果以上命令都能正常执行并得到预期结果，说明安装完成。

---

## 3.7 常见安装问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `claude: command not found` | 安装路径未加入 PATH | 重新打开终端，或手动将安装目录加入 PATH |
| Windows 下 Bash 工具不可用 | 未安装 Git for Windows | 安装 Git for Windows，或接受 PowerShell 回退 |
| 登录后仍提示未认证 | 会话过期或网络问题 | `claude auth logout && claude auth login` |
| 代理/防火墙阻断 | 企业网络限制 | 配置系统代理或联系网络管理员 |
| 安装脚本权限 denied | 当前用户无写入权限 | 检查 `~/.local/bin` 或 `/usr/local/bin` 权限 |
