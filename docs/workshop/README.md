# Agent Runtime 工坊

> 手把手从零实现一个 Claude Code 风格的 Coding Agent。

本工坊以 **MewCode Python**（`../../mewcode/`）为参考实现，按章节逐步拆解 Agent Runtime 的核心机制。

## 核心章节

| 章节 | 内容 | 理论学习 | Python 实现 | 实战演练 |
|------|------|----------|-------------|----------|
| ch-01 | 初识 Coding Agent | [theory](./chapters/ch-01-intro-to-coding-agent/theory.md) | [python](./chapters/ch-01-intro-to-coding-agent/python-implementation.md) | [hands-on](./chapters/ch-01-intro-to-coding-agent/hands-on.md) |
| ch-02 | 让 AI 开口说话 | [theory](./chapters/ch-02-conversation-module/theory.md) | [python](./chapters/ch-02-conversation-module/python-implementation.md) | [hands-on](./chapters/ch-02-conversation-module/hands-on.md) |
| ch-03 | 工具系统 | [theory](./chapters/ch-03-tool-system/theory.md) | [python](./chapters/ch-03-tool-system/python-implementation.md) | [hands-on](./chapters/ch-03-tool-system/hands-on.md) |
| ch-04 | Agent 主循环 | [theory](./chapters/ch-04-agent-loop/theory.md) | [python](./chapters/ch-04-agent-loop/python-implementation.md) | [hands-on](./chapters/ch-04-agent-loop/hands-on.md) |
| ch-06 | 权限系统 | [theory](./chapters/ch-06-permissions/theory.md) | [python](./chapters/ch-06-permissions/python-implementation.md) | [hands-on](./chapters/ch-06-permissions/hands-on.md) |
| ch-08 | 上下文管理 | [theory](./chapters/ch-08-context-management/theory.md) | [python](./chapters/ch-08-context-management/python-implementation.md) | [hands-on](./chapters/ch-08-context-management/hands-on.md) |
| ch-09 | 记忆系统 | [theory](./chapters/ch-09-memory-system/theory.md) | [python](./chapters/ch-09-memory-system/python-implementation.md) | [hands-on](./chapters/ch-09-memory-system/hands-on.md) |
| ch-11 | Skill 系统 | [theory](./chapters/ch-11-skills/theory.md) | [python](./chapters/ch-11-skills/python-implementation.md) | [hands-on](./chapters/ch-11-skills/hands-on.md) |
| ch-13 | SubAgent | [theory](./chapters/ch-13-subagents/theory.md) | [python](./chapters/ch-13-subagents/python-implementation.md) | [hands-on](./chapters/ch-13-subagents/hands-on.md) |
| ch-15 | Agent Teams | [theory](./chapters/ch-15-agent-teams/theory.md) | [python](./chapters/ch-15-agent-teams/python-implementation.md) | [hands-on](./chapters/ch-15-agent-teams/hands-on.md) |

## 参考实现

- [mewcode/](../../mewcode/) — Python 版 MewCode 完整实现

## 学习路线

```
ch-01 → ch-02 → ch-03 → ch-04
          ↓
    ch-06 → ch-08 → ch-09
          ↓
    ch-11 → ch-13 → ch-15
```
