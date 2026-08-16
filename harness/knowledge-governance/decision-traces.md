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

## Hook Architecture in Detail

Claude Code 已经会持续写入完整的 session transcript。为 trace 设计 Hook 时，不应重新记录整段对话，也不应在每一轮强迫 AI 撰写复盘；Hook 的职责是**精确定位、登记并触发后续提炼**。

```text
Claude Code writes JSONL continuously
→ SessionEnd hook receives session metadata
→ hook writes one queue record and returns
→ asynchronous worker reads transcript, diff, and verification results
→ worker emits candidate decisions and review cases
→ human promotes, edits, or discards candidates later
```

这使“留痕”变成被动发生的系统能力，而不是每次工作都需要执行的仪式。

### Why SessionEnd Is the Default Trigger

`SessionEnd` 是默认选择，因为它在 session 退出、`/clear` 和通过 `/resume` 切换 session 时触发，并向 command hook 的标准输入提供：

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/project/session.jsonl",
  "cwd": "/path/to/project",
  "hook_event_name": "SessionEnd",
  "reason": "other"
}
```

它没有终止控制权，因此很适合做日志登记或清理工作。默认时间预算只有 1.5 秒，故不应在这里同步调用长上下文模型总结 session。

### Event Selection

| Event | 适合做什么 | 为什么不是默认方案 |
| --- | --- | --- |
| `SessionEnd` | 登记完整 session、排队异步复盘 | 最低干扰；推荐作为主入口 |
| `PreCompact` | 很长的 session 在压缩前建立中间检查点 | 只在长任务或高风险任务中启用 |
| `PostCompact` | 记录已经生成的压缩摘要 | 无法替代完整原始 transcript |
| `Stop` | 对每轮输出做轻量完成度判断 | 过于频繁，增加延迟并打断心流 |
| `PostToolUse` | 审计特定高风险工具或文件修改 | 能看到操作，通常看不到完整语义决策 |
| `SubagentStop` | 收集专门子 agent 的结论与 transcript | 适合独立 reviewer 或研究子任务 |

### Example Settings

下面是概念示意。实际脚本路径、队列位置、权限和保留周期需要按团队环境配置。

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/enqueue-trace.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`enqueue-trace.sh` 从 stdin 读取 JSON，只写入一条待处理记录，并且不向 stdout 输出会进入 agent context 的内容。它不总结、不调用外部模型、不读取大量文件。

### Queue Record

```json
{
  "session_id": "abc123",
  "transcript_path": "/approved/local/path/abc123.jsonl",
  "repository": "example-repository",
  "branch": "feature-x",
  "head_commit": "123456",
  "ended_at": "2026-08-16T12:00:00Z",
  "reason": "clear",
  "status": "pending"
}
```

队列记录应保存指针和少量元数据，而不是复制 transcript 本身。可额外记录 git diff 的摘要或验证命令状态，但不要让 Hook 为此运行昂贵操作。

### Background Worker Responsibilities

异步 worker 读取队列后，才进行较重的工作：

1. 读取 transcript、最终 diff、测试输出和相关 issue / PR；
2. 找出候选关键决策、人工纠正、失败尝试、未验证假设；
3. 给每个候选项附上原始证据位置；
4. 生成 review case 或 decision candidate 草稿；
5. 输出“无可沉淀内容”也是合法结果；
6. 将草稿放在候选区域，等待人集中处理。

worker 的目标是高召回：宁可提出少量可删除的候选项，也不要让人重新阅读长 transcript 寻找关键时刻。晋升到长期规则时，再由人追求高精度。

### Failure Recovery

不能把完整性完全建立在 `SessionEnd` 是否成功触发上。异常退出、终端崩溃或脚本故障都可能遗漏入队；但 transcript 通常在工作过程中已经持续保存。

因此应有补偿机制：

```text
SessionEnd queue record missing
→ next SessionStart or scheduled scanner finds unprocessed JSONL
→ verifies repository and retention policy
→ enqueues it once using session_id as idempotency key
```

### Security and Retention Boundaries

- 原始 transcript 可能含源码、命令输出、内部链接、客户数据或凭据；默认不提交 Git。
- 候选摘要与正式规则分开存放；只将经确认、已脱敏的长期知识放入共享仓库。
- 限制 worker 可访问的目录、网络和外部服务，尤其是处理生产仓库时。
- 为 raw trace、candidate summary 和 canonical knowledge 分别设置保留期与访问权限。
- 记录 provenance：任何决策卡片应能回到 session、commit、测试或相关文件，而不是只留下 AI 的概括。

## Incremental Adoption

### Phase 0: Preserve Existing Evidence

- 使用已有的本地 session JSONL；
- 不创建文档，不引入模型调用；
- 只确认保留期、安全边界和 session 可追溯性。

### Phase 1: Enqueue on Session End

- 使用 `SessionEnd` Hook 记录 session 指针；
- worker 先只输出候选项，不写正式规则；
- 每周或每次 PR review 集中处理候选项。

### Phase 2: Promote Knowledge

- 可复用的技术取舍进入 decision log；
- 重复 review 错误进入 case library；
- 多个 case 支持的规则进入标准或 agent instructions；
- 可确定性验证的约束进入 CI。

### Phase 3: Measure Quality

- 候选决策是否真的减少重复讨论？
- AI 是否减少重复犯错？
- 哪些候选项总被删除，说明提取器过度捕捉？
- 哪些人工发现的问题没有被捕捉，说明信号或样本不足？

trace 系统的成功标准不是产生更多记录，而是让后续任务更少重新发现相同事实、更少重复同一种错误。

## Sources

- [Claude Code: Manage sessions](https://code.claude.com/docs/en/sessions)
- [Claude Code: Hooks reference](https://code.claude.com/docs/en/hooks)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
