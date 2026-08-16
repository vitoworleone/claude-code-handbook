# Decision Traces

> 不在工作过程中强迫人或 AI 编写完整复盘。先自动保存完整证据，再异步提取候选决策，最后由人决定哪些内容值得成为长期知识。

AI 协作中会产生两类容易流失的知识：

1. 人的评审判断：为什么内容要改？
2. 关键决策的依据：为什么在多个可行方案中选择当前方案？

最终 diff 只能说明“做了什么”，很难说明“为什么这样做”。

## Three-Layer Retention

```text
Raw session trace
→ AI-generated decision candidates
→ Human-approved durable knowledge
```

| 层 | 内容 | 用途 |
| --- | --- | --- |
| Raw trace | 对话、工具调用、测试、失败尝试、diff | 审计和追溯 |
| Candidate knowledge | 候选决策、候选 review case、未决风险 | 降低人工回看成本 |
| Durable knowledge | 决策记录、案例库、规则、CI | 跨任务复用 |

原始 trace 是档案，不应默认加载到 agent 上下文，也不应直接提交到代码仓库。

## Do Not Preserve Hidden Reasoning

真正需要长期保留的是可审计的依据，而不是模型的完整内部思维过程：

```text
遇到了什么问题
→ 考虑了哪些方案
→ 最终选择了什么
→ 关键依据和证据是什么
→ 放弃其他方案的原因是什么
→ 对未来形成了什么约束
```

## What Counts as a Key Decision

以下情况值得产生候选决策卡片：

- 在两个以上合理方案中作出有取舍的选择；
- 初始方案失败后采用非显而易见的新路径；
- 改变产品、接口、文档或信息架构边界；
- 决策依赖最终结果中看不出的事实；
- 用户纠正了 AI 的默认判断；
- 类似任务未来可能再次遇到；
- 如果不知道原因，维护者很可能错误撤销该决定。

机械性修改、普通格式调整和一次性操作通常不需要进入决策记录。

## Decision Candidate Template

```md
## Decision Candidate

### Problem
What needed to be resolved?

### Decision
What was chosen?

### Evidence
Which facts, constraints, tests, or user needs supported it?

### Alternatives
What was considered and why was it rejected?

### Impact and scope
What future work does this constrain? When does it not apply?

### Provenance
- Session ID:
- Transcript:
- Commit or PR:
- Related files and tests:
```

## Capture without Interrupting Flow

Claude Code already stores session transcripts continuously as local JSONL. A `SessionEnd` hook can obtain the `session_id` and `transcript_path`; it should quickly enqueue a processing job, not synchronously ask a model to summarize a long session.

```text
Claude Code saves transcript
→ SessionEnd hook records session metadata
→ background worker reads transcript + git diff + test results
→ AI emits a small set of candidate decisions
→ human confirms, edits, or discards later
```

`PreCompact` can create an optional checkpoint for very long sessions. `Stop` is generally too frequent for this purpose, and `PostToolUse` is best reserved for specific high-risk actions rather than semantic decisions.

## Promotion Rules

| Candidate type | Destination |
| --- | --- |
| One-off implementation detail | PR, commit, or issue |
| Durable technical or information-architecture choice | decision log / ADR |
| Repeated AI or human review failure | review case library |
| Stable rule across multiple cases | standard or path rule |
| Objectively checkable constraint | script, lint, test, or CI |

Raw transcripts can contain source code, tool output and sensitive information. Keep them local or in an approved secure archive; do not commit them to a shared repository by default.

## Sources

- [Claude Code: Manage sessions](https://code.claude.com/docs/en/sessions)
- [Claude Code: Hooks reference](https://code.claude.com/docs/en/hooks)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
