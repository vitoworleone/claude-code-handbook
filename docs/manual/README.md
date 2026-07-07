# Claude Code 使用手册（草稿）

> 本文档基于 `E:\Claude Code\ClaudeCode-Compressed-2026-05-28\` 压缩文档集整理。
> 原始素材：69 个 Markdown 文档的压缩版。
> 创建日期：2026-05-28
> 状态：大纲阶段，各章节内容待填充

---

## 手册定位

这不是源码分析文档，而是一份**从使用者视角出发**的实战指南。去掉了 query_loop 状态机、StreamingToolExecutor 内部实现等学术级源码细节，保留了可直接落地的工作流、配置方法和最佳实践。

**核心理念**：官方文档告诉你"有什么功能"，实战文档告诉你"怎么组合用起来最高效"。

---

## 目录结构

### 第一部分：认识 Claude Code
- [第1章 它到底是什么](part-01-overview/ch-01-what-is-it.md)
  - 核心定位：不是聊天机器人，是 Agent Runtime 操作系统
  - 适用场景与竞品区别
  - 模型分工策略
- [第2章 三种使用形态](part-01-overview/ch-02-three-forms.md)
  - 交互式 REPL / Headless SDK / 管道模式 / 后台运行

### 第二部分：基础配置与上手
- [第3章 安装与初始化](part-02-basics/ch-03-install-init.md)
  - 权限模式选择、CLI 工具链配置
- [第4章 会话管理](part-02-basics/ch-04-session-mgmt.md)
  - 会话生命周期、常用命令、Plan Mode
- [第5章 项目级配置（`.claude/` 目录）](part-02-basics/ch-05-project-config.md)
  - 配置优先级链、CLAUDE.md、rules、skills、agents、commands

### 第三部分：核心工作流（实战方法整合）
- [第6章 需求驱动工作流](part-03-workflow/ch-06-demand-workflow.md)
  - 刘小排五步 + Matt Pocock 管线（需求明确 → PRD → 看板 → AFK → QA → 审查 → 上线）
- [第7章 五层安全开发闭环](part-03-workflow/ch-07-security-closure.md)
  - 徐文浩：沙箱 → 分支 → Code Review → 自动测试 → AI 审核
- [第8章 开发节奏与心态](part-03-workflow/ch-08-development-rhythm.md)
  - 三阶段消耗策略、角色转变、Smart Zone 意识
- [第9章 从个人提速到团队提效——小米 AI Coding 工程化实践](part-03-workflow/ch-09-xiaomi-practice.md)
  - 统一工作流（VAF）、代码知识库（VKF）、协作工作台（eight-claw）、四条设计原则

### 第四部分：记忆与上下文管理
- [第10章 记忆系统](part-04-context/ch-10-memory-system.md)
  - 四层持久化体系、CLAUDE.md 写法、自动记忆提取
- [第11章 上下文窗口管理](part-04-context/ch-11-context-window.md)
  - 五层渐进式防御压缩、Token 经济性、Prompt Cache
- [第12章 上下文质量提升](part-04-context/ch-12-context-quality.md)
  - 子代理隔离、Skill 加载、Doc Rot 防护

### 第五部分：扩展与自动化
- [第13章 Skills 系统](part-05-extension/ch-13-skills.md)
  - SKILL.md 格式、存储优先级、动态上下文注入
- [第14章 Hooks 系统](part-05-extension/ch-14-hooks.md)
  - 事件类型、常见玩法、Memory/Skill/Hook 边界区分
- [第15章 MCP 与外部系统集成](part-05-extension/ch-15-mcp-integration.md)
  - MCP 调用外部系统、Channels 事件推送、安全边界
- [第16章 插件系统](part-05-extension/ch-16-plugins.md)
  - 插件结构、市场、分发

### 第六部分：并行与协作
- [第17章 多任务并行](part-06-parallel/ch-17-multi-task.md)
  - Agent View、后台运行、子代理模式
- [第18章 Worktree 隔离](part-06-parallel/ch-18-worktree.md)
  - Git worktree 并行会话、子代理自动隔离
- [第19章 并行流水线（四角色模型）](part-06-parallel/ch-19-pipeline.md)
  - Planner + Sandbox + Reviewer + Merger、Ralph Loop 演进

### 第七部分：安全与规范
- [第20章 权限与审批](part-07-security/ch-20-permissions.md)
  - 四级决策链、白名单模型、Bash 安全解析、Fail-Closed
- [第21章 隔离与防护](part-07-security/ch-21-isolation.md)
  - Sandbox、Worktree、子代理权限收缩

### 第八部分：高级技巧
- [第22章 输出风格与模式](part-08-advanced/ch-22-output-styles.md)
  - Default/Proactive/Explanatory/Learning 四种风格、提示技巧
- [第23章 自动化与 Dream 模式](part-08-advanced/ch-23-automation.md)
  - Stop-Time 自动化、AutoDream、Away Summary
- [第24章 常见失败模式与修复](part-08-advanced/ch-24-failure-modes.md)
  - 上下文溢出、死循环、大文件编辑、工具调用失败

---

## 快速上手建议

如果只想快速上手，**ROI 最高的阅读路径**：

```
第1章（认识它） → 第4章（会话管理） → 第5章（项目配置）
    ↓
第6章（核心工作流） → 第9章（小米组织实践） → 第10章（记忆系统） → 第11章（上下文管理）
    ↓
第13章（Skills） + 第15章（MCP）
```

---

## 内容来源说明

本手册整合了以下素材：

| 来源类型 | 文件数 | 代表内容 |
|---|---|---|
| apply/ 实战方法论 | 5 | Matt Pocock 工作流、徐文浩安全闭环、刘小排实战方法论、小米 AI Coding 工程化实践、官方文档阅读玩法 |
| Offical Doc/ 官方文档 | 10 | 目录结构、配置、Channels、Hooks、Skills、记忆、插件、Worktree、最佳实践 |

---

*待办：逐章填充内容，将压缩文档中的知识点转化为可直接使用的手册内容。*
