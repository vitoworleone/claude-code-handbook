<p align="center">
  <img src="docs/assets/readme-banner-1280x640.png" alt="claude-code-handbook" width="100%">
</p>

<p align="center">
  <a href="./docs/"><img src="https://img.shields.io/badge/docs-Markdown-lightgrey" alt="Docs"></a>
  <a href="./mewcode/"><img src="https://img.shields.io/badge/python-3.13-blue" alt="Python"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow" alt="License"></a>
</p>

---

## 一句话定位

**把 Claude Code 从"会用"推到"造得出"的实战知识库。**

不是官方文档的复读，也不是学术论文的堆砌。这里有三样东西：

1. **使用手册** — 让你把 Claude Code 用得更稳、更快、更省心
2. **实践配方** — 直接可用的 Skill、Agent 示例和配置模板
3. **Agent Runtime 工坊** — 手把手从零实现一个 Claude Code 风格的 Coding Agent

---

## 适合谁

- 👨‍💻 **个人开发者**：想建立一套稳定的 AI 辅助开发工作流
- 🏗️ **AI 全栈工程师**：需要把 Claude Code 接入复杂项目和企业流程
- 🚀 **团队负责人**：想参考小米等组织的 AI Coding 工程化实践
- 🔧 **Agent builders**：计划深入 Agent Runtime，从使用者走向建造者

---

## 四大板块

### 📘 使用手册

8 部分 24 章，从安装配置到高级技巧。

- 核心工作流（刘小排 / Matt Pocock / 徐文浩）
- 小米 AI Coding 工程化实践
- 记忆、上下文、Skills、Hooks、MCP
- 权限、隔离、失败模式

→ [进入手册](./docs/manual/)

---

### 🧰 实践配方

可直接用的代码、配置和模板。

- Agent 示例代码
- SKILL.md 模板（agent-builder / code-review / mcp-builder / pdf）
- Claude Code 中英学习文档
- 主流 Agent Harness 对照参考

→ [查看配方](./docs/recipes/)

---

### 🛠️ Agent Runtime 工坊

10 章教程 + Python 参考实现。

- 从 LLM 客户端到 Agent 主循环
- 工具系统、权限、上下文、记忆
- Skill、SubAgent、Agent Teams

→ [开始工坊](./docs/workshop/)

---

### 🏗️ Harness 笔记

Agent Harness 架构学习与整理。

- OpenCode / Codex / DeerFlow 对照
- Claude Code Runtime 机制拆解
- Harness 设计模式总结

→ [查看笔记](./harness/)

---

## 📚 内容地图

### 使用手册

| 部分 | 内容 |
|------|------|
| **part-01-overview** | [它到底是什么](./docs/manual/part-01-overview/ch-01-what-is-it.md)、[三种使用形态](./docs/manual/part-01-overview/ch-02-three-forms.md) |
| **part-02-basics** | [安装与初始化](./docs/manual/part-02-basics/ch-03-install-init.md)、[会话管理](./docs/manual/part-02-basics/ch-04-session-mgmt.md)、[项目级配置](./docs/manual/part-02-basics/ch-05-project-config.md) |
| **part-03-workflow** | [需求驱动工作流](./docs/manual/part-03-workflow/ch-06-demand-workflow.md)、[安全开发闭环](./docs/manual/part-03-workflow/ch-07-security-closure.md)、[开发节奏](./docs/manual/part-03-workflow/ch-08-development-rhythm.md)、[小米实践](./docs/manual/part-03-workflow/ch-09-xiaomi-practice.md) |
| **part-04-context** | [记忆系统](./docs/manual/part-04-context/ch-10-memory-system.md)、[上下文窗口](./docs/manual/part-04-context/ch-11-context-window.md)、[上下文质量](./docs/manual/part-04-context/ch-12-context-quality.md) |
| **part-05-extension** | [Skills](./docs/manual/part-05-extension/ch-13-skills.md)、[Hooks](./docs/manual/part-05-extension/ch-14-hooks.md)、[MCP](./docs/manual/part-05-extension/ch-15-mcp-integration.md)、[插件](./docs/manual/part-05-extension/ch-16-plugins.md) |
| **part-06-parallel** | [多任务](./docs/manual/part-06-parallel/ch-17-multi-task.md)、[Worktree](./docs/manual/part-06-parallel/ch-18-worktree.md)、[流水线](./docs/manual/part-06-parallel/ch-19-pipeline.md) |
| **part-07-security** | [权限与审批](./docs/manual/part-07-security/ch-20-permissions.md)、[隔离与防护](./docs/manual/part-07-security/ch-21-isolation.md) |
| **part-08-advanced** | [输出风格](./docs/manual/part-08-advanced/ch-22-output-styles.md)、[自动化](./docs/manual/part-08-advanced/ch-23-automation.md)、[失败模式](./docs/manual/part-08-advanced/ch-24-failure-modes.md) |

### 实践配方

| 配方 | 说明 |
|------|------|
| **[agents](./docs/recipes/agents/)** | Agent loop、tool use、subagent、skill loading、context compact、task system、background tasks、agent teams 等示例代码 |
| **[skills](./docs/recipes/skills/)** | 可直接复用的 SKILL.md 模板：agent-builder、code-review、mcp-builder、pdf |
| **[docs](./docs/recipes/docs/)** | Claude Code 中英学习文档（en / zh） |
| **[source-analysis](./docs/recipes/source-analysis/)** | 主流 Agent Harness（OpenCode / Codex / DeerFlow）设计对照参考 |

### Agent Runtime 工坊

| 章节 | 内容 | 理论学习 | Python 实现 | 实战演练 |
|------|------|----------|-------------|----------|
| ch-01 | 初识 Coding Agent | [theory](./docs/workshop/chapters/ch-01-intro-to-coding-agent/theory.md) | [python](./docs/workshop/chapters/ch-01-intro-to-coding-agent/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-01-intro-to-coding-agent/hands-on.md) |
| ch-02 | 让 AI 开口说话 | [theory](./docs/workshop/chapters/ch-02-conversation-module/theory.md) | [python](./docs/workshop/chapters/ch-02-conversation-module/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-02-conversation-module/hands-on.md) |
| ch-03 | 工具系统 | [theory](./docs/workshop/chapters/ch-03-tool-system/theory.md) | [python](./docs/workshop/chapters/ch-03-tool-system/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-03-tool-system/hands-on.md) |
| ch-04 | Agent 主循环 | [theory](./docs/workshop/chapters/ch-04-agent-loop/theory.md) | [python](./docs/workshop/chapters/ch-04-agent-loop/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-04-agent-loop/hands-on.md) |
| ch-06 | 权限系统 | [theory](./docs/workshop/chapters/ch-06-permissions/theory.md) | [python](./docs/workshop/chapters/ch-06-permissions/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-06-permissions/hands-on.md) |
| ch-08 | 上下文管理 | [theory](./docs/workshop/chapters/ch-08-context-management/theory.md) | [python](./docs/workshop/chapters/ch-08-context-management/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-08-context-management/hands-on.md) |
| ch-09 | 记忆系统 | [theory](./docs/workshop/chapters/ch-09-memory-system/theory.md) | [python](./docs/workshop/chapters/ch-09-memory-system/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-09-memory-system/hands-on.md) |
| ch-11 | Skill 系统 | [theory](./docs/workshop/chapters/ch-11-skills/theory.md) | [python](./docs/workshop/chapters/ch-11-skills/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-11-skills/hands-on.md) |
| ch-13 | SubAgent | [theory](./docs/workshop/chapters/ch-13-subagents/theory.md) | [python](./docs/workshop/chapters/ch-13-subagents/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-13-subagents/hands-on.md) |
| ch-15 | Agent Teams | [theory](./docs/workshop/chapters/ch-15-agent-teams/theory.md) | [python](./docs/workshop/chapters/ch-15-agent-teams/python-implementation.md) | [hands-on](./docs/workshop/chapters/ch-15-agent-teams/hands-on.md) |

参考实现：[mewcode/](./mewcode/)

---

## 🚀 快速开始

**完全新手**：

```
手册第1章（认识它）
  → 第4章（会话管理）
  → 第5章（项目级配置）
  → 第6章（核心工作流）
```

**想亲手造一个 Agent**：

```
工坊 ch-01 → ch-02 → ch-03 → ch-04
        ↓
  ch-06 → ch-08 → ch-09
        ↓
  ch-11 → ch-13 → ch-15
```

**想提升稳定性**：

```
手册第7章（五层安全开发闭环）
  → 第10章（记忆系统）
  → 第11章（上下文窗口管理）
  → 第24章（常见失败模式与修复）
```

**团队落地**：

```
手册第9章（小米 AI Coding 工程化实践）
  → 第17-19章（多任务 / Worktree / 流水线）
  → 第20-21章（权限与隔离）
```

---

## 📁 仓库结构

```
.
├── docs/
│   ├── assets/          # 横幅、插图、小米实践配图
│   ├── manual/          # 8 部分使用手册
│   ├── recipes/         # 可复用配方：agents / skills / docs / source-analysis
│   └── workshop/        # Agent Runtime 工坊教程
│       └── chapters/
├── harness/             # Agent Harness 学习笔记
├── mewcode/             # Python 版 Agent Runtime 参考实现
├── AGENTS.md            # 仓库维护规范
├── LICENSE              # MIT
└── README.md            # 本文件
```

---

## 📝 命名规范

- **目录**：`kebab-case`（`part-01-overview`、`query-loop-deep-dive`）
- **Markdown 文件**：`kebab-case`
- **Python 模块**：`snake_case`
- **避免**：中文文件名、空格

---

## ⚠️ 声明

本仓库内容为学习、实践与经验沉淀，所有引用素材均已标注来源。本仓库不包含任何受版权保护的原始商业源码。

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=vitoworleone/claude-code-handbook&type=Date)](https://star-history.com/#vitoworleone/claude-code-handbook&Date)
