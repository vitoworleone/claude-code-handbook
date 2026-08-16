# Subjective Evaluation

> 主观内容的 CI，不是让 AI 代替专家判断，而是把专家反复使用的判断方法逐步沉淀下来。

AI 给出的分数不是事实，也不应被当作最终质量结论。它更适合作为分流信号：决定内容可以继续流转、需要修改，还是必须交给人审核。

## Start with Human-in-the-Loop

第一阶段不应追求完整自动化，而应观察人如何修正 AI：

- 人修改了什么？
- 为什么修改？
- 哪些错误反复出现？
- 什么情况必须找领域专家？
- 哪些内容没有明显事实错误，但仍然“不够好”？

要沉淀的不是大量资料，而是判断：

```text
案例
→ 人作出的决定
→ 作出决定的原因
→ 可以复用的规则
```

## Use Cases to Extract Tacit Knowledge

不要只问专家“你的标准是什么”。更有效的做法是复盘具体事件：

- 当时首先注意到了什么？
- 哪个信号让你觉得有问题？
- 如果不修改，会造成什么后果？
- 新手为什么可能看不出来？
- 有没有表面相似但不需要修改的例外？

这能把专家的隐性敏感度逐步拆成可解释的信号、风险和边界。

## Review Case Template

```md
## Review Case

- Document type:
- User task:
- Original content:
- Revised content:
- Decision:
- Key signal:
- Risk if unchanged:
- Evidence used:
- Candidate reusable rule:
- Exceptions:
- Severity: blocking / advisory
- Routing: deterministic check / AI review / human review
```

第一版只需要 5～10 个判断含量高的案例，而不是一套庞大的 benchmark。

## Keep Rubrics Small and Observable

产品或技术文档可以从四个维度开始：

| 维度 | 核心问题 |
| --- | --- |
| Factual accuracy | 内容能否被代码、测试或正式资料支持？ |
| Task completeness | 用户完成目标所需的信息是否齐全？ |
| Boundary accuracy | 限制、权限、适用范围和前提是否讲清楚？ |
| Communication quality | 目标读者能否理解并完成操作？ |

每个维度优先使用三种结果：通过、需要修改、无法判断。避免一开始就采用精细百分制，因为精确分数很容易掩盖依据不足的问题。

## Different Document Types Need Different Criteria

教程、操作指南、参考手册和概念解释服务不同读者需求。公共规则可以共享，但每种文档仍需专属标准：

- Tutorial：学习路径是否连贯；
- How-to：用户能否完成明确任务；
- Reference：信息是否准确、完整、易查找；
- Explanation：是否解释清楚原因、关系和背景。

## From Cases to CI

```text
AI 生成或更新内容
→ 人工审核并记录原因
→ 聚类反复出现的问题
→ 提取候选规则与典型案例
→ AI 按规则初筛
→ 人工复核不确定项并抽样检查
→ 稳定规则进入 CI
```

不是所有工作都值得建立 loop。简单低频任务可以直接人工审核；高频、重复、规则逐渐稳定的任务适合自动化；高风险、强业务判断仍需要人审批。

## Sources

- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [How evals drive the next chapter in AI for businesses](https://openai.com/index/evals-drive-next-chapter-of-ai/)
- [Critical decision method for eliciting knowledge](https://doi.org/10.1109/21.31053)
- [Diátaxis](https://diataxis.fr/)
