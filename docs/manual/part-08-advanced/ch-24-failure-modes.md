# 第24章 常见失败模式与修复

Claude Code 的失败不一定来自模型不聪明。很多失败来自上下文、权限、工具、验证和工作流边界设计不当。

这章的目标不是列一堆报错，而是给你一套诊断顺序：先判断失败属于哪一层，再选择修复手段。

---

## 24.1 先把失败看成 harness 问题

《Agent Harness Engineering: A Survey》提出一个核心判断：长任务 agent 的可靠性，很多时候受限于 harness，而不是模型本身。

这篇论文的背景是：近年来 agent 研究大量关注模型能否规划、调用工具和记忆，但生产实践发现，不改模型、只改工具接口、上下文注入、验证 hook 和运行时约束，也能显著提升 benchmark 表现。

它的结论是：执行环境、工具接口、上下文与记忆、生命周期、可观测性、验证、治理这七层共同决定 agent 可靠性。

这个结论的依据包括多个 harness-only 提升案例：改 edit-tool 格式可带来最高 10 倍收益；固定模型通过 harness 调整在 Terminal-Bench 2.0 上提升 13.7 个百分点。

由此可以推断：遇到失败时，不要第一反应就是"换更强模型"。更应该问：上下文是否正确、工具是否可用、权限是否阻断、验证是否缺失、状态是否丢失。

---

## 24.2 上下文相关失败

### 上下文溢出

症状是模型开始遗忘早期约束、重复读文件、回答变短，或者直接触发 prompt-too-long。

修复方式是先用 `/compact` 或 auto-compact，让旧对话变成摘要。如果任务已经结束一个阶段，优先 `/clear`，不要把多个任务硬塞进同一会话。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》分析了 Claude Code 的五层 compaction pipeline：tool result budget、snip、microcompact、context collapse、auto-compact。

它的背景是：Claude Code 需要在长任务中保留足够状态，但模型上下文窗口有限，直接丢历史会破坏连续性。

论文的结论是：Claude Code 采用渐进压缩，而不是单次截断。先用轻量方式减少工具结果和历史，再在必要时生成完整摘要。

推论是：压缩不是免费操作。越重的压缩越可能损失细节。关键决策、失败测试输出、用户最新要求应尽量保持原文，其他长日志可以摘要。

### 上下文碎片化

上下文碎片化常见于"浅模块"系统：很多文件都知道一点点业务逻辑，模型必须读大量文件才能理解一个改动。

修复方式不是不断扩大上下文，而是重构为更深模块：让一个模块承担完整责任，减少跨文件跳转。

《Agent Harness Engineering: A Survey》在上下文工程章节指出，给模型太少信息会让它无法行动，给太多信息又会让性能下降。工程目标是每一步给最小高信号 token 集。

这个结论的依据包括 long-context 研究：相关信息放在上下文中间时，准确率会明显下降；上下文越长，模型越容易出现 context rot。

推论是：代码库结构本身会影响 AI 成功率。可定位、边界清晰的深模块，比大量浅文件更适合 agent 工作。

### `/compact` 后指令丢失

`/compact` 会把旧对话变成摘要，但不保证保留所有细节。尤其是你在对话中临时说过的规则，可能被摘要弱化。

修复方式是把长期规则写进 CLAUDE.md、settings、hooks 或明确计划文件。临时口头规则只适合当前短任务。

如果 compact 后模型忘了子目录规则，要求它重新读取对应目录下的 CLAUDE.md 或规则文件。不要假设所有目录级约定都会永远留在活跃上下文中。

### 对话历史过长

如果同一个会话里已经混入很多任务、二十多轮以上来回纠正、上下文里有大量不再相关的探索，最好的修复往往是 `/clear`。

不要把 `/compact` 当成万能修复。compact 适合保留一个任务的连续性；`/clear` 适合切断旧任务污染。

---

## 24.3 权限与审批相关失败

### 反复被 deny

如果一个正常操作反复被拒绝，先检查 deny rules 是否过于宽泛。不要用 allow 去覆盖 deny。

第20章已经讲过，Claude Code 是 deny-first。deny 优先于 allow，是安全系统的底线。

修复方式是缩小 deny 规则范围，或者把某类安全操作加入更精确的 allowlist。

### Auto 模式连续失败

Auto 模式不是"完全自动"。它只是让分类器和权限策略在常规场景中减少人工确认。

如果 Auto 模式连续拒绝或请求人工审批，说明当前任务超出了它的信任范围。修复方式是暂停 Auto，回到 default 或 plan，把步骤拆清楚。

### 分类器缺少上下文

自动审批依赖上下文。如果分类器不知道某个命令是项目标准构建命令，它可能要求人工确认。

修复方式是把可信基础设施写进 CLAUDE.md、permissions allowlist 或项目脚本。例如把 `npm test`、`npm run typecheck`、只读诊断命令固定下来。

### 大量子命令退化

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在 Section 11.3 讨论安全与性能的结构性张力。

它的背景是：多层安全设计依赖各层独立工作，但极端负载下，细粒度检查会带来性能压力。

论文提到一个关键现象：当命令中包含超过 50 个 subcommands 时，系统可能因性能限制退回到单一通用审批提示，而不是逐个执行 deny-rule 检查。

这个结论说明，防御层并非永远独立。性能压力可能让多个安全层同时退化。

推论是：不要把高风险操作包装成巨大复合命令。分批执行，保持权限系统的精细判断能力。

---

## 24.4 执行相关失败

### 死循环

症状是模型反复读同一批文件、重复运行同一命令、一直说"我再检查一下"。

先检查是否设置了 max turns、是否有 Stop hook 强制 continuation、是否有 reactive compact 后继续丢失同一关键信息。

修复方式是中断当前轮次，明确写出停止条件。例如"只检查这三个文件，给出结论，不要修改代码"。

### API 重试失败

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》指出，query loop 有多种恢复机制，包括输出 token 上限提升、reactive compaction、prompt-too-long handling、streaming fallback 和 fallback model。

背景是 agent 执行不是一次 API 调用，而是多轮状态机。失败可能发生在模型输出、上下文大小、流式传输或工具阶段。

结论是：运行时会自动恢复可恢复错误，但恢复有边界。多次恢复失败后，系统应终止并把控制权还给用户。

推论是：如果 API 重试失败，不要继续让同一会话硬跑。保存当前目标、已知状态和失败证据，然后 `/clear` 或恢复到检查点。

### 大文件编辑失败

大文件编辑失败常见原因是行号或上下文片段已经变化。模型基于旧片段发起 edit，工具无法定位。

修复方式是重新读取目标片段，缩小编辑范围，优先用结构化 patch，避免让模型一次改几百行。

如果文件很大，先要求模型提出改动计划，再分块执行。每一块改完后运行局部验证。

### 工具调用失败

工具调用失败通常来自 schema 错误、参数缺失、路径错误或权限不匹配。

修复方式是让模型复述工具参数，检查 schema，再重试。不要把工具错误当成业务错误。

---

## 24.5 架构性失败模式

### 厨房水槽会话

一个会话里同时做需求分析、修 bug、重构、写文档、问原理，最后上下文变成垃圾桶。

修复方式是按任务切会话。探索、实现、审查、总结可以是不同会话，必要时用文件保存交接状态。

### 反复纠正

如果同一问题纠正两次以上，继续纠正通常收益很低。当前上下文里已经充满失败路径和错误假设。

修复方式是 `/clear`，用更好的提示重新开始。新提示要包含目标、约束、已知失败、验证标准和禁止路径。

### CLAUDE.md 过度膨胀

CLAUDE.md 不是项目百科，也不是 API 文档。它应该只放 Claude Code 无法从代码中猜到、且长期稳定的规则。

官方最佳实践建议目标控制在 200 行以内。已经被模型稳定正确执行的事项，可以删除或转成 hook。

### Doc Rot

过期文档会误导 agent，比没有文档更危险。尤其是迁移中的项目，旧 README、旧架构图和旧 PRD 很容易让模型走错方向。

修复方式是标注文档状态，删除过期内容，或在 CLAUDE.md 中明确"以代码和测试为准，旧文档仅供参考"。

---

## 24.6 信任与验证相关失败

最危险的失败不是命令报错，而是 Claude Code 声称成功但实际失败。

官方最佳实践的核心原则是：让 Claude 显示证据，而不是只说完成。证据可以是测试输出、命令返回码、截图、构建日志或 diff 摘要。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在未来方向中讨论 silent failure 和 observability-evaluation gap。

它的背景是：已部署 agent 的主要风险不一定是崩溃，而是静默错误。系统表面顺利运行，但任务质量、验证覆盖或业务语义已经偏离。

论文的结论是：关闭这个 gap 不能只靠更强模型，还需要额外 harness scaffolding，例如 generator-evaluator separation、sprint contracts 和 post-hoc checks。

《Agent Harness Engineering: A Survey》也把 Observability and Operations 提升为一等层。它认为可观测性不只是日志，而是记录 LLM call、工具调用、检索步骤、上下文装配、成本、延迟和失败轨迹。

由此可以推断：你不能只看最终回答，要看产生回答的轨迹。尤其是代码任务，必须把验证标准放进提示里，并要求模型运行验证。

实操规则：

- 提示中写明验收命令
- 要求展示关键测试输出
- UI 任务要求截图或视觉对比
- 修 bug 时先写失败测试或复现步骤
- 发现 QA 问题时丢回 issue 或看板，不要在口头上下文里悄悄修

---

## 24.7 性能与成本相关失败

### Prompt cache miss

Agent 任务常常很贵，不只是因为模型输出多，而是因为每轮都会带上 system prompt、工具 schema、上下文和历史。

《Agent Harness Engineering: A Survey》在上下文工程章节引用 Manus 团队的经验：KV-cache hit rate 是生产 agent 最重要的指标之一。

这个结论的背景是：缓存 token 比非缓存 token 便宜很多，但缓存命中依赖稳定前缀。

推论是：不要频繁改变 system prompt、工具列表和前缀结构。插件、工具、输出风格和动态注入如果放在前缀早期，会放大 cache miss。

### 上下文膨胀

上下文膨胀常见来源包括：长工具输出、重复注入规则、全量 skill 正文、大量无关 MCP schema、子代理返回过长报告。

修复方式是按层保留信息：当前编辑片段和失败输出原文保留；已读大文件摘要保留；长期知识保留索引；无关工具输出丢弃。

### 子代理 token 膨胀

子代理可以隔离探索，但不是免费。每个子代理都有自己的上下文窗口、工具池和总结输出。

适合用子代理的场景是并行调查、独立审查和不希望污染主上下文的深探索。不适合把每个小问题都扔给子代理。

---

## 24.8 诊断工具与调试方法

遇到失败时，先问四个问题：

```text
目标是否明确？
上下文是否污染？
工具和权限是否阻断？
验证证据是否存在？
```

常用诊断手段包括：

| 工具 / 方法 | 用途 |
| --- | --- |
| `/context` | 查看当前上下文和消息结构 |
| `/memory` | 验证 CLAUDE.md 或 memory 是否加载 |
| `/clear` | 切断污染上下文，重新开始 |
| `/compact <instructions>` | 带指令压缩当前会话 |
| `--debug` / `--debug-file` | 查看调试输出 |
| `claude logs <id>` | 查看后台会话日志 |
| fresh reviewer / subagent | 用新鲜上下文审查当前 diff |

如果你不知道失败在哪一层，优先用 fresh reviewer。让一个新上下文只看目标、计划、diff 和验证输出，通常能更快暴露偏差。

---

## 本章引用的论文与材料

- 《Agent Harness Engineering: A Survey》：用于解释 harness over model、上下文工程、context drift、可观测性、可靠性工程和成本优化。
- 《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》：用于解释 compaction pipeline、恢复机制、安全与性能的结构性张力、silent failure 与 observability-evaluation gap。
- Claude Code 官方最佳实践：用于整理 `/clear`、`/compact`、验证驱动、CLAUDE.md 修剪和常见失败模式。
- 《权限模式与决策链》分析报告：用于补充 deny-first、权限模式、自动模式和工具执行前的多层决策链。
