# MewCode

> 一个用于教学的 Python 版 Coding Agent Runtime。

MewCode 是 [claude-code-handbook](..) 中 **Agent Runtime 工坊** 的参考实现。它不是一个生产级产品，而是一个**可运行、可修改、可学习**的代码骨架，帮助你理解 Claude Code 这类编程 Agent 的核心机制。

---

## 能学到什么

通过阅读和实践 MewCode，你可以理解：

- **Agent 主循环**：如何把用户输入、模型输出、工具调用串成循环
- **工具系统**：如何注册、执行、返回工具结果
- **LLM 客户端**：如何接入 Anthropic / OpenAI 并处理流式响应
- **权限系统**：多层权限检查和安全决策
- **上下文管理**：Token 压缩、摘要和窗口管理
- **记忆系统**：跨会话持久化和自动提取
- **Skill 系统**：可复用技能包的加载和执行
- **SubAgent**：子 Agent 创建与任务分发
- **Agent Teams**：多 Agent 协作与团队编排

---

## 快速开始

### 环境要求

- Python >= 3.11
- uv（推荐）或 pip

### 安装

```bash
cd mewcode
uv sync
```

### 运行

```bash
uv run mewcode
# 或
python -m mewcode
```

### 运行测试

```bash
uv run pytest tests/ -v
```

---

## 项目结构

```
mewcode/
├── mewcode/
│   ├── __main__.py          # CLI 入口
│   ├── agent.py             # Agent 主循环
│   ├── client.py            # LLM API 客户端
│   ├── conversation.py      # 对话管理
│   ├── tools/               # 内置工具实现
│   ├── permissions/         # 权限检查
│   ├── memory/              # 记忆系统
│   ├── context/             # 上下文管理
│   ├── skills/              # Skill 加载器
│   ├── mcp/                 # MCP 客户端
│   └── teams/               # Agent Teams
├── tests/                   # 单元测试
└── pyproject.toml           # 项目配置
```

---

## 与工坊教程对应

| 工坊章节 | MewCode 模块 |
|----------|-------------|
| ch-02 让 AI 开口说话 | `mewcode/client.py`, `mewcode/conversation.py` |
| ch-03 工具系统 | `mewcode/tools/` |
| ch-04 Agent 主循环 | `mewcode/agent.py` |
| ch-06 权限系统 | `mewcode/permissions/` |
| ch-08 上下文管理 | `mewcode/context/` |
| ch-09 记忆系统 | `mewcode/memory/` |
| ch-11 Skill 系统 | `mewcode/skills/` |
| ch-13 SubAgent | `mewcode/agents/` |
| ch-15 Agent Teams | `mewcode/teams/` |

---

## 配置

运行前需要设置 API Key：

```bash
export ANTHROPIC_API_KEY=sk-...
# 或
export OPENAI_API_KEY=sk-...
```

---

## 免责声明

MewCode 仅用于学习目的。它会执行 shell 命令和文件操作，请在隔离环境中运行，不要在生产代码库或敏感环境中直接使用。
