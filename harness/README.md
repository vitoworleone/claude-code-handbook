# Harness 学习与整理

> 本目录用于整理对 Agent Harness 架构的学习、对比分析与实践经验。

## 什么是 Harness

Harness 是包在模型外面的运行时系统，负责把一个 LLM 变成可以持续执行工作的 agent。

核心职责：

- Agent Loop
- Tool Registry
- Policy / Sandbox
- Context Manager
- Task / Memory State
- Subagent / Background Work
- Extension Surface
- Observability / Recovery

## 待整理内容

- [ ] OpenCode / Codex / DeerFlow 设计对照
- [ ] Claude Code Runtime 机制拆解
- [ ] Harness 架构模式总结

## 专题整理

- [Codex Harness 源码学习地图](./codex/README.md)：拆解 Agent Runtime、Context、Prompt、Tools、MCP、Skills、Plugins、Session、Memory 与 Compact 的装配和运行机制。

## Knowledge Governance

- [Knowledge Governance](./knowledge-governance/README.md)：AI 协作中的验证循环、主观评审、关键决策 Trace，以及文档、规则与 Skills 的长期治理。
