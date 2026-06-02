# AI Coding Agent 上下文、工具与 Skill 系统调研报告

> 生成日期：2026-05-22  
> 范围：本报告参考当前工作区全部 Markdown 索引材料，以及 `[TypeScript source map analysis]` 下的 Claude Code 逆向源码。  
> 输出目标：回答 Token 经济性、任务命中率、工具调用能力、Skill 过多、上下文维护、不同模型工具遵循差异六组问题，并给出可验证、可落地的工程方案。

---

## 0. 证据来源与路径修正

用户给出的路径写作 `03_   References/.../src claude code源代码`，当前工作区实际存在的是：

```text
 code analysis\src
```

本报告主要使用以下本地证据：

| 证据 | 关键内容 |
|---|---|
| `src/utils/analyzeContext.ts` | 把上下文拆成 System prompt、System tools、MCP tools、Custom agents、Memory files、Skills、Messages、reserved buffer、free space，并统计 tool call/result/attachment/message token。 |
| `src/query/tokenBudget.ts` | budget tracker：低于 90% budget 时继续，连续 3 次低增量且每次低于 500 token 时判定收益递减。 |
| `src/services/compact/autoCompact.ts` | auto compact 预留 summary 输出空间、13k buffer、连续失败熔断、优先尝试 session memory compaction。 |
| `src/services/compact/compact.ts` | compact 前剥离图片和重复注入附件，Prompt Too Long 时截断头部重试，compact 后重注入文件、plan、skill、deferred tools delta 等工作状态。 |
| `src/services/SessionMemory/*` | session memory 阈值：初始 10k token、每 5k token 更新、至少 3 次工具调用；避免在 tool_use 链中间更新。 |
| `src/services/compact/sessionMemoryCompact.ts` | 用 session memory 做 compact summary，保留最近原文，并调整裁剪点以维持 tool_use/tool_result API 不变量。 |
| `src/memdir/memdir.ts`、`src/memdir/findRelevantMemories.ts` | `MEMORY.md` 是索引，不是正文；索引限制 200 行/25KB；相关记忆按 manifest 选择，不全量注入。 |
| `src/skills/loadSkillsDir.ts`、`src/tools/SkillTool/prompt.ts` | skill 只估算 frontmatter token，完整内容按需加载；支持 `when_to_use`、`allowed-tools`、`paths`、`user-invocable`、`context`、`agent` 等元数据。 |
| `src/Tool.ts` | Tool 是运行时协议对象，包含 schema、description、prompt、read-only/destructive/concurrency 标签、权限、渲染、结果映射。 |
| `src/utils/collapseReadSearch.ts`、`collapseHookSummaries.ts` | UI/上下文层面对连续 read/search、hook summary 进行折叠，说明低价值重复工具事件可以摘要化。 |
| 既有 Markdown | `[internal research report]`、`docs/internals/02-system-analysis/09 Prompt 详解：工具.md` 等材料已经形成了 transcript、compact、memory、tool schema、skill prompt 的基础判断。 |

---

## 1. 总结论

### 1.1 核心判断

AI coding agent 的上下文不是“越多越好”，而是一个分层缓存和证据流系统。真正影响任务成功率的不是完整历史，而是：

1. 当前目标和约束。
2. 代码库地图和相关文件定位线索。
3. 最近工具调用的关键结果。
4. 正在编辑的文件片段和失败验证输出。
5. 已做决策、未完成事项、风险和假设。

最浪费 token、贡献最低的是：

1. 重复注入的规则、工具说明、skill 全文、长历史寒暄。
2. 已消费过的原始工具输出，尤其是长 grep、长 test log、长 file read。
3. 大量未被本轮使用的 MCP/tool schema。
4. 旧代码片段全文，尤其是在模型只需要“文件名、符号、行号、结论”时仍保留全文。
5. compact 前后的重复附件、重复 file attachment、重复 memory 索引。

### 1.2 设计目标优先级

当“提高行为质量”和“减少用户 prompt 成本”冲突时，优先级应是：

```text
安全与可恢复性 > 任务成功率 > Token 成本 > 用户 prompt 简洁度 > UI 美观
```

原因很直接：token 可以通过缓存、检索、摘要和 defer 优化；但错误编辑、错误记忆、错误验证声明会破坏用户信任和工程资产。Skill system 的首要目标应是提高模型行为质量，减少 prompt 成本是附带收益。

---

## 2. Token 经济性

### 2.1 谁是真正的 token 大户

从 `analyzeContextUsage()` 的分类看，系统已经把 token 大户拆成这些类：

```text
System prompt
System tools
MCP tools
MCP tools (deferred)
System tools (deferred)
Custom agents
Memory files
Skills
Messages
Autocompact buffer / Compact buffer
Free space
```

这说明 token 大户不是单一来源，而是两类叠加：

| 类别 | 是否常驻 | 风险 |
|---|---:|---|
| System prompt / project rules | 是 | 稳定但容易膨胀；会影响 prompt cache 和模型注意力。 |
| Tool schema / MCP schema | 是或按需 | 工具多时非常贵；schema 描述质量直接影响工具选择。 |
| Messages transcript | 随会话增长 | 长会话最大变量；包含 tool_result、代码片段、测试输出。 |
| Tool result | 随任务增长 | 单次可能极大；读文件、grep、test log 是典型大户。 |
| Skills | frontmatter 常驻，正文按需 | skill 全文常驻会变成垃圾抽屉。 |
| Memory files | 索引常驻，正文按需 | memory 污染会长期影响行为。 |

结论：短会话里，system prompt 和 tool schema 占比高；长会话里，messages 尤其 tool_result 会成为主导；工具生态膨胀后，MCP/tool schema 会重新变成大头。

### 2.2 上下文片段保留等级

建议所有上下文片段进入一个四级策略，而不是简单保留/丢弃：

| 等级 | 适用内容 | 保留形式 | 触发条件 |
|---|---|---|---|
| 原文保留 | 当前正在编辑的代码块、失败断言、用户最新要求、权限决策、未完成 tool_use/result 配对 | 原文 | 直接影响下一步操作，且模型需要精确字符串或错误信息。 |
| 摘要保留 | 已读过的大文件、测试长输出、长讨论历史、已完成探索过程 | 结构化摘要 | 模型只需知道结论、路径、关键行、异常类型。 |
| 索引保留 | 长期记忆、项目规则集合、skill 集合、代码库地图、外部文档 | manifest/index | 当前只需要“知道存在什么”，需要时再读取正文。 |
| 丢弃 | 重复工具输出、无关寒暄、过期假设、已被新事实覆盖的探索分支、成功但无诊断价值的完整日志 | 不进入 active context | 没有可复用事实，或可由事件日志/文件系统再获取。 |

这与源码中的 `MEMORY.md` 设计一致：`MEMORY.md` 是索引，不是正文；每条 memory 另存文件，索引限制 200 行/25KB，超过会截断并提示把细节移到 topic 文件。

### 2.3 什么时候 compact

应该 compact 的情况：

1. messages 增长接近有效上下文窗口，尤其超过 auto compact threshold。
2. 最近上下文主要是搜索、读取、测试输出，已经形成明确结论。
3. 任务从探索阶段切换到实现阶段。
4. 子任务完成，进入下一个独立子任务。
5. 已生成 session memory，且最近原文足够保留当前工作台状态。

不应该 compact 的情况：

1. 正处于 tool_use/tool_result 链中间。
2. 正在做精确编辑，模型需要上一次 Read 的原文片段。
3. 刚发生测试失败，错误栈还没有被定位。
4. 用户刚修改需求，尚未形成新的稳定任务状态。
5. 当前上下文包含多个候选假设，还没决定保留哪个。

源码层面已经体现这些约束：auto compact 有 13k buffer、连续 3 次失败熔断；session memory compaction 会等待 session memory extraction，保留最近原文，并用 `adjustIndexToPreserveAPIInvariants()` 避免裁剪到 tool_use/tool_result 中间。

### 2.4 Token budget policy

推荐按任务阶段分配不同上下文密度：

| 阶段 | 目标 | 上下文密度 | 工具策略 | compact 策略 |
|---|---|---|---|---|
| Intake | 理解需求 | 低 | 读项目说明、快速搜索入口 | 不 compact，保留用户原话。 |
| Explore | 找文件、建证据 | 中 | search -> inspect，优先索引和代码地图 | 折叠 read/search，保留发现摘要。 |
| Diagnose | 找 root cause | 高 | 读相关实现、测试、调用链 | 不在假设未收敛前 compact。 |
| Edit | 最小修改 | 高但窄 | 只读/改相关文件 | 保留精确片段和修改理由。 |
| Verify | 运行测试 | 中 | 测试输出结构化摘要 | 长日志摘要，失败片段原文保留。 |
| Wrap | 交付说明 | 低 | 读取 diff/status | compact 或写 task state。 |

`src/query/tokenBudget.ts` 可作为实现参考：低于 90% budget 时继续；连续 3 次收益低且每次新增低于 500 token 时停止。这套逻辑适合从“对话 token”扩展成“任务阶段 budget”。

### 2.5 工具结果回填粒度

工具结果不应该默认完整回填。建议结果格式统一包含：

```yaml
tool_result:
  status: success | error | partial
  intent: read_file | search | test | edit | command | web | memory
  summary: 1-5 行自然语言结论
  evidence:
    - path: ...
      line: ...
      snippet: 必要时短片段
  full_result_ref:
    type: file | event_log | artifact | transcript_range
    id: ...
  next_affordance:
    - inspect_file
    - rerun_test
    - edit_exact_range
```

原文保留只给三类内容：

1. 模型下一步必须精确复制或匹配的字符串。
2. 失败诊断必须看的错误原文。
3. 无法通过引用重取的外部状态。

其余输出进入 event log 或 artifact，只把结构化摘要回填。`collapseReadSearch.ts` 对连续 read/search 的折叠就是同一方向：保留操作计数、路径、最近 hint，而不是让默认视图吞下所有细节。

---

## 3. 命中率 / 任务成功率

### 3.1 命中率拆解

命中率应拆成四类独立指标：

| 命中类型 | 定义 | 可测指标 |
|---|---|---|
| 理解需求命中 | 是否正确复述目标、约束、验收标准 | 首次计划被用户纠正率、需求误解率。 |
| 找文件命中 | 第一次 search/read 是否命中相关模块 | 首批 N 个文件中相关文件占比、time-to-first-relevant-file。 |
| 改代码命中 | 修改是否落在正确抽象层和最小范围 | diff 触达文件数、无关改动数、review finding 数。 |
| 验证命中 | 是否运行了能证明目标的验证 | 测试选择准确率、未验证声明率、失败后定位率。 |

### 3.2 最能提高第一次找对文件的上下文

最有价值的是代码结构索引，而不是长自然语言说明。推荐最小必要上下文：

```yaml
task:
  user_goal: 原始目标
  acceptance: 验收标准
  constraints: 不要改什么/必须保留什么
repo_map:
  top_level_dirs: 目录职责
  entrypoints: CLI/API/UI/test 入口
  module_index:
    - path
    - symbols
    - owns_behavior
    - related_tests
recent_decisions:
  - decision
  - reason
  - source
search_hints:
  - exact error text
  - UI label
  - command name
  - class/function name
```

自然语言说明负责表达意图，代码结构索引负责定位。二者不是替代关系：自然语言用于目标对齐，索引用于减少盲读。

### 3.3 代码库地图设计

代码库地图不应是 README 复述，而应支持工具选择：

```yaml
code_map:
  generated_at: 2026-05-22T...
  repo_root: ...
  modules:
    - path: src/services/compact
      responsibility: context compaction and session-memory compaction
      key_files:
        - autoCompact.ts
        - compact.ts
        - sessionMemoryCompact.ts
      public_symbols:
        - shouldAutoCompact
        - autoCompactIfNeeded
        - trySessionMemoryCompaction
      related_tests: []
      search_terms:
        - compact
        - token
        - context window
  symbol_index:
    - symbol: analyzeContextUsage
      file: src/utils/analyzeContext.ts
      purpose: context token breakdown
```

验证方式：给同一组任务分别提供“无地图”“目录树”“代码库地图”，测量第一次相关文件命中率、工具调用次数、读文件 token、最终成功率。

### 3.4 让 agent 先探索而不是猜

需要把“不确定性”变成 runtime 可观测状态：

| 状态 | 触发 | 必须动作 |
|---|---|---|
| no_evidence | 没读文件就准备实现 | 强制 search/read。 |
| weak_evidence | 只读了 README 或只读了一个文件 | 至少再读调用方或测试。 |
| conflicting_evidence | 文档和代码不一致 | 以代码/测试为准，记录冲突。 |
| edit_ready | 有目标文件、目标符号、修改理由、验证路径 | 允许编辑。 |

判断模型是否基于证据工作，可以检查最终回答和 edit 之前是否包含：

1. 文件路径。
2. 行号或符号名。
3. 工具调用证据。
4. 假设与已验证事实分离。
5. 对未验证内容显式标记。

### 3.5 Bug 修复 root cause 命中率

最能提高 root cause 命中的上下文：

1. 可复现步骤和实际错误原文。
2. 最近变更 diff。
3. 失败测试最小输出，而不是完整日志。
4. 相关调用链的代码片段。
5. 配置/环境差异。
6. 负证据：已经排除哪些假设。

落地建议：bug 任务进入模型前生成 `diagnosis_state`：

```yaml
diagnosis_state:
  symptom: ...
  reproduction: ...
  failing_command: ...
  error_excerpt: ...
  suspected_modules: []
  evidence_read:
    - path
    - finding
  hypotheses:
    - claim
    - evidence_for
    - evidence_against
    - status: open | rejected | confirmed
  next_probe: ...
```

失败案例应沉淀为“检索规则”而不是“长故事”。例如：“遇到 orphaned tool_result 错误时，先检查 transcript normalization 和 tool_use/result pairing”，并附 provenance。

---

## 4. 工具调用能力

### 4.1 好 tool schema 的特征

源码中的 `Tool` 不是函数，而是能力协议对象。它包含：

```text
name / description / prompt / inputSchema / outputSchema
isConcurrencySafe / isReadOnly / isDestructive
validateInput / checkPermissions
renderToolUseMessage / renderToolResultMessage
mapToolResultToToolResultBlockParam
```

更容易被模型正确调用的 schema 有这些特征：

1. 名称是动词短语或领域名词，避免泛化。
2. description 写“什么时候用”和“不要什么时候用”。
3. 参数名贴近用户意图，不暴露内部实现细节。
4. 参数约束写进 schema，而不是只写自然语言。
5. 对精确匹配、路径、破坏性操作、并发安全显式标注。
6. 返回结构固定，错误也是结构化结果，不把异常丢给模型猜。

### 4.2 描述应该写什么

优先级：

```text
适用场景 > 参数约束 > 行为边界 > 示例 > 内部实现
```

示例有用，但不能替代 schema。最好的 description 是决策提示：

```text
Use this when you need to search file contents by regex.
Do not use it to read a known file path; use Read instead.
Returns matching file paths and line snippets, capped by limit.
```

### 4.3 工具太多时如何选择

工具太多时，模型失败通常发生在三处：

1. 没有看到最相关工具，因为 schema 太多导致注意力稀释。
2. 看到了工具，但 description 区分度不足。
3. 工具链顺序错，把 edit 放在 inspect 前，或把 Bash 当万能入口。

Claude Code 源码已有两个方向：

1. `ToolSearchTool` / deferred tools：未加载工具不计入 active context，使用后才加载。
2. `isReadOnly`、`isDestructive`、`isConcurrencySafe`：工具 affordance 进入运行时协议。

建议明确给工具打标签：

```yaml
affordance:
  category: search | inspect | edit | verify | memory | orchestration
  safety: read_only | write | destructive | external
  concurrency: safe | exclusive
  context_cost: low | medium | high
  result_shape: summary | artifact | full_text | diff
```

### 4.4 原子工具还是高层复合工具

结论：两层都要有。

| 工具类型 | 适用 | 风险 |
|---|---|---|
| 原子工具 | read、grep、edit、test、apply patch | 调用链长，模型可能跳步。 |
| 复合工具 | diagnose、code_map_search、run_relevant_tests、summarize_tool_output | 黑箱化，错误时难归因。 |

推荐做法：复合工具只封装稳定流程，不封装最终判断。例如：

```text
search_code_map(query) -> 候选模块
inspect_module(paths) -> 结构化文件摘要
run_relevant_tests(paths) -> 测试命令和摘要
```

不要做：

```text
fix_bug_automatically()
```

### 4.5 search -> inspect -> edit -> verify 链条

可以用 runtime policy 强制阶段迁移：

```yaml
required_evidence_before_edit:
  - at_least_one_search_or_known_path
  - at_least_one_file_read
  - edit_target_symbol_or_line
  - validation_plan

required_evidence_before_done:
  - test_or_build_result
  - if_not_run_tests: explicit reason
  - files_changed_summary
```

这比只在 prompt 中写“必须先验证”更可靠。工具调用链失败时，runtime 可以插入结构化反馈：

```yaml
error_code: EDIT_WITHOUT_INSPECTION
message: You attempted to edit before reading the target file.
required_next_action: read_file
```

---

## 5. Skill 太多怎么办

### 5.1 不应全量注入 skill 正文

Skill 全量进入 system prompt 是错误方向。Claude Code 逆向源码的方向更合理：

1. 常驻的是 skill 的索引或 frontmatter 估算。
2. 完整 skill prompt 只在用户显式调用或模型调用 SkillTool 后进入 transcript。
3. `paths` frontmatter 支持条件触发。
4. `user-invocable` 控制是否暴露给用户命令列表。

因此应采用三层加载：

| 层 | 内容 | 是否常驻 |
|---|---|---|
| Skill index | name、description、when_to_use、paths、tags、cost、source、version | 可以常驻或检索。 |
| Skill brief | 触发条件、输入输出、关键步骤、禁止事项 | 命中候选时加载。 |
| Skill full | 完整操作手册、脚本、参考文件 | 触发后加载，用完可摘要。 |

### 5.2 推荐 metadata

```yaml
name: verification-before-completion
description: Require verification before claiming work is complete.
when_to_use: before final response after code changes
trigger:
  explicit_names:
    - verification
  intents:
    - final_claim
    - bugfix_complete
  paths: []
priority: 80
scope: task | session | project
lifetime: until_final | until_phase_change | one_turn
allowed_tools:
  - shell_command
affordance:
  category: verify
  safety: read_only
  context_cost: low
conflicts_with:
  - caveman
requires:
  - current_goal
version: 1
provenance:
  file: ...
```

### 5.3 自动触发还是显式触发

| Skill 类型 | 触发方式 | 例子 |
|---|---|---|
| 安全/权限/验证 | 自动触发 | destructive guard、verification-before-completion。 |
| 领域操作手册 | 条件触发 | pdf、frontend-design、openai-docs。 |
| 用户风格偏好 | 用户显式或项目配置 | caveman、writing style。 |
| 高成本流程 | 显式触发 | research-deep、多 agent benchmark。 |
| 有冲突风险的流程 | 显式触发 | TDD、brainstorming、ship。 |

### 5.4 Skill 选择策略

单一策略不够，应组合：

```text
规则路由：安全、验证、路径触发
关键词/BM25：技能名、用户显式名称、术语命中
Embedding：语义相近任务
模型分类：最后裁决 top-k
Runtime policy：冲突解决和 lifetime 管理
```

当 skill 很多时，先由 runtime 检索 top-k，再让模型在小集合中判断；不要把所有 skill 都塞进 system prompt 让模型自己找。

### 5.5 Skill 何时移除

Skill 被加载后不应永驻 transcript。建议设置 lifetime：

| lifetime | 移除条件 |
|---|---|
| one_turn | 下一轮模型调用后摘要或移除。 |
| until_phase_change | 从 explore 到 edit、edit 到 verify 时移除。 |
| until_final | 最终回复后移除。 |
| session | 仅限低 token、高价值、无冲突的行为规范。 |

保留的是“该 skill 已触发、完成了哪些步骤、剩余要求”，不是全文。

### 5.6 衡量 skill 是否值得常驻

常驻 system prompt 的条件应非常苛刻：

```text
benefit_score = behavior_improvement * trigger_frequency * severity
cost_score = avg_tokens * conflict_rate * attention_penalty
```

只有 `benefit_score / cost_score` 很高，且内容短、稳定、跨任务通用、无冲突时才常驻。大多数 skill 应进入检索层。

---

## 6. 上下文维护方案

### 6.1 推荐上下文层

```text
L0 System invariants
  身份、安全、工具协议、权限边界，极短且稳定。

L1 Project rules
  项目约束、代码风格、重要路径索引。可缓存，可版本化。

L2 Task state
  当前目标、阶段、已做操作、未完成事项、验证状态。

L3 Working memory
  最近几轮原文、当前编辑片段、失败输出、关键工具结果。

L4 Retrieved knowledge
  按需召回的 memory、文档、代码地图、skill brief。

L5 Transcript/event log
  完整事件流，默认不全量进入模型。

L6 Artifacts
  长工具输出、测试日志、搜索结果、报告草稿、截图等。
```

### 6.2 哪些进入 system/user/tool

| 内容 | 放置位置 |
|---|---|
| 不可违反的行为边界 | system |
| 当前用户目标和验收标准 | user/current task |
| 项目规则索引 | system 或 retrieved context |
| 长期知识正文 | tool 按需读取 |
| 工具 schema | API tools，按需 defer |
| 工具结果 | tool_result 摘要 + artifact 引用 |
| task state | runtime 结构化维护，注入为短块 |
| 完整 transcript | event log/session，不默认注入 |

### 6.3 Compact 后必须保留的 task state

```yaml
task_state:
  goal: ...
  user_constraints:
    - ...
  phase: explore | diagnose | edit | verify | wrap
  current_evidence:
    - source: file/tool/test/user
      claim: ...
      provenance: ...
  files_touched:
    - path
      action: read | edited | created
      reason: ...
  decisions:
    - decision
      reason
      alternatives_rejected
  open_questions:
    - ...
  next_steps:
    - ...
  validation:
    commands_run:
      - command
        result: pass | fail | blocked
        excerpt: ...
    remaining_risk: ...
```

这比模型自由总结更可靠，因为它给 compact summary 一个固定 schema。

### 6.4 Memory、Session、Compact、RAG、Code Map 边界

| 机制 | 负责什么 | 不负责什么 |
|---|---|---|
| Session | 保存完整会话和恢复 | 不降低 active context token。 |
| Compact | 压缩当前 transcript | 不保存长期知识，不替代检索。 |
| Memory | 跨会话长期事实和偏好 | 不保存所有对话，不保存可从代码推导的信息。 |
| RAG | 大文档/外部知识按需召回 | 不维护当前任务状态。 |
| Code Map | 代码定位与模块索引 | 不替代读文件，不保证最新细节。 |
| Event log | 原始操作审计和可重放 | 不直接喂给模型。 |

### 6.5 避免 memory 污染

必须引入 provenance 和状态：

```yaml
memory_entry:
  claim: ...
  source:
    type: user | file | test | tool | decision
    ref: ...
  confidence: observed | inferred | user_preference | outdated
  created_at: ...
  last_verified_at: ...
  invalidates:
    - ...
  review_after: ...
```

写 memory 的规则：

1. 只保存决策、偏好、稳定外部事实、项目约束。
2. 不保存可从代码直接推导的内容。
3. 推断必须标记 inferred。
4. 被新事实覆盖时要更新或废弃旧 memory。
5. 每条 memory 要能追溯到原始事件或文件。

### 6.6 Event log 驱动上下文

建议把每次工具调用、文件修改、测试结果写入 event log，再由 event log 生成上下文：

```yaml
event:
  id: ...
  time: ...
  actor: model | user | tool | runtime
  type: tool_call | tool_result | file_edit | test_run | decision | compact
  payload_ref: ...
  summary: ...
  token_cost: ...
  provenance: ...
```

然后 runtime 用投影器生成：

```text
event log -> task_state projection
event log -> compact summary
event log -> validation summary
event log -> memory candidates
event log -> benchmark telemetry
```

这比让模型自己从长 transcript 里总结更可控。

---

## 7. 不同模型的工具遵循差异

### 7.1 差异维度

不同模型的工具能力差异不应只看“是否会调用工具”，而应看：

1. 是否调用：需要查证时是否先查文件。
2. 调用顺序：是否 search -> inspect -> edit -> verify。
3. 参数准确性：路径、正则、old_string、schema 字段是否正确。
4. 错误恢复：工具报错后是否能修正参数。
5. 权限遵循：是否尊重 read-only/destructive/approval。
6. 成本控制：是否过度读文件或过度调用工具。
7. 完成声明：是否验证前就声称完成。
8. compact 后连续性：是否丢失目标、约束、未完成事项。

### 7.2 强推理模型 vs 快模型

强推理模型：

1. 可以给更抽象的 tool description。
2. 更适合多步诊断和冲突证据处理。
3. 但可能用长推理替代工具查证，需要强制 evidence policy。

快模型：

1. 需要更明确的工具 affordance 和示例。
2. 更适合单步 search/read/format/summary。
3. 更容易在工具多时退化，应给更小工具集。

结论：应为不同模型配置不同 tool set、system prompt、skill loading policy。

### 7.3 Benchmark 设计

#### 任务集

| 类别 | 用例 |
|---|---|
| 文件定位 | 给 UI 文案、错误名、命令名，让模型找实现文件。 |
| 精确编辑 | 修改一个函数，要求最小 diff。 |
| Bug 诊断 | 给失败测试和日志，找 root cause。 |
| 工具恢复 | 故意给不存在路径/模糊 old_string，测重试。 |
| 权限遵循 | 尝试 destructive 命令，测是否请求确认。 |
| 验证纪律 | 修改后是否运行正确测试。 |
| Skill 路由 | 多 skill 候选，测是否加载正确 skill。 |
| Compact 连续性 | 中途 compact，测是否保留目标和下一步。 |

#### 指标

```yaml
metrics:
  tool_call_rate_when_needed: 0-1
  tool_order_score: 0-1
  parameter_validity_rate: 0-1
  first_relevant_file_rank: integer
  unnecessary_tool_calls: integer
  tokens_to_success: integer
  edit_correctness: pass | fail
  verification_claim_match: pass | fail
  permission_violation_count: integer
  recovery_success_after_tool_error: 0-1
  compact_continuity_score: 0-1
```

#### 对照实验

```text
A: 全量工具 schema + 全量 skill index
B: deferred tool schema + skill top-k
C: 只有自然语言项目说明
D: 自然语言 + code map
E: 完整 tool result 回填
F: 结构化 tool result 摘要 + artifact 引用
```

预期：

1. `D` 的找文件命中率高于 `C`。
2. `B` 的 token 成本低于 `A`，工具选择不应显著下降。
3. `F` 的 token 成本低于 `E`，但 bug 诊断任务中必须保留失败原文片段，否则 root cause 命中率下降。

---

## 8. 可落地实施方案

### Phase 1：可观测性

1. 在每轮请求前输出 context breakdown：system、tools、MCP、skills、memory、messages、tool_result、attachments。
2. 为每次 tool_result 记录 token、类型、是否被摘要、artifact id。
3. 为每次 edit 前记录 evidence_state。
4. 为每次 final 前记录 verification_state。

验收：

```text
任意一次 agent 任务结束后，可以回答：
- 本次 token 最大头部是谁？
- 读了哪些文件？
- 哪些工具结果被完整保留？
- 哪些被摘要？
- 是否验证后才声明完成？
```

### Phase 2：上下文分层与 task_state

1. 引入结构化 `task_state.yaml` 投影。
2. compact 时优先保留 task_state，而不是只依赖自然语言 summary。
3. 把长工具输出写入 artifact/event log。
4. final answer 从 task_state 和 validation_state 生成。

验收：

```text
手动触发 compact 后，agent 仍能说清：
- 当前目标
- 已改文件
- 为什么这么改
- 剩余验证
- 下一步操作
```

### Phase 3：Tool result 格式治理

1. 所有工具返回统一 envelope。
2. read/search/test 默认摘要化，必要片段原文保留。
3. 长输出写 artifact，tool_result 只给引用和关键摘录。
4. 错误码结构化，给模型明确 next_action。

验收：

```text
同一 bug 修复任务，摘要化结果相比完整结果：
- input token 降低 >= 30%
- 成功率下降 <= 5%
- 失败时能通过 artifact 追溯原文
```

### Phase 4：Skill 按需加载

1. 建 skill index，不注入全文。
2. 使用规则 + BM25/embedding + 模型分类取 top-k。
3. 增加 lifetime 和 conflict metadata。
4. skill 完成后只保留执行状态，不保留全文。

验收：

```text
当 skill 数量从 20 增加到 200：
- system prompt token 不线性增长
- 正确 skill 进入 top-5 的召回率 >= 95%
- 冲突 skill 同时加载率 < 2%
```

### Phase 5：模型工具遵循 benchmark

1. 固定任务集和 repo snapshot。
2. 每个模型跑 30-100 次。
3. 采集工具顺序、参数错误、token、最终测试结果。
4. 输出模型专属 tool policy。

验收：

```text
可以针对每个模型给出：
- 推荐工具集大小
- 是否启用 deferred tools
- 是否需要强制 evidence gate
- schema 描述是否需要示例
- 验证前声明完成的风险等级
```

---

## 9. 最终回答六组问题

### 9.1 Token 经济性

最浪费 token 的是低价值重复历史、长工具输出、未使用工具 schema、skill 全文、重复规则和旧代码片段。真正的大户会随阶段变化：短会话是 system/tools，长会话是 messages/tool_result，工具生态膨胀后是 MCP/tool schema。应使用“原文、摘要、索引、丢弃”四级保留策略，并按任务阶段调节上下文密度。

### 9.2 命中率 / 任务成功率

第一次找对文件最依赖代码库地图、符号索引、错误文本、相关测试路径，而不是长篇自然语言。最小上下文是目标、验收、约束、repo map、最近决策和 search hints。让 agent 不猜的关键是 evidence gate：没有证据不能 edit，没有验证不能 done。

### 9.3 工具调用能力

好工具不是“函数”，而是带 schema、description、affordance、权限、并发、安全、结果映射的协议对象。工具多时要 deferred、搜索、分层加载。工具返回要结构化摘要，并保留 artifact 引用。工具链应由 runtime policy 约束为 search -> inspect -> edit -> verify。

### 9.4 Skill 太多怎么办

不要全量注入 skill 正文。常驻 skill index，候选时加载 brief，触发后加载 full。metadata 必须包含触发条件、适用范围、allowed tools、paths、lifetime、conflicts、context cost。Skill system 的第一目标是行为质量，prompt 成本是第二目标。

### 9.5 上下文维护方案

上下文应分 system、project rules、task state、working memory、retrieved knowledge、transcript/event log、artifacts。Session 负责恢复，Compact 负责压缩 active transcript，Memory 负责长期事实，RAG 负责外部知识，Code Map 负责定位。最佳方案是结构化 event log 生成 task_state，而不是靠模型自由总结全部历史。

### 9.6 不同模型工具遵循差异

差异主要体现在是否调用工具、调用顺序、参数准确性、错误恢复、权限遵循、成本控制、验证纪律。强推理模型适合复杂诊断但仍需 evidence gate；快模型需要更强 schema、较小工具集和更明确错误码。必须通过 benchmark 测量，而不是凭主观体感判断。

---

## 10. 可验证性清单

后续实现或实验时，至少记录这些数据：

```yaml
context:
  system_prompt_tokens:
  tool_schema_tokens:
  mcp_schema_tokens:
  skill_index_tokens:
  memory_index_tokens:
  message_tokens:
  tool_result_tokens:
  attachment_tokens:

task:
  phase:
  first_relevant_file_rank:
  files_read:
  files_edited:
  unnecessary_reads:
  evidence_before_edit: true | false
  validation_before_done: true | false

tooling:
  tool_call_sequence:
  invalid_tool_calls:
  parameter_errors:
  permission_blocks:
  recovery_success:

compact:
  compact_trigger:
  tokens_before:
  tokens_after:
  task_state_preserved: true | false
  tool_pairing_preserved: true | false

skills:
  total_skills:
  indexed_skills:
  loaded_skills:
  correct_skill_loaded:
  conflict_detected:
```

只有这些指标存在，才能把“更省 token”“更高命中率”“工具遵循更好”从感觉变成可验证工程结论。
