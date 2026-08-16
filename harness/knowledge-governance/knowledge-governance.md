# Knowledge Governance

> 文档、规则、案例与 Skills 的目标不是“越多越好”，而是让每一条知识有明确身份、唯一来源、适用范围和退出机制。

## The Architecture

```text
Facts, policies, decisions, and cases
        ↓
Canonical knowledge sources
        ↓
Task-specific distilled views
        ↓
AGENTS.md / CLAUDE.md / path rules / skills
        ↓
scripts / lint / tests / CI / human approval
```

一条规则只能在权威来源中被定义一次。面向 AI 的文件不是规范的复制品，而是为特定任务蒸馏出的最小工作上下文。

## The Five Fates of a Document

每一份资料都应被归入以下之一：

| Fate | Purpose | Default behavior |
| --- | --- | --- |
| Canonical | 当前事实或规范的权威来源 | 可被任务引用 |
| Derived | 为 agent 或 CI 生成的视图 | 可重新生成，不手改 |
| Evidence | 支撑事实、规则或决策的材料 | 按需读取 |
| Temporary | 当前任务的草稿、候选项或执行计划 | 结束后晋升或淘汰 |
| Archive | 历史追溯材料 | 默认不加载 |

如果一份文档无法归类，要么需要拆分，要么不应进入长期知识库。

## A Map, Not an Encyclopedia

根 `AGENTS.md` 或 `CLAUDE.md` 应该是地图，而不是千页手册。它只回答：

- 这个仓库是什么；
- 当前任务应先看哪里；
- 哪些来源是权威的；
- 哪些风险必须验证；
- 不确定时应查什么或升级给谁。

局部规则应按目录或文件类型加载；专门工作流应放进 Skills；长背景和案例应留在可按需检索的 references 中。这样 AI 获得的是任务包，而不是知识库倾倒。

### Treat context as a compiled task packet

模型不会像熟练员工一样自动“内化”一整套分散的文档。它每次工作时都需要重新获得足够、正确且相互一致的上下文。因此，知识治理的核心不是增加一个更大的总说明，而是为任务组装最小且可验证的上下文包：

```text
task intent
+ applicable policy
+ current facts and interfaces
+ relevant historical decisions / counterexamples
+ verification commands
= task packet
```

根指令只负责把 agent 导向这些来源。Skill 负责固定流程和输入输出格式。引用材料负责背景与例外。脚本、测试和 CI 负责把能客观验证的规则落到执行层。把这些职责混在一个长文件里，通常会带来过期、冲突、检索失败和注意力稀释。

### A concise rule needs an addressable background

人可以看到一句“兼容性优先”后，自动联想到历史事故、用户群、技术约束和例外；AI 不会可靠地拥有这些联想。解决方法不是把背景重复贴进每个 prompt，而是让简短规则能指向：

- 何时适用，何时不适用；
- 为什么存在（决定记录、事故或案例）；
- 以什么事实为准（权威来源）；
- 应如何验证；
- 不确定时找谁或走什么升级路径。

“规则要凝练”与“背景要可达”并不矛盾：前者降低工作上下文噪音，后者避免规则变成脱离语境的口号。

## Governing Existing Documentation

不要试图先读完并重写全部历史资料。增量流程如下：

```text
Inventory files and links
→ classify by role and status
→ identify canonical sources for high-value tasks
→ create a small knowledge map
→ migrate by reference, not duplication
→ archive stale materials with pointers
```

从 3～5 类高频或高风险任务开始。对每类任务明确：

```text
事实去哪里找？
规范去哪里找？
历史决定去哪里找？
已知反例去哪里找？
如何验证？
```

发现两个相互矛盾的“当前规则”时，不要创建第三份折中说明；指定一个权威来源，另一个标记为 Derived 或 Archive，并保留迁移指针。

### Inventory by decision value, not file count

文档屎山的治理不应从“全部阅读并分类”开始。先找最会影响交付质量的决策点：产品能力边界、兼容性承诺、数据安全要求、发布流程、写作约定和已知反例。对于每一个决策点，回答：

```text
当前权威结论是什么？
谁能改它？
它依据什么事实？
哪些 agent、文档或检查正在消费它？
结论失效时谁会发现？
```

没有答案的资料不必立刻删除；先把它放在 Evidence 或 Archive，并禁止把它当作当前规范。这样可以在不做大规模重写的情况下，逐步停止“旧文档以貌似权威的方式参与决策”。

### Prefer references over copies

复用内容时，默认从 agent instructions、Skills 或专题文档链接到 canonical source，而不是复制一段“当前规则”。复制只有在两种情况下合理：离线执行必须携带的最小事实，或由脚本生成的 Derived view。后一种必须带来源、生成方式和更新时间；否则很快会出现人不知道该改哪里、AI 读到互相矛盾版本的情况。

## Minimum Metadata for Durable Knowledge

只对会影响后续 AI 行为、团队决策或 CI 的内容记录元数据：

```yaml
id: DOC-RULE-012
role: policy
status: canonical
scope: docs/how-to/**
owner: documentation-team
last_verified: 2026-08-16
evidence:
  - ../review-cases/DOC-021.md
consumers:
  - ../../.claude/skills/doc-review/SKILL.md
enforcement: ai-review
review_after: 2027-02-16
```

优先治理三类问题：无 owner 的权威资料、超过复核日期的规则、同一规则的多份定义。

### Metadata is an operational contract

元数据不是为了给每篇随手笔记增加负担。只有当某项内容会影响 agent 行为、发布决定或自动检查时，才要求记录治理信息。最重要的字段不是漂亮的分类，而是：

| Field | It answers |
| --- | --- |
| role and status | 这是不是当前规范，还是证据/历史？ |
| scope | 哪些任务可以使用它？ |
| owner | 事实变化时谁负责确认？ |
| evidence | 为什么可以相信它？ |
| consumers | 改动会影响哪些指令、Skills、测试或文档？ |
| review date | 何时需要重新确认？ |

没有 owner 的内容可以被检索，但不应获得“自动成为规则”的权力；没有证据的结论可以作为候选假设，但不应成为硬门禁。

## Two Continuous Loops

### Knowledge Production

```text
session trace / user feedback / review / incident
→ candidate fact, decision, or case
→ human confirmation
→ canonical source update
→ distilled instruction and validation update
→ test on new work
```

### Knowledge Health

```text
code, product, or canonical source changes
→ inspect linked docs, rules, and skills
→ find stale, conflicting, orphaned, or duplicate content
→ propose a repair
→ owner confirms
→ update index and validation
```

AI is useful for inventory, duplicate discovery, broken-link checks, stale-content candidates, and migration drafts. It should not unilaterally retire a policy or make high-risk domain decisions.

### The two loops need different human roles

知识生产循环中，agent 可以抓取 session trace、整理评审意见、聚类相似问题、起草规则和更新派生材料；人负责确认事实、定义风险取舍、批准例外和提升规则等级。

知识健康循环中，agent 可以扫描死链、冲突候选、过期日期、未使用的 instructions 和与代码变更相关的文档；人负责决定“内容是否真的过期”以及是否废弃。把“发现候选问题”自动化，把“改变权威结论”保留给责任人，是减少维护成本的关键边界。

### Measure retrieval quality, not only repository size

知识库健康不等于文件数量少。更有用的信号包括：

- 高风险任务是否能在限定时间内找到权威来源；
- agent 是否经常请求已经存在却难以找到的资料；
- 同一问题是否反复被 reviewer 指出；
- 规则是否产生大量无效告警或人工例外；
- code/product 改动后，关联文档与 instructions 是否被检查；
- 已归档材料是否仍被引用为“当前事实”。

这些指标让治理从一次性“清理屎山”变成对决策系统的持续观测。

## Put Constraints Where They Belong

| Constraint | Best location |
| --- | --- |
| Applies to every task in a repository | root `AGENTS.md` or `CLAUDE.md` |
| Applies to one directory or file type | path-scoped rule |
| Applies to a specific task workflow | Skill plus references |
| Needs rationale or has meaningful exceptions | decision record or case library |
| Can be objectively verified | script, lint, test, or CI |
| Is a high-risk value judgment | human approval gate |

一个实用的反模式是把所有经验都写进根 `AGENTS.md` / `CLAUDE.md`。它会让低相关规则挤占注意力，并在每次任务中重复加载。另一个反模式是把每个历史教训都做成 Skill：Skills 应该是可重复的操作能力，不是档案库。背景与案例保持可检索，只有在某个工作流确实需要时才显式加载。

## Practical Rollout

1. Create a `knowledge-map.md` and name the first canonical sources.
2. Classify only the material touched by active work; do not launch a repository-wide rewrite.
3. Turn repeated review feedback into cases, then into candidate rules.
4. Distill stable rules into scoped agent instructions and Skills.
5. Promote deterministic rules into checks.
6. Periodically run a doc-gardening pass for stale owners, duplicated rules, broken links and unverified derived files.

## A Concrete Starting Structure

下面是一种通用结构；它不是唯一正确的目录，而是职责分离的示例：

```text
knowledge/
  map.md                    # 入口、权威来源和任务路由
  policies/                 # 当前规范（Canonical）
  decisions/                # 需要理由、例外和时间线的决定
  review-cases/             # 已确认的正例、反例与评审理由
  references/               # 长背景、外部证据、研究材料
  archive/                  # 历史资料，默认不进入任务上下文
  derived/                  # 可重建的摘要、索引、任务视图
.claude/
  skills/                   # 稳定、可重复的任务流程
  hooks/                    # 采集 trace、校验或排队脚本
AGENTS.md or CLAUDE.md      # 轻量地图与全局约束
```

此结构不要求一次迁移所有旧文档。可以先从一个高频任务开始，例如“更新产品操作指南”：指定它的事实来源、写作规则、评审案例、验证命令和人工升级条件。等这个任务的 task packet 稳定后，再复制治理模式到下一个任务。

## When to Create, Merge, or Retire a Rule

在每一次新增规则前做一个小判断：

| Question | Suggested action |
| --- | --- |
| 它只解释一次性事件吗？ | 保留为 case 或 decision，不做全局规则 |
| 它已经在多个任务中重复出现吗？ | 形成候选 policy 或 Skill 步骤 |
| 它是否可以由程序明确验证？ | 优先写成测试、lint 或 CI |
| 它依赖业务权衡或上下文？ | 记录 rationale 和人工审批边界 |
| 已有规则是否覆盖它？ | 补充例外或案例，避免创建同义副本 |

废弃规则也应有迁移记录：标记 replacement、失效原因和影响范围。删除内容之前先确认没有 instructions、Skills、CI 或外部读者仍把它当作权威来源。归档不是逃避维护，而是明确告诉人和 agent：这份材料可追溯，但不再决定当前行为。

## A Sustainable Cadence

不需要设立一个庞大的“文档治理项目”。更可持续的节奏是：

1. 日常工作中，用短 review note 记录高价值修正；
2. 每个周期由 agent 汇总候选重复问题、冲突与过期项；
3. 负责人只审核少量候选晋升、废弃和例外；
4. 把已确认且可验证的规则接入任务包或 CI；
5. 定期回放真实任务，检查 agent 是否找对了上下文、是否仍能解释自己的判断。

这样维护的对象不再是“越来越多的文档”，而是一张能服务具体任务、能被验证、也允许遗忘的知识网络。

## Sources

- [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [The new rules of context engineering for Claude 5 generation models](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models)
- [Agent Skills Specification](https://agentskills.io/specification)
- [GitLab: AI agent instruction files for documentation](https://docs.gitlab.com/development/documentation/ai-instruction-files-documentation/)
