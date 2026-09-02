# Codex 系统提示词：来源与组装方式

本文说明本仓库中 Codex 最终发送给模型的 Prompt 如何由基础指令和动态上下文共同组成。完整中文基础指令请先阅读 [base-instructions.zh-CN.md](base-instructions.zh-CN.md)。

> 结论：运行时的系统提示词不是单一固定文件。Core 会在启动线程时选择基础指令，并在每一次模型请求时与工具定义、会话历史、项目指令等内容组合为最终 Prompt。

## 1. 术语

| 名称 | 含义 | 是否等同于系统提示词 |
| --- | --- | --- |
| Base Instructions | 一条线程的基础指令；会进入模型请求的 `instructions` 字段。 | 是，主体部分。 |
| Developer Instructions | 调用方或配置提供的额外开发者级指令。 | 否，是独立叠加层。 |
| AGENTS.md | 项目或目录范围内的人类工作约束。 | 否，是项目上下文。 |
| Skill Instructions | 某类任务的操作指南和参考信息。 | 否，按条件注入的上下文。 |
| Tool Definitions | 当前允许模型调用的工具及参数 schema。 | 否，但会与基础指令一同发送。 |

## 2. 主要源码位置

| 位置 | 作用 |
| --- | --- |
| [`default.md`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/prompts/base_instructions/default.md) | 编译进程序的默认基础指令英文全文。 |
| [`base-instructions.zh-CN.md`](base-instructions.zh-CN.md) | 默认基础指令的中文整理译本。 |
| [`skill-context-loading.md`](skill-context-loading.md) | Skill 目录与按需正文如何分两阶段进入 `Prompt.input`。 |
| [`tool-context-loading.md`](tool-context-loading.md) | 不同工具来源如何获取、注册、渐进暴露并进入 `Prompt.tools`。 |
| [`models.json`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/models-manager/models.json) | 随模型目录提供的 `instructions_template`；实际选择某个模型时可作为基础指令来源。 |
| [`protocol/src/models.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/models.rs) | 用 `include_str!` 将默认 Markdown 编译为 `BASE_INSTRUCTIONS_DEFAULT`。 |
| [`protocol/src/openai_models.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/openai_models.rs) | 渲染模型的 `instructions_template`，包括 personality 变量。 |
| [`core/src/session/mod.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs) | 线程启动时选择基础指令。 |
| [`core/src/session/turn.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs) | 每次模型采样前把基础指令、工具和输入组装为 `Prompt`。 |

## 3. 基础指令的选择优先级

创建一个新的 `Session` 时，Core 使用以下优先级：

```text
1. config.base_instructions
2. 已恢复会话历史中保存的 session_meta.base_instructions
3. 当前模型的 instructions_template（按 personality 渲染）
```

其中第 1 项可来自内联配置或 `model_instructions_file` 配置文件。基础指令被选定后会随线程保存；因此恢复线程时可以保持原来的行为，即使之后默认模型模板已经变化。

## 4. 默认基础指令与动态上下文

默认全文以 [`default.md`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/prompts/base_instructions/default.md) 为准；中文阅读版见 [`base-instructions.zh-CN.md`](base-instructions.zh-CN.md)。基础指令当前包含以下主题：

```text
角色与能力
  - Codex 是本地运行的编码 Agent
  - 可以接收用户输入、工作区上下文并调用函数工具

工作方式
  - 语气、清晰度和进度沟通要求
  - 调用工具前的简短预告（preamble）
  - 什么时候应使用执行计划
  - 任务实施、验证、进度更新和最终回复规范

项目指令：AGENTS.md
  - AGENTS.md 的目录作用域
  - 嵌套 AGENTS.md 的优先级
  - 系统、开发者、用户和项目指令的优先级关系

工具规范
  - Shell 命令和文件操作的使用约束
  - update_plan 等控制工具的使用原则
  - 其他工具调用的安全与沟通要求
```

不要把这份默认基础指令与项目中的 `AGENTS.md` 混为一谈：前者定义 Codex 的通用行为；后者是某个仓库或目录的本地规则。

## 5. 动态上下文：一次模型请求还会加入什么

Core 在每个 `Step` 中构建如下概念模型：

```text
Prompt
├── base_instructions      基础系统指令
├── tools                  当前模型可见的工具 schema
├── input                  历史消息、用户输入、工具结果和上下文片段
├── output_schema          可选的结构化输出要求
└── request metadata       模型、权限、环境、追踪等请求元数据
```

`AGENTS.md`、Skill、Plugin/Connector 指令、MCP 资源等内容通常进入 `input` 的上下文片段，而不是替换 `base_instructions`。

## 6. 与 Agent Runtime 的关系

```text
Thread 启动
  → 解析 Config / 模型目录 / 历史
  → 选定并保存 Base Instructions

Turn 运行
  → 读取 Base Instructions
  → 捕获当前 Step 的工具、环境和 MCP 快照
  → 合并历史和当前输入
  → 发起模型请求
```

因此，系统提示词属于 `codex-core` 的上下文管理职责，而不是 Tool、MCP 或 UI 的职责。

## 7. 学习顺序

建议按以下顺序阅读源码：

1. [`base-instructions.zh-CN.md`](base-instructions.zh-CN.md)：先了解默认行为的中文版本。
2. [`default.md`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/prompts/base_instructions/default.md)：需要逐句对照时查看英文原文。
3. [`models.json`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/models-manager/models.json)：理解不同模型可携带不同模板。
4. `core/src/session/mod.rs` 中“Resolve base instructions for the session”附近：理解选择优先级。
5. `core/src/session/turn.rs` 中 `build_prompt`：理解基础指令如何与工具和历史一起送给模型。

## 8. 维护说明

本文件是源码导览和架构说明，不复制默认提示词全文，以避免与上游的 `default.md` 或模型目录模板发生漂移。需要查看完整原文时，应以第 2 节列出的源码为准。
