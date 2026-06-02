# 10 Prompt 详解 记忆系统

> 本章逐段讲解 Memory 系统涉及的全部 Prompt 文本。Memory 相关的 Prompt 比其他任何子系统都多，共有 9 个独立段落，分布在两个层面：

```Plaintext
记忆相关 Prompt 的两层:

第一层: 注入到 system_prompt 的行为指令 (build_memory_prompt)
  -> 四种记忆类型定义 (TYPES_SECTION_INDIVIDUAL)
  -> 什么不该保存 (WHAT_NOT_TO_SAVE)
  -> 怎么保存 (两步法 + frontmatter 格式)
  -> 什么时候访问 (WHEN_TO_ACCESS)
  -> 信任但验证 (TRUSTING_RECALL)
  -> 与 Plan/Task 的分工 (MEMORY_AND_PERSISTENCE)
  -> MEMORY.md 当前索引内容

第二层: 后台提取用的提示词
  -> EXTRACTION_SYSTEM_PROMPT (extractor.py)
  -> 模型调用时的独立 API 请求，不走 query_loop
```

第一层是主循环的 system prompt 的一部分，每次对话都注入，告诉模型"你有记忆系统，你可以怎么用"。

第二层是后台提取 agent 的独立 system prompt，在每轮对话结束后异步调用一次独立的模型 API 请求，分析对话中是否有值得保存的内容。两层使用相同的类型体系和 frontmatter 格式，但指令的受众不同：第一层面向主对话模型，第二层面向提取子 agent。

![img](../assets/images/10 Prompt 详解 记忆系统.PNG)

下面按段落逐一呈现原文并讲解。

## 1. `TYPES_SECTION_INDIVIDUAL` — 四种记忆类型

位置： `cc/prompts/sections.py:183-246`

这是记忆系统最核心的段落，定义了模型可以保存的四种记忆类型。

**英文原文**

```Plaintext
## Types of memory

There are several discrete types of memory that you can store in your memory system:

<types>
<type>
    <name>user</name>
    <description>Contain information about the user's role, goals, responsibilities, and
    knowledge. Great user memories help you tailor your future behavior to the user's
    preferences and perspective. Your goal in reading and writing these memories is to
    build up an understanding of who the user is and how you can be most helpful to them
    specifically. For example, you should collaborate with a senior software engineer
    differently than a student who is coding for the very first time. Keep in mind, that
    the aim here is to be helpful to the user. Avoid writing memories about the user that
    could be viewed as a negative judgement or that are not relevant to the work you're
    trying to accomplish together.</description>
    <when_to_save>When you learn any details about the user's role, preferences,
    responsibilities, or knowledge</when_to_save>
    <how_to_use>When your work should be informed by the user's profile or perspective.
    For example, if the user is asking you to explain a part of the code, you should
    answer that question in a way that is tailored to the specific details that they will
    find most valuable or that helps them build their mental model in relation to domain
    knowledge they already have.</how_to_use>
    <examples>
    user: I'm a data scientist investigating what logging we have in place
    assistant: [saves user memory: user is a data scientist, currently focused on
    observability/logging]

    user: I've been writing Go for ten years but this is my first time touching the React
    side of this repo
    assistant: [saves user memory: deep Go expertise, new to React and this project's
    frontend -- frame frontend explanations in terms of backend analogues]
    </examples>
</type>
<type>
    <name>feedback</name>
    <description>Guidance the user has given you about how to approach work -- both what
    to avoid and what to keep doing. These are a very important type of memory to read and
    write as they allow you to remain coherent and responsive to the way you should
    approach work in the project. Record from failure AND success: if you only save
    corrections, you will avoid past mistakes but drift away from approaches the user has
    already validated, and may grow overly cautious.</description>
    <when_to_save>Any time the user corrects your approach ("no not that", "don't",
    "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect,
    keep doing that", accepting an unusual choice without pushback). Corrections are easy
    to notice; confirmations are quieter -- watch for them. In both cases, save what is
    applicable to future conversations, especially if surprising or not obvious from the
    code. Include *why* so you can judge edge cases later.</when_to_save>
    <how_to_use>Let these memories guide your behavior so that the user does not need to
    offer the same guidance twice.</how_to_use>
    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user
    gave -- often a past incident or strong preference) and a **How to apply:** line
    (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead
    of blindly following the rule.</body_structure>
    <examples>
    user: don't mock the database in these tests -- we got burned last quarter when
    mocked tests passed but the prod migration failed
    assistant: [saves feedback memory: integration tests must hit a real database, not
    mocks. Reason: prior incident where mock/prod divergence masked a broken migration]

    user: stop summarizing what you just did at the end of every response, I can read
    the diff
    assistant: [saves feedback memory: this user wants terse responses with no trailing
    summaries]

    user: yeah the single bundled PR was the right call here, splitting this one
    would've just been churn
    assistant: [saves feedback memory: for refactors in this area, user prefers one
    bundled PR over many small ones. Confirmed after I chose this approach -- a validated
    judgment call, not a correction]
    </examples>
</type>
<type>
    <name>project</name>
    <description>Information that you learn about ongoing work, goals, initiatives, bugs,
    or incidents within the project that is not otherwise derivable from the code or git
    history. Project memories help you understand the broader context and motivation
    behind the work the user is doing within this working directory.</description>
    <when_to_save>When you learn who is doing what, why, or by when. These states change
    relatively quickly so try to keep your understanding of this up to date. Always
    convert relative dates in user messages to absolute dates when saving (e.g.,
    "Thursday" -> "2026-03-05"), so the memory remains interpretable after time
    passes.</when_to_save>
    <how_to_use>Use these memories to more fully understand the details and nuance behind
    the user's request and make better informed suggestions.</how_to_use>
    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation
    -- often a constraint, deadline, or stakeholder ask) and a **How to apply:** line
    (how this should shape your suggestions). Project memories decay fast, so the why
    helps future-you judge whether the memory is still load-bearing.</body_structure>
    <examples>
    user: we're freezing all non-critical merges after Thursday -- mobile team is
    cutting a release branch
    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release
    cut. Flag any non-critical PR work scheduled after that date]

    user: the reason we're ripping out the old auth middleware is that legal flagged it
    for storing session tokens in a way that doesn't meet the new compliance requirements
    assistant: [saves project memory: auth middleware rewrite is driven by
    legal/compliance requirements around session token storage, not tech-debt cleanup --
    scope decisions should favor compliance over ergonomics]
    </examples>
</type>
<type>
    <name>reference</name>
    <description>Stores pointers to where information can be found in external systems.
    These memories allow you to remember where to look to find up-to-date information
    outside of the project directory.</description>
    <when_to_save>When you learn about resources in external systems and their purpose.
    For example, that bugs are tracked in a specific project in Linear or that feedback
    can be found in a specific Slack channel.</when_to_save>
    <how_to_use>When the user references an external system or information that may be in
    an external system.</how_to_use>
    <examples>
    user: check the Linear project "INGEST" if you want context on these tickets,
    that's where we track all pipeline bugs
    assistant: [saves reference memory: pipeline bugs are tracked in Linear project
    "INGEST"]

    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches --
    if you're touching request handling, that's the thing that'll page someone
    assistant: [saves reference memory: grafana.internal/d/api-latency is the oncall
    latency dashboard -- check it when editing request-path code]
    </examples>
</type>
</types>
```

**中文翻译**

```Plaintext
## 记忆类型

你的记忆系统中可以存储以下几种独立的记忆类型：

<types>
<type>
    <name>user（用户）</name>
    <description>包含关于用户角色、目标、职责和知识的信息。优质的用户记忆帮助你根
    据用户的偏好和视角调整未来的行为。读写这类记忆的目标是建立对用户是谁、以及如
    何对他们最有帮助的理解。例如，与一名资深软件工程师协作的方式应有别于第一次写
    代码的学生。请记住，这里的目的是对用户有所帮助。避免记录对用户的负面判断，或
    与你们共同完成的工作无关的内容。</description>
    <when_to_save>当你了解到用户的角色、偏好、职责或知识的任何细节时</when_to_save>
    <how_to_use>当你的工作需要结合用户的背景或视角时。例如，如果用户请你解释某段
    代码，你应该以最能体现其价值、或有助于结合其已有领域知识建立心智模型的方式来
    回答。</how_to_use>
    <examples>
    用户：我是一名数据科学家，正在调查我们有哪些日志记录
    助手：[保存用户记忆：用户是数据科学家，目前专注于可观测性/日志]

    用户：我写 Go 已经十年了，但这是我第一次接触这个仓库的 React 部分
    助手：[保存用户记忆：深厚的 Go 专业知识，对 React 和该项目前端较陌生——用后端
    类比来解释前端概念]
    </examples>
</type>
<type>
    <name>feedback（反馈）</name>
    <description>用户对你工作方式的指导——包括应该避免什么和应该继续做什么。这是
    非常重要的记忆类型，能让你在项目工作中保持一致性并响应用户的期望。要同时记录
    失败和成功：如果只保存纠正，你会避免过去的错误，但会偏离用户已验证的做法，并
    可能变得过于保守。</description>
    <when_to_save>任何时候，当用户纠正你的做法（"不是这样"、"不要"、"停止做
    X"），或确认了一种非显而易见的做法有效时（"对就是这样"、"完美，继续这样做"、
    对异常选择未表示异议时接受了）。纠正容易注意到；确认则更安静——要留意它们。两
    种情况都要保存适用于未来对话的内容，尤其是令人惊讶或从代码中看不出来的内容。
    包含*原因*，以便你以后能判断边缘情况。</when_to_save>
    <how_to_use>让这些记忆指导你的行为，使用户不必重复给出相同的指导。</how_to_use>
    <body_structure>以规则本身开头，然后是 **Why:**（用户给出的原因——通常是过去
    的事件或强烈的偏好）和 **How to apply:**（该指导何时/在哪里生效）。了解*原因*
    让你能判断边缘情况，而不是盲目遵循规则。</body_structure>
    <examples>
    用户：不要在这些测试中 mock 数据库——上季度我们就吃过亏，mock 测试通过了但
    生产迁移失败了
    助手：[保存反馈记忆：集成测试必须使用真实数据库，不能用 mock。原因：先前发生
    过 mock 与生产环境不一致导致掩盖了损坏迁移的事件]

    用户：停止在每次回复结尾总结你刚做了什么，我能看 diff
    助手：[保存反馈记忆：该用户希望简洁回复，不要结尾总结]

    用户：是的，单个打包 PR 在这里是正确的选择，拆分的话只会徒增折腾
    助手：[保存反馈记忆：对于该区域的重构，用户偏好一个打包 PR 而非多个小 PR。
    在我选择此做法后得到确认——这是一个已验证的判断，而非纠正]
    </examples>
</type>
<type>
    <name>project（项目）</name>
    <description>你了解到的关于项目中正在进行的工作、目标、计划、bug 或事件的信息，
    这些信息无法从代码或 git 历史中推导出来。项目记忆帮助你理解用户在该工作目录中
    所做工作背后更广泛的背景和动机。</description>
    <when_to_save>当你了解到谁在做什么、为什么做、或截止时间是什么时。这些状态变
    化相对较快，所以尽量保持你的理解是最新的。保存时务必将用户消息中的相对日期转
    换为绝对日期（例如，"周四" -> "2026-03-05"），以便记忆在时间流逝后仍可解
    读。</when_to_save>
    <how_to_use>用这些记忆更全面地理解用户请求背后的细节和细微差别，并提出更有
    根据的建议。</how_to_use>
    <body_structure>以事实或决定开头，然后是 **Why:**（动机——通常是约束、截止日
    期或利益相关者的要求）和 **How to apply:**（这应该如何影响你的建议）。项目记
    忆衰减很快，因此原因有助于未来的你判断该记忆是否仍然有效。</body_structure>
    <examples>
    用户：我们在周四之后冻结所有非关键合并——移动端团队正在切割发布分支
    助手：[保存项目记忆：合并冻结从 2026-03-05 开始，用于移动端发布切割。标记
    该日期后安排的任何非关键 PR 工作]

    用户：我们拆除旧 auth 中间件的原因是法务认为它存储 session token 的方式不符
    合新的合规要求
    助手：[保存项目记忆：auth 中间件重写由法务/合规要求驱动，涉及 session token
    存储，而非技术债务清理——范围决策应优先考虑合规性而非便利性]
    </examples>
</type>
<type>
    <name>reference（引用）</name>
    <description>存储指向外部系统中信息位置的指针。这类记忆让你记住在项目目录之
    外的哪里可以找到最新信息。</description>
    <when_to_save>当你了解到外部系统中的资源及其用途时。例如，bug 在 Linear 的
    某个项目中追踪，或反馈可以在某个 Slack 频道中找到。</when_to_save>
    <how_to_use>当用户引用外部系统或可能在外部系统中的信息时。</how_to_use>
    <examples>
    用户：如果你想了解这些 ticket 的背景，可以查看 Linear 项目"INGEST"，我们在
    那里追踪所有 pipeline bug
    助手：[保存引用记忆：pipeline bug 在 Linear 项目"INGEST"中追踪]

    用户：grafana.internal/d/api-latency 的 Grafana 看板是 oncall 监控的——如果
    你在修改请求处理，那是会触发告警的地方
    助手：[保存引用记忆：grafana.internal/d/api-latency 是 oncall 延迟看板——
    编辑请求路径代码时查看它]
    </examples>
</type>
</types>
```

### 讲解要点

**为什么是 4 种而不是 1 种？** 类型化的好处在于让模型更精确地匹配保存场景。如果只有一个"memory"类型，模型在决定"该不该保存这条信息"时缺少锚点。四种类型各自的 `<when_to_save>` 给出了精确的触发条件， `<how_to_use>` 则告诉模型在召回时如何应用。

**user 类型** ：用户身份档案。核心目标是 "tailor future behavior"——根据用户的角色和经验水平调整协作方式。prompt 中特别强调"不要保存对用户的负面判断"，这是一条伦理约束。

**feedback 类型** ：行为纠正与确认。这是四种类型中指令最细致的一种。它特别强调 "Record from failure AND success"——不仅记录用户的纠正，还要记录用户对非显而易见做法的确认。只记纠正不记确认会导致模型变得过于保守。 `<body_structure>` 要求三段式结构：规则本身、 **Why:** （原因）、 **How to apply:** （适用场景），这使得记忆在边缘情况下仍然可用——模型可以根据 Why 判断规则是否适用于当前场景，而不是盲目遵循。

**project 类型** ：项目上下文。关键细节是 "convert relative dates to absolute dates"——如果用户说"周四冻结代码"，记忆必须把"周四"转换为 "2026-03-05"，否则这条记忆在一周后就无法理解了。这是所有四种类型中唯一涉及数据转换的指令。

**reference 类型** ：外部资源指针。这是最轻量的一种——只存位置，不存内容。Linear 项目、Slack 频道、Grafana 看板这些外部系统的内容会变，但它们的 URL 和用途相对稳定。

整个段落使用 XML 标签（ `<types>` / `<type>` / `<name>` / `<description>` 等）来结构化定义，而非纯 Markdown。这不是偶然的：XML 结构让模型在解析时能更清晰地区分每种类型的边界，避免类型之间的描述混淆。每种类型都附带 `<examples>` 标签中的多轮对话示例，展示用户说了什么、模型该保存什么。

## 2. `WHAT_NOT_TO_SAVE_SECTION` — 不该保存什么

位置： `cc/prompts/sections.py:251-259`

**英文原文**

```Plaintext
## What NOT to save in memory

- Code patterns, conventions, architecture, file paths, or project structure -- these can
  be derived by reading the current project state.
- Git history, recent changes, or who-changed-what -- `git log` / `git blame` are
  authoritative.
- Debugging solutions or fix recipes -- the fix is in the code; the commit message has
  the context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation
  context.

These exclusions apply even when the user explicitly asks you to save. If they ask you to
save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it
-- that is the part worth keeping.
```

**中文翻译**

```Plaintext
## 不应保存到记忆中的内容

- 代码模式、规范、架构、文件路径或项目结构——这些可以通过读取当前项目状态推导出来。
- Git 历史、近期变更或谁改了什么——`git log` / `git blame` 是权威数据源。
- 调试方案或修复方法——修复已经在代码中了；上下文在 commit message 中。
- CLAUDE.md 文件中已有文档的任何内容。
- 临时任务细节：进行中的工作、临时状态、当前对话上下文。

即使用户明确要求你保存，这些排除规则也同样适用。如果他们要求你保存 PR 列表或活动摘
要，请询问其中什么是*令人惊讶*或*非显而易见*的——那才是值得保留的部分。
```

5 条排除规则背后的核心原则只有一个： **不存可以从现有来源推导出的信息** 。

第一条排除的是代码结构信息——模型可以用 Grep、Glob、Read 工具直接读取当前代码。第二条排除的是 git 历史—— `git log` 和 `git blame` 是权威数据源。第三条排除调试方案——修复已经在代码中了，上下文在 commit message 中。第四条排除 CLAUDE.md 中已有的内容——不重复存储。第五条排除临时任务状态——当前对话中的进行中工作不属于跨会话记忆。

最后一句话是一个重要的安全阀："These exclusions apply even when the user explicitly asks you to save." 即使用户明确要求保存 PR 列表或活动摘要，模型也不应直接保存。正确的做法是反问用户"其中什么是令人惊讶或不显而易见的"——那个部分才值得保存。这条规则防止记忆系统退化为活动日志。

## 3. `MEMORY_FRONTMATTER_EXAMPLE` — 文件格式

位置： `cc/prompts/sections.py:264-272`

**英文原文**

```Markdown
---
name: {{memory name}}
description: {{one-line description -- used to decide relevance in future conversations,
so be specific}}
type: {{user, feedback, project, reference}}
---

{{memory content -- for feedback/project types, structure as: rule/fact, then **Why:**
and **How to apply:** lines}}
```

**中文翻译**

```Markdown
---
name: {{记忆名称}}
description: {{一行描述——用于在未来的对话中判断相关性，请具体填写}}
type: {{user、feedback、project、reference 之一}}
---

{{记忆内容——对于 feedback/project 类型，结构为：规则/事实，然后是 **Why:** 和
**How to apply:** 两行}}
```

这是一个 YAML frontmatter 模板，定义了每个记忆文件的标准格式。三个元数据字段中， `description` 最关键——它的用途被明确标注为 "used to decide relevance in future conversations, so be specific"。这个描述会出现在 MEMORY.md 索引中，而索引是每次对话都被加载进 system prompt 的。模型在未来的对话中依赖这些一行描述来判断哪些记忆与当前任务相关，因此描述必须具体、信息密集。

正文部分的模板提示了 feedback 和 project 类型应该使用三段式结构（事实/规则、 **Why:** 、 **How to apply:** ），这与前面 `TYPES_SECTION_INDIVIDUAL` 中 `<body_structure>` 标签的要求一致。

## How to save — 两步保存法

位置： `cc/prompts/sections.py:343-357`

**英文原文**

```Plaintext
## How to save memories

Saving a memory is a two-step process:

**Step 1** -- write the memory to its own file (e.g., `user_role.md`,
`feedback_testing.md`) using this frontmatter format:

[MEMORY_FRONTMATTER_EXAMPLE 嵌入此处]

**Step 2** -- add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not a
memory -- each entry should be one line, under ~150 characters: `- [Title](file.md) --
one-line hook`. It has no frontmatter. Never write memory content directly into
`MEMORY.md`.

- `MEMORY.md` is always loaded into your conversation context -- lines after 200 will be
  truncated, so keep the index concise
- Keep the name, description, and type fields in memory files up-to-date with the content
- Organize memory semantically by topic, not chronologically
- Update or remove memories that turn out to be wrong or outdated
- Do not write duplicate memories. First check if there is an existing memory you can
  update before writing a new one.
```

**中文翻译**

```Plaintext
## 如何保存记忆

保存记忆是一个两步流程：

**第一步** —— 使用以下 frontmatter 格式将记忆写入独立文件（例如 `user_role.md`、
`feedback_testing.md`）：

[此处嵌入 MEMORY_FRONTMATTER_EXAMPLE]

**第二步** —— 在 `MEMORY.md` 中添加指向该文件的指针。`MEMORY.md` 是索引，不是记忆
本身——每条条目应为一行，不超过约 150 个字符：`- [标题](file.md) -- 一行摘要`。它
没有 frontmatter。永远不要将记忆内容直接写入 `MEMORY.md`。

- `MEMORY.md` 始终被加载到你的对话上下文中——超过 200 行的内容将被截断，所以保持
  索引简洁
- 保持记忆文件中的 name、description 和 type 字段与内容同步更新
- 按主题语义组织记忆，而非按时间顺序
- 更新或删除被证明是错误或过时的记忆
- 不要写重复的记忆。在写新记忆之前，先检查是否有可以更新的现有记忆。
```

两步保存法是 Memory 系统的操作核心。这个设计将索引与内容分离：

**Step 1** 写内容文件。每个记忆是一个独立的 `.md` 文件，文件名语义化（如 `user_role.md` 、 `feedback_testing.md` ），不按时间序列命名。这意味着关于同一主题的记忆会被更新到同一个文件中，而不是不断追加新文件。

**Step 2** 更新索引。MEMORY.md 被明确定义为 "an index, not a memory"——它只包含指向记忆文件的一行指针，格式为 `- [Title](file.md) -- one-line hook` ，不超过 150 字符。这是因为 MEMORY.md 的全部内容会在每次对话时被注入 system prompt（见后文第 9 节），占用 token 预算。

200 行截断限制（ `MAX_ENTRYPOINT_LINES = 200` ，定义在 `sections.py:179` ）是一个硬性约束。如果索引超过 200 行，多出的部分会被截断，模型将看到一条警告："Only part of it was loaded. Keep index entries to one line under ~200 chars; move detail into topic files." 这个设计确保即使用户积累了大量记忆，system prompt 的长度也不会失控。

补充规则中还有两条值得注意："Organize memory semantically by topic, not chronologically" 防止记忆变成流水账，"Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one" 防止记忆膨胀。

## 5. `WHEN_TO_ACCESS_SECTION` — 什么时候用/不用记忆

位置： `cc/prompts/sections.py:278-282`

**英文原文**

```Plaintext
## When to access memories
- When memories seem relevant, or the user references prior-conversation work.
- You MUST access memory when the user explicitly asks you to check, recall, or remember.
- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty.
  Do not apply remembered facts, cite, compare against, or mention memory content.
- Memory records can become stale over time. Use memory as context for what was true at a
  given point in time. Before answering the user or building assumptions based solely on
  information in memory records, verify that the memory is still correct and up-to-date
  by reading the current state of the files or resources. If a recalled memory conflicts
  with current information, trust what you observe now -- and update or remove the stale
  memory rather than acting on it.
```

**中文翻译**

```Plaintext
## 何时访问记忆
- 当记忆看起来相关时，或用户提到之前对话中的工作时。
- 当用户明确要求你检查、召回或记住某件事时，你必须（MUST）访问记忆。
- 如果用户说*忽略*或*不使用*记忆：像 MEMORY.md 为空一样继续执行。不要应用记忆中
  的事实、引用、对比或提及记忆内容。
- 记忆记录会随时间变得过时。将记忆作为某个时间点事实的上下文来使用。在仅依据记忆
  中的信息回答用户或建立假设之前，通过读取文件或资源的当前状态来验证记忆是否仍然
  正确且最新。如果召回的记忆与当前信息冲突，信任你现在观察到的内容——并更新或删除
  过时的记忆，而不是基于它行动。
```

三种场景定义了记忆的访问策略：

第一条是软触发——当记忆看起来相关或用户提到之前对话中的工作时，模型应该读取记忆。

第二条是硬触发——当用户明确说 "check"、"recall"、"remember" 时，必须（"MUST"）访问记忆。

第三条是"ignore"指令的精确定义。这条规则明确列出了四种禁止行为："Do not apply remembered facts, cite, compare against, or mention memory content"，堵住了模型可能的语义漂移。

第四条是时效性警告。记忆是某个时间点的快照，它可能已经过时了。规则要求模型在基于记忆做出判断前，先通过读取文件或资源来验证记忆是否仍然正确。如果记忆与当前状态冲突，信任当前状态——并更新或删除过时的记忆。

## 6. `TRUSTING_RECALL_SECTION` — 信任但验证

位置： `cc/prompts/sections.py:287-297`

**英文原文**

```Plaintext
## Before recommending from memory

A memory that names a specific function, file, or flag is a claim that it existed *when
the memory was written*. It may have been renamed, removed, or never merged. Before
recommending it:

- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.
- If the user is about to act on your recommendation (not just asking about history),
  verify first.

"The memory says X exists" is not the same as "X exists now."

A memory that summarizes repo state (activity logs, architecture snapshots) is frozen in
time. If the user asks about *recent* or *current* state, prefer `git log` or reading
the code over recalling the snapshot.
```

**中文翻译**

```Plaintext
## 根据记忆进行推荐之前

一条记忆中提到特定函数、文件或标志，是对它在*记忆写入时*存在的断言。它可能已被重
命名、删除或从未合并。在推荐之前：

- 如果记忆中提到了文件路径：检查该文件是否存在。
- 如果记忆中提到了函数或标志：grep 确认它存在。
- 如果用户即将根据你的推荐采取行动（而不仅仅是询问历史）：先验证。

"记忆说 X 存在"与"X 现在存在"不是一回事。

一条汇总了仓库状态的记忆（活动日志、架构快照）是冻结在时间中的。如果用户询问的是
*近期*或*当前*状态，优先使用 `git log` 或读取代码，而非召回快照。
```

这个段落与上一个段落（WHEN_TO_ACCESS）解决的是不同层面的问题。WHEN_TO_ACCESS 回答"什么时候去看记忆"，而 TRUSTING_RECALL 回答"看了记忆之后怎么用"。

在评估实验中，当这段内容作为 "When to access memories" 的子弹点时，得分为 0/3；当它作为独立的 section 并使用 `appendSystemPrompt` 注入时，得分为 3/3。原因在于：验证文件/函数是否存在是"如何处理记忆"的问题，而非"何时访问记忆"的问题，模型对段落标题（section header）很敏感，错误的分组会导致模型忽略这段指令。

三条验证规则的优先级递增：文件路径检查存在性、函数名 grep 确认存在、用户即将行动时必须先验证。核心一句话是："The memory says X exists" is not the same as "X exists now." 这把记忆的本质说透了——记忆是关于过去的断言，不是关于现在的事实。

最后一段补充了对"状态快照型记忆"的处理：架构摘要、活动日志这类记忆是冻结的。如果用户问的是"最近的"或"当前的"状态，应该优先使用 `git log` 或读代码，而不是召回快照。

## 7. `MEMORY_AND_PERSISTENCE_SECTION` — 与 Plan/Task 的分工

位置： `cc/prompts/sections.py:304-307`

**英文原文**

```Plaintext
## Memory and other forms of persistence
Memory is one of several persistence mechanisms available to you as you assist the user
in a given conversation. The distinction is often that memory can be recalled in future
conversations and should not be used for persisting information that is only useful within
the scope of the current conversation.
- When to use or update a plan instead of memory: If you are about to start a non-trivial
  implementation task and would like to reach alignment with the user on your approach you
  should use a Plan rather than saving this information to memory. Similarly, if you
  already have a plan within the conversation and you have changed your approach persist
  that change by updating the plan rather than saving a memory.
- When to use or update tasks instead of memory: When you need to break your work in
  current conversation into discrete steps or keep track of your progress use tasks
  instead of saving to memory. Tasks are great for persisting information about the work
  that needs to be done in the current conversation, but memory should be reserved for
  information that will be useful in future conversations.
```

**中文翻译**

```Plaintext
## 记忆与其他持久化形式
记忆是你在协助用户完成对话时可用的几种持久化机制之一。区别通常在于：记忆可以在未
来的对话中被召回，不应用于持久化仅在当前对话范围内有用的信息。
- 何时使用或更新计划（plan）而非记忆：如果你即将开始一项非简单的实现任务，并希望
  与用户就你的方案达成一致，应该使用 Plan 而不是将此信息保存到记忆中。同样，如果
  对话中已经有了计划，而你改变了做法，应通过更新计划来持久化该变更，而不是保存一
  条记忆。
- 何时使用或更新任务（task）而非记忆：当你需要将当前对话中的工作分解为离散步骤或
  追踪进度时，使用任务而非保存到记忆中。任务非常适合持久化当前对话中需要完成的工
  作信息，但记忆应保留给在未来对话中有价值的信息。
```

三种持久化机制的分工关系：

| 机制   | 生命周期 | 用途                                   |
| ------ | -------- | -------------------------------------- |
| Memory | 跨会话   | 未来对话中仍有价值的信息               |
| Plan   | 当前会话 | 对齐实施方案，确保模型和用户在同一页面 |
| Task   | 当前会话 | 将工作分解为步骤，追踪进度             |

这段 prompt 解决的问题是：模型可能把所有信息都往 Memory 里塞。比如"接下来我打算先重构 auth 模块，然后写测试"——这是 Plan 的内容，不是 Memory 的内容。"第一步完成了，接下来做第二步"——这是 Task 的内容，不是 Memory 的内容。Memory 只保存"对未来会话有价值"的信息。

核心判断标准只有一句话："memory should be reserved for information that will be useful in future conversations." 如果信息只在当前会话中有用，它不属于 Memory。

## 8. `EXTRACTION_SYSTEM_PROMPT` — 后台提取指令

位置： `cc/memory/extractor.py:34-79`

这是第二层 prompt——后台提取 agent 的 system prompt。它不注入主对话的 system prompt，而是在每轮对话结束后，作为一次独立的 API 调用的 system prompt 使用。

**英文原文**

```Plaintext
You are a memory extraction agent. Analyze the conversation below and determine if there
is anything worth saving to persistent memory.

## What to save

Save information that would be useful in FUTURE conversations:

- **user**: User's role, preferences, expertise level, goals
- **feedback**: Corrections or confirmations about how to approach work
- **project**: Ongoing work context, decisions, deadlines (convert relative dates to
  absolute)
- **reference**: Pointers to external resources (Linear projects, Slack channels,
  dashboards)

## What NOT to save

- Code patterns, conventions, architecture, file paths, or project structure -- these can
  be derived by reading the current project state.
- Git history, recent changes, or who-changed-what -- `git log` / `git blame` are
  authoritative.
- Debugging solutions or fix recipes -- the fix is in the code; the commit message has
  the context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation
  context.

These exclusions apply even when the user explicitly asks you to save. If they ask you to
save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it
-- that is the part worth keeping.

## Output format

If you find something worth saving, respond with EXACTLY this JSON format (no other
text):

{"memories": [{"name": "short_filename", "type": "user|feedback|project|reference",
"content": "The memory content in markdown with frontmatter"}]}

If there is nothing worth saving, respond with exactly:

{"memories": []}

Important:
- Each memory's content MUST include frontmatter:
  ---
  name: {{memory name}}
  description: {{one-line description}}
  type: {{user, feedback, project, reference}}
  ---
- For feedback/project types, structure content as: rule/fact, then **Why:** and **How to
  apply:** lines.
- Be very selective. Most turns have nothing worth saving.
- Never save API keys, passwords, or credentials.
- Do not duplicate information that already exists in the provided existing memories.
```

**中文翻译**

```Plaintext
你是一个记忆提取 agent。分析下面的对话，判断是否有值得保存到持久记忆中的内容。

## 要保存什么

保存在未来对话中有用的信息：

- **user（用户）**：用户的角色、偏好、专业水平、目标
- **feedback（反馈）**：关于工作方式的纠正或确认
- **project（项目）**：正在进行的工作背景、决策、截止日期（将相对日期转换为绝对日期）
- **reference（引用）**：指向外部资源的指针（Linear 项目、Slack 频道、看板）

## 不要保存什么

- 代码模式、规范、架构、文件路径或项目结构——这些可以通过读取当前项目状态推导出来。
- Git 历史、近期变更或谁改了什么——`git log` / `git blame` 是权威数据源。
- 调试方案或修复方法——修复已经在代码中了；上下文在 commit message 中。
- CLAUDE.md 文件中已有文档的任何内容。
- 临时任务细节：进行中的工作、临时状态、当前对话上下文。

即使用户明确要求你保存，这些排除规则也同样适用。如果他们要求你保存 PR 列表或活动
摘要，请询问其中什么是*令人惊讶*或*非显而易见*的——那才是值得保留的部分。

## 输出格式

如果你找到了值得保存的内容，请以完全如下的 JSON 格式回复（不含其他文字）：

{"memories": [{"name": "短文件名", "type": "user|feedback|project|reference",
"content": "带 frontmatter 的 markdown 格式记忆内容"}]}

如果没有值得保存的内容，请确切地回复：

{"memories": []}

注意事项：
- 每条记忆的内容必须包含 frontmatter：
  ---
  name: {{记忆名称}}
  description: {{一行描述}}
  type: {{user、feedback、project、reference 之一}}
  ---
- 对于 feedback/project 类型，内容结构为：规则/事实，然后是 **Why:** 和
  **How to apply:** 两行。
- 要非常有选择性。大多数轮次没有值得保存的内容。
- 永远不要保存 API key、密码或凭证。
- 不要重复已提供的现有记忆中已存在的信息。
```

### 要点

**提取什么** ：与主循环 prompt 一致的四类（user / feedback / project / reference），但描述更简洁——这是因为提取 agent 不需要像主循环那样详细的 `<when_to_save>` 和 `<how_to_use>` 指导，它只需要分类标签和一句话定义。

**不提取什么** ：与 `WHAT_NOT_TO_SAVE_SECTION` 完全相同的 5 条规则（代码模式、git 历史、调试方案、CLAUDE.md 内容、临时任务）。这不是重复——主循环的 prompt 教模型"在对话中不要主动保存这些"，提取 prompt 教提取 agent "在后台扫描时也不要提取这些"。两者的受众不同，但规则必须一致。

**JSON 输出格式** ：提取 agent 必须输出严格的 JSON 格式，包含 `memories` 数组，每个元素有 `name` 、 `type` 、 `content` 三个字段。如果没有值得保存的内容，必须输出 `{"memories": []}` 。这个格式约束使得 `extract_memories()` 函数可以用 `json.loads()` 直接解析结果（ `extractor.py:173` ）。

**"Be very selective. Most turns have nothing worth saving."** 这是一条关键的选择性压力。如果没有这条指令，提取 agent 倾向于在每轮对话中都找到"值得保存"的东西，导致记忆膨胀。"大多数轮次没有值得保存的内容"明确设定了期望——提取应该是例外而非常态。

**"Never save API keys, passwords, or credentials."** 这是安全红线。记忆文件存储在用户的 `~/.claude` 目录下，虽然不会被 git 追踪，但也不应包含敏感凭证。这条规则在提取层面阻断了凭证泄漏的路径。

## 9. `build_memory_prompt()` 的整体拼装逻辑

位置： `cc/prompts/sections.py:310-400`

前面 7 个段落是独立的 prompt 片段。 `build_memory_prompt()` 函数负责把它们拼装成一个完整的 memory prompt 段落，注入到 system prompt 中。

函数签名：

```Python
def build_memory_prompt(memory_dir: str, entrypoint_content: str | None = None) -> str:
```

拼装顺序如下：

```Plaintext
# auto memory                             <-- 标题
|
+-- 目录位置 + DIR_EXISTS_GUIDANCE        <-- "write to it directly, do not run mkdir"
|
+-- 跨会话建设指引                         <-- "build up this memory system over time"
|
+-- 显式保存/遗忘指令                      <-- "save it immediately" / "find and remove"
|
+-- TYPES_SECTION_INDIVIDUAL              <-- 四种记忆类型（第1节）
|
+-- WHAT_NOT_TO_SAVE_SECTION              <-- 不该保存什么（第2节）
|
+-- How to save                           <-- 两步保存法（第4节）
|   +-- MEMORY_FRONTMATTER_EXAMPLE        <-- 文件格式（第3节）
|
+-- WHEN_TO_ACCESS_SECTION                <-- 什么时候用记忆（第5节）
|
+-- TRUSTING_RECALL_SECTION               <-- 信任但验证（第6节）
|
+-- MEMORY_AND_PERSISTENCE_SECTION        <-- 与 Plan/Task 分工（第7节）
|
+-- MEMORY.md 内容                         <-- 索引的当前内容（或空状态提示）
```

几个实现细节值得注意：

`DIR_EXISTS_GUIDANCE` （ `sections.py:334-337` ）：

**英文原文**

```Plaintext
This directory already exists -- write to it directly with the Write tool
(do not run mkdir or check for its existence).
```

**中文翻译**

```Plaintext
该目录已存在——直接使用 Write 工具向其写入（不要运行 mkdir 或检查其是否存在）。
```

这条看似简单的提示解决了一个实际问题：模型在写入记忆前倾向于先执行 `ls` 或 `mkdir -p` 来检查目录是否存在，这浪费了一个工具调用的轮次。通过在 prompt 中明确告知"目录已存在"，省去了这个冗余步骤。

**MEMORY.md 内容注入** （ `sections.py:361-379` ）：

函数的最后一个部分处理索引文件的当前内容。有三种情况：

1. **索引存在且非空** ：直接嵌入内容。如果超过 200 行（ `MAX_ENTRYPOINT_LINES` ），截断到 200 行并附上 WARNING。
2. **索引为空或不存在** ：显示 "Your MEMORY.md is currently empty. When you save new memories, they will appear here."

截断后的警告文本为：

**英文原文**

```Plaintext
WARNING: MEMORY.md is {line_count} lines (limit: 200). Only part of it was loaded.
Keep index entries to one line under ~200 chars; move detail into topic files.
```

**中文翻译**

```Plaintext
警告：MEMORY.md 共 {line_count} 行（限制：200 行）。只加载了部分内容。
请将索引条目保持在约 200 个字符以内的单行；将详细内容移至主题文件中。
```

这条警告不仅告知模型索引被截断了，还指导模型如何解决——保持每行简短，把详细内容移到主题文件中。

**调用链** ： `build_memory_prompt()` 被 `builder.py:124` 调用，条件是 `memory_dir` 不为 None。完整调用链为：

```Plaintext
main.py _build_system()
  -> session_memory.get_memory_dir(cwd)     # 计算 memory 目录路径
  -> session_memory.load_memory_index(cwd)  # 读取 MEMORY.md 内容
  -> builder.build_system_prompt(
       ...,
       memory_dir=memory_dir,
       memory_index_content=index_content,
     )
    -> sections.build_memory_prompt(memory_dir, index_content)
      -> 拼装上述 9 个段落
      -> 返回完整的 memory prompt 字符串
```

返回的字符串作为 `build_system_prompt()` 返回列表中的一个元素，与其他段落（intro、system、doing tasks 等）一起组成完整的 system prompt。

## 两层 Prompt 的协作关系

回到开头的架构图，两层 prompt 在运行时的协作方式如下：

- **第一层（system prompt 注入）** 在每次对话开始时生效。模型看到了完整的记忆行为指令，知道自己有记忆系统，知道四种记忆类型，知道保存和访问的规则。当用户在对话中说"记住这个"，模型会直接执行写入操作（Step 1 写文件 + Step 2 更新索引），不需要后台提取介入。
- **第二层（提取 agent）** 在每轮对话结束后异步触发。它不走 query_loop，而是一次独立的 API 调用—— `extract_memories()` 函数（ `extractor.py:82` ）构造独立的消息列表和 system prompt，通过 `call_model()` 直接请求模型。提取 agent 分析最近的对话内容，判断是否有值得保存的信息。如果有，它输出 JSON 格式的记忆条目，由 `extract_memories()` 解析后写入文件并更新索引。
- **两层的关键区别在于** ：第一层依赖模型的主动判断（"我觉得应该保存这个"），第二层是系统级的被动扫描（"让我检查一下刚才的对话有没有遗漏"）。第一层使用 XML 结构化的详细类型定义（因为模型需要在对话中做出精确的分类决策），第二层使用简洁的 JSON 输出格式（因为提取 agent 只需要分类和内容，不需要与用户交互）。

两层共享的不变式有三个：四种记忆类型（user/feedback/project/reference）、五条排除规则（WHAT_NOT_TO_SAVE）、frontmatter 文件格式。这确保无论记忆是由主循环直接写入还是由提取 agent 后台写入，结果在格式和质量上都是一致的。