# 第23章 自动化与 Dream 模式

Claude Code 的自动化不是另起一个调度器。它更像是在每一轮 agent 执行的生命周期边界上，挂了一组受限、可恢复、低打扰的后台维护任务。

理解这一点，比记住某个功能名更重要。Dream 模式也不是神秘能力，而是长期记忆治理的一种低频整理机制。

---

## 23.1 自动化哲学：简单循环，重型 harness

Claude Code 的核心执行形态是一个 agentic query loop。用户输入之后，系统装配上下文，调用模型，捕获 tool use，经过权限门，执行工具，把结果写回消息，再继续下一轮。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在 Section 4 用"修复一个失败测试"作为 running example，拆解了一轮请求的完整路径。

这篇论文的背景是：作者想说明 Claude Code 的能力不是来自复杂的显式规划图，而是来自一个简单循环外部的运行时基础设施。

它的结论是：Claude Code 选择了 reactive loop，而不是树搜索或复杂图编排。这个设计牺牲了一部分全局搜索能力，换来低延迟、可流式输出、可恢复和较低控制复杂度。

论文给出的依据是 query pipeline 的固定序列：settings resolution、mutable state initialization、context assembly、pre-model context shapers、model call、tool dispatch、permission gate、tool result collection、stop condition。

由此可以推断：Claude Code 的自动化应该挂在这些稳定边界上，而不是散落在 UI 事件里。只有生命周期边界稳定，后台任务才不会和主交互抢状态。

《Agent Harness Engineering: A Survey》从更大的领域角度得出相同方向的结论。它提出 binding-constraint thesis：长任务 agent 的可靠性受限于 harness，而不只是模型。

这篇 survey 的背景是：研究界过去更关注模型能力，而生产实践发现，同一个模型在不同工具、上下文、权限、恢复和验证基础设施下表现差异很大。

它引用的结果包括：仅改变 edit-tool 格式和 harness 可带来最高 10 倍 benchmark 提升；固定模型通过 prompt 重构、middleware context injection 和 self-verification hooks，可在 Terminal-Bench 2.0 上提升 13.7 个百分点。

因此，它的结论是：真实 agent 系统要工程化的不是一条 prompt，而是执行环境、工具接口、上下文、生命周期、可观测性、验证和治理。

对 Claude Code 使用者来说，这个结论可以转成一句话：不要只问"怎么让模型更聪明"，要问"怎么让流程可恢复、可验证、可重复执行"。

---

## 23.2 Query 管道：自动化发生在哪里

一轮 query 的主路径可以简化成：

```text
用户输入
  -> 装配上下文
  -> 调用模型
  -> 捕获 tool_use
  -> 权限判断
  -> 执行工具
  -> 写入 tool_result
  -> 继续或停止
```

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》指出，模型看到的不是原始 transcript，而是经过多层处理的上下文。

在模型调用前，系统会执行 budget reduction、snip、microcompact、context collapse 和 auto-compact 等 pre-model context shapers。它们从轻到重，逐步减少上下文压力。

这个结论的依据是论文对 `query.ts` 的源码级拆解。每个 shaper 在模型调用前运行，较轻的处理先执行，只有压力仍然过高时才触发更重的 compact。

由此可以推断：自动化不应该随意改写历史消息。更稳的做法是把维护任务放在模型调用前后明确边界，让它们和 compaction、memory、permission 共用同一套运行时约束。

---

## 23.3 Stop-Time 自动化：一轮结束后的维护窗口

第 11 份自动化系统分析报告把 Claude Code 的后台自动化概括为 stop-time maintenance agents。

也就是说，一轮 query 完成后，系统进入 stop boundary。在这个稳定时刻，运行时可以做几类维护工作：提示下一步、抽取记忆、整理长期记忆、执行用户 Stop hooks、触发协作相关 hooks。

这个设计有三个好处。

第一，stop boundary 的上下文稳定。模型回复已经完整生成，工具结果也已经回写，后台任务不需要猜主流程是否还会继续改变状态。

第二，主交互不会被内部维护任务阻塞。Prompt suggestion、Extract Memories 和 AutoDream 都是 fire-and-forget；失败只写日志，不应该打断用户。

第三，用户 hooks 仍然保留可阻塞语义。内部维护任务可以后台跑，但用户配置的 Stop hooks 代表外部集成边界，应该能返回错误并阻止 continuation。

可以把 stop-time 自动化理解成：

```text
query() 完成
  -> 保存 cache-safe fork context
  -> 后台提示建议
  -> Extract Memories
  -> AutoDream
  -> 用户 Stop hooks
  -> teammate hooks
```

关键点是：这些不是第二套 runtime。Extract Memories 和 AutoDream 都复用 forked agent、query loop、工具系统和权限策略，只是换成更受限的 `canUseTool`。

---

## 23.4 Extract Memories、AutoDream 与 Away Summary

这三个功能容易混在一起，但职责完全不同。

| 机制 | 触发点 | 写入对象 | 目的 |
| --- | --- | --- | --- |
| Extract Memories | stop boundary | auto memory files / `MEMORY.md` | 把本轮对话中长期有价值的事实写入记忆 |
| AutoDream | stop boundary 后经过门控 | consolidated memory files | 低频整理碎片化长期记忆 |
| Away Summary | 终端失焦一段时间 | system message | 帮用户回来时快速恢复注意力 |

Extract Memories 是高频但受限的维护任务。它有 feature gate、GrowthBook gate、autoMemoryEnabled、remote mode、subagent id、cursor、in-progress 状态等多层约束。

它找不到 cursor 时会选择 fail-open：重新统计所有 model-visible messages。原因是记忆抽取是补充性写入，漏掉重要事实比重复扫描更糟。

AutoDream 是低频 consolidation pipeline。它不是每轮都运行，而是经过时间门、扫描节流、session 数量门和 `.consolidate-lock` 互斥锁。

`.consolidate-lock` 同时表达两件事：上次整理是什么时候，以及当前是否已有进程在整理。这个设计避免了为长期记忆整理引入额外数据库。

Away Summary 则更克制。它不是 memory，不写长期文件，不使用工具。终端失焦约 5 分钟且没有 turn in progress 时，它用小模型生成 1 到 3 句短摘要，帮助用户回来后恢复上下文。

因此，Dream 模式的本质不是"模型睡觉后更聪明"，而是：

```text
低频触发
  + 受限 forked agent
  + 读取 memory / logs / sessions
  + 整理 Markdown memory
  + task UI 可见、可中止
```

---

## 23.5 恢复机制：失败不是立刻停机

自动化系统要可靠，不能只处理成功路径。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在 Section 4.4 分析了 query loop 的恢复机制。

它的背景是：一个 coding agent 会遇到长输出、上下文溢出、流式 API 问题、模型失败和工具失败。每一种都不应该直接把任务推给用户。

论文总结的恢复机制包括：max output tokens escalation、reactive compaction、prompt-too-long handling、streaming fallback 和 fallback model。

这个结论的依据是 query loop 中的多个 transition path。系统在不同错误上替换 State，再重新进入循环，而不是用递归或一次性异常终止。

由此可以推断：Claude Code 的自动化不是"永不失败"，而是"可恢复的失败优先自动恢复，不可恢复的失败才暴露给人"。

对使用者来说，重要的是区分两种失败。

一种是运行时可恢复失败，比如输出被截断、上下文接近上限、API 临时错误。系统会尝试自动恢复。

另一种是语义失败，比如修错方向、测试没覆盖、误读需求。系统无法靠 retry 自动解决，必须依赖验证标准、人工审查或 fresh reviewer。

---

## 23.6 停止条件：什么时候该停，什么时候该继续

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在 Section 4.5 给出 query loop 的停止条件：无 tool use、达到 max turns、上下文溢出、hook 介入、显式 abort。

这个结论的背景是：agent loop 如果没有清晰停止条件，就会进入无限探索、重复调用工具或不断自我修复。

它的依据是 query loop 的 terminal branch。模型不再产生 tool use 时，turn 自然完成；max turns 和 abort 是外部边界；hooks 则允许用户策略介入。

由此可以推断：自动化任务必须同时有自然完成条件和硬边界。只靠"模型觉得完成了"不够，只靠 max turns 也不够。

Extract Memories 使用 `maxTurns: 5`，但还叠加 feature gate、cursor、inProgress、main-agent-write skip 和受限工具权限。

AutoDream 没有只依赖一个数字，而是叠加 minHours、minSessions、scan throttle、process lock、DreamTask abort 和 memory-dir 权限。

这给后台任务设计提供了一个通用原则：防 runaway 要靠入口频率、并发控制、上下文范围、工具权限和用户可中止性共同约束。

---

## 23.7 从交互式验证到后台跑一千次

自动化不是一开始就全自动。更稳的路径是"校准 -> 批量 -> 全自动"。

《Claude Code 实战方法论》把这个过程拆成三阶段。

第一阶段是校准期。你做一个，AI 做一个，然后对比、纠正、补 SOP。这个阶段消耗人的时间，但建立流程信任。

第二阶段是过渡期。AI 按流程做，你抽查关键节点。人的角色从执行者变成校验者。

第三阶段是放手期。前面 30 到 60 个样本验证通过后，把同一流程交给后台任务批量跑。

这个方法论和《Agent Harness Engineering: A Survey》的 harness 观点一致。后者强调，可靠性来自环境、约束、反馈、验证和恢复机制，而不是模型一次性聪明。

因此，不要问"这一轮能不能生成正确代码"，要问：

- 这个流程能不能重复跑 100 次
- 每次是否有明确输入、输出和验收标准
- 失败时能否定位在提示、工具、权限、上下文还是验证
- 验证通过后能否后台执行

当这些条件成立，`claude --bg` 或后台 agent 才有意义。否则，后台运行只会把错误放大。

---

## 23.8 自动化的设计原则

第一，后台任务要挂在生命周期边界上。不要让自动化散落在 UI 事件、计时器和临时脚本里。

第二，后台 agent 可以复用主 runtime，但必须换权限策略。Extract Memories 和 AutoDream 复用 query loop，但只能读受限文件、执行 read-only Bash、写 memory 目录。

第三，内部维护任务不应阻塞主交互。用户 hooks 可以阻塞，runtime housekeeping 不应该阻塞。

第四，记忆系统要有索引语义。`MEMORY.md` 应该是入口和目录，不应该变成所有事实堆积的垃圾桶。

第五，不是所有摘要都应该进入长期记忆。Away Summary 只是恢复注意力的 UX，不应污染长期 memory。

第六，Dream 的核心是 consolidation。它解决的是长期记忆碎片化，不是替代验证、测试或人工判断。

---

## 本章引用的论文与材料

- 《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》：用于解释 agentic query loop、pre-model context shapers、恢复机制和停止条件。
- 《Agent Harness Engineering: A Survey》：用于解释 harness over model、生命周期与编排层，以及为什么长任务可靠性依赖状态、恢复、验证和控制边界。
- 《11 Automation System and Dream Mode》分析报告：用于整理 Extract Memories、AutoDream、SessionMemory、Away Summary 和 stop-time 自动化路径。
- 《Claude Code 实战方法论》：用于补充后台运行、校准到放手、批量执行的实操方法。
