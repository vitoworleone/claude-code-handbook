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

## Put Constraints Where They Belong

| Constraint | Best location |
| --- | --- |
| Applies to every task in a repository | root `AGENTS.md` or `CLAUDE.md` |
| Applies to one directory or file type | path-scoped rule |
| Applies to a specific task workflow | Skill plus references |
| Needs rationale or has meaningful exceptions | decision record or case library |
| Can be objectively verified | script, lint, test, or CI |
| Is a high-risk value judgment | human approval gate |

## Practical Rollout

1. Create a `knowledge-map.md` and name the first canonical sources.
2. Classify only the material touched by active work; do not launch a repository-wide rewrite.
3. Turn repeated review feedback into cases, then into candidate rules.
4. Distill stable rules into scoped agent instructions and Skills.
5. Promote deterministic rules into checks.
6. Periodically run a doc-gardening pass for stale owners, duplicated rules, broken links and unverified derived files.

## Sources

- [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [The new rules of context engineering for Claude 5 generation models](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models)
- [Agent Skills Specification](https://agentskills.io/specification)
- [GitLab: AI agent instruction files for documentation](https://docs.gitlab.com/development/documentation/ai-instruction-files-documentation/)
