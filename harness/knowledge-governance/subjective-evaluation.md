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

### Collect decisions, not just reference material

“收集资料”是必要的，但它通常不足以产出评审标准。资料告诉我们**应该知道什么**；一次真实评审中的取舍，才会揭示专家实际上**如何判断**。第一批可沉淀的原料应来自：

- AI 的初稿、人的修改稿和最终发布稿；
- 被接受或被驳回的具体变更；
- 用户反馈、线上事故、支持工单和反复出现的误解；
- 审核时引用的代码、测试、发布说明、产品策略等证据；
- 专家认为“看起来没错、但不能这样写”的反例。

一个好案例不是长篇聊天记录，而是一个可重放的判断单元：给定任务、输入、修改结果和证据，后来的人或 agent 能理解为什么这里应当通过、修改或升级。原始 session、会议记录和外部资料应保留为 Evidence；真正进入长期规则库的是经过确认的结论和它的适用边界。

### Interview the work at the moment of correction

在 AI-in-the-loop 阶段，最有价值的时刻是专家刚刚改完一处内容时。此时只需用极低摩擦的方式补一个短注释，例如：

```text
DOC-021 | boundary | blocking
把“支持导入”改为“仅支持 CSV 导入”。
依据：当前版本的 API 和发布说明。
风险：用户会按不存在的能力设计迁移方案。
```

不要求专家立刻写完整规范。后续可以由 agent 汇总这些短注释、提出候选规则；规则是否成立、范围多大，仍由负责人确认。这样既不打断工作心流，也不会让复盘只能依赖逐渐过期的上下文窗口。

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

### What makes an early case high-value

优先选择能暴露不同判断方式的案例，而不是十个相似的拼写错误：

| 优先级 | 案例特征 | 为什么值得保留 |
| --- | --- | --- |
| 高 | 会让用户做出错误操作或错误决策 | 能定义 blocking 风险 |
| 高 | 多位审核者可能有不同结论 | 能暴露规则歧义，值得校准 |
| 高 | 需要结合多个证据源 | 能定义检索和验证路径 |
| 中 | 高频、格式稳定、可重复 | 候选自动化收益高 |
| 低 | 一次性的措辞偏好 | 先作为偏好，不急于做 CI |

不要只保存“正确答案”。也保存接近但不合格的答案，以及一个看似类似却应该放行的例外。它们能防止后续 AI 将规则过度泛化。

## Keep Rubrics Small and Observable

产品或技术文档可以从四个维度开始：

| 维度 | 核心问题 |
| --- | --- |
| Factual accuracy | 内容能否被代码、测试或正式资料支持？ |
| Task completeness | 用户完成目标所需的信息是否齐全？ |
| Boundary accuracy | 限制、权限、适用范围和前提是否讲清楚？ |
| Communication quality | 目标读者能否理解并完成操作？ |

每个维度优先使用三种结果：通过、需要修改、无法判断。避免一开始就采用精细百分制，因为精确分数很容易掩盖依据不足的问题。

### A score is a routing signal, not truth

对主观内容，单一总分常常造成错误的确定感。更实用的输出是“各维度结论 + 可引用证据 + 下一步路由”：

| 结果 | 含义 | 默认动作 |
| --- | --- | --- |
| Pass | 有足够证据满足规则 | 自动继续，仍保留抽检 |
| Revise | 发现了可说明的问题 | 返回作者或 agent 修订 |
| Unknown | 证据不足、规则冲突或超出能力边界 | 升级给人，不猜测 |
| Block | 触及已定义的高风险条件 | 阻止发布并要求审批 |

只有当每个维度的证据、错误代价和处理方式都不同，才需要把它们合成分数。例如：事实准确性可以设为 blocking；表达质量只用于建议或排序。若必须量化，应先记录 false accept（错误放行）和 false reject（错误拦截），再讨论阈值，而不是只追求“平均分看起来合理”。

有些审美或策略问题难以原子化成 checklist。此时可以使用成对比较：在相同任务下比较 A、B 两个版本，要求评审说明哪个更符合目标、证据是什么。累积足够对比后，再尝试提炼维度；不要假装一开始就存在客观的 83 分标准。

## Different Document Types Need Different Criteria

教程、操作指南、参考手册和概念解释服务不同读者需求。公共规则可以共享，但每种文档仍需专属标准：

- Tutorial：学习路径是否连贯；
- How-to：用户能否完成明确任务；
- Reference：信息是否准确、完整、易查找；
- Explanation：是否解释清楚原因、关系和背景。

同一份“文档好不好”的评价，在不同类型中含义完全不同。比如 Reference 的首要风险是漏项或与实现不符；How-to 的首要风险是用户按步骤做不成；Explanation 则允许不覆盖所有参数，但必须帮助读者形成正确心智模型。因此，公共 rubric 应只放公共维度，类型专属检查应放在相应的 task packet 或 Skill 中。

## Build a Calibrated Review Loop

### Write a review contract before automating judgment

让 AI 审查之前，先明确它必须交付什么。一个可审计的 review contract 可以是：

```text
任务：评估此操作指南是否可安全发布。
只根据指定的代码、测试、正式发布说明和产品策略判断；不要用常识补全事实。

对每个维度输出：Pass / Revise / Unknown / Block、证据链接或摘录、
问题位置、风险解释和建议动作。

没有证据时必须输出 Unknown；不要因为文风流畅而推断产品能力。
事实、权限、兼容性和数据安全问题为 blocking；措辞建议为 advisory。
```

这比“帮我 review 一下”可靠得多：它限制了证据来源，强制暴露不确定性，并把主观意见与发布风险分开。

### Calibrate against a small golden set

建立 Golden Set 的目的不是训练模型，而是检验“这套评审语言是否真的表达了专家标准”。每个样本至少包括任务、候选输出、允许使用的证据、专家结论和理由。运行 AI 评审后，观察四件事：

1. 它是否发现了专家认为必须发现的问题（召回）；
2. 它的通过结论中，有多少被专家判为错误放行（precision / false accept）；
3. 它是否把合理例外误报成问题（false reject）；
4. 它给出的理由是否真的引用了正确证据，而非事后编造解释。

当人和 AI 的结论不一致时，首先检查 rubric 与证据包是否含糊，而不是直接认定 AI 或专家“错了”。分歧通常意味着四种情况之一：规则缺失、规则范围不清、例外未写出、或任务本身需要产品决策。前两类可以改规则；后两类应继续交给人。

### Separate author, verifier, and approver

同一个模型或同一段推理链既生成又验证时，容易重复自己的盲点。更稳妥的结构是：

```text
author agent creates a change
        ↓
independent verifier receives the change, rubric, and evidence package
        ↓
human approver handles Unknown, Block, and sampled Pass cases
```

独立不必意味着昂贵的第二个模型；关键是让 verifier 从干净任务描述、代码和证据重新判断，而不是读取作者的自我辩护。对高风险变更，还应要求 verifier 给出可复现的检查步骤或最小反例。

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

### Promote rules gradually

一条候选规则不要直接变成硬门禁。推荐的演进路径是：

```text
review note
→ reviewed case
→ advisory AI check
→ calibrated rubric
→ deterministic check or CI gate (when possible)
→ periodic audit and exception review
```

把高频、证据明确、错误成本高且可重放的判断优先自动化。把低频、强策略、强语境或法规边界不清的判断保留为人工 gate。真正的目标不是“所有内容都由 agent loop”，而是在重复工作上减少人力，同时把人集中在最值得其专业判断的地方。

## A Lightweight Operating Rhythm

每周或每个发布周期做一次短复盘即可：

1. 选出本周期最有代表性的 3～5 个审核决定；
2. 合并重复问题，标记哪些是事实、流程、边界或表达问题；
3. 为其中一两个问题写出候选规则和反例；
4. 用旧案例回放新规则，确认没有明显误伤；
5. 决定其仍是案例、成为 advisory，还是满足进入 CI 的条件。

这样，评审标准不是一次性设计出的“完美评分表”，而是从业务实践中逐步长出来的可校准系统。

## Sources

- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [How evals drive the next chapter in AI for businesses](https://openai.com/index/evals-drive-next-chapter-of-ai/)
- [Critical decision method for eliciting knowledge](https://doi.org/10.1109/21.31053)
- [Diátaxis](https://diataxis.fr/)
