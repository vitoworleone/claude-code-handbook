# 第12章 上下文质量提升

> "我给 Claude 贴了整整 50 页文档，它为什么还是理解错了？""为什么同样的 prompt，窗口空的时候效果很好，填了一半就不行了？""CLAUDE.md 写完之后是不是一劳永逸？"

这三个问题指向同一个主题：**上下文质量**。第11章讲了窗口容量管理——五层压缩、九种来源、Prompt Cache 优化。但容量只是问题的一面。本章聚焦更根本的问题：**即使窗口只用了 50%，如果里面是噪声，模型照样会跑偏。**

*Agent Harness Engineering: A Survey*（CMU、Yale、Amazon 等联合发表）将这种现象称为 **Context Drift**——随着会话累积工具结果和中间推理，关键信号被无关信息淹没，模型逐渐偏离正确轨道。这不是容量问题，而是质量问题。

本章不讲"怎么装更多"，而讲"怎么装更好"——从 Prompt Engineering 到 **Context Engineering**。

---

## 12.1 给足上下文：质量优于数量

### 问题：我给了 Claude 一大段代码，它为什么还是理解错了？

**刘小排的洞察：AI 能力上限 = 上下文质量**

早期使用 LLM 时，直觉是"给得越多越聪明"。遇到复杂问题，用户倾向于贴整份文档、整个代码库，指望 AI 从中找出重点。

但这个直觉忽略了 Transformer 架构的基本性质。第11章已证明注意力计算是 O(n²) 的——每加一个 token，所有已有 token 的注意力权重都要重新计算。**无关 token 不仅消耗容量，还会分散注意力权重。** 模型需要额外的计算资源来"识别并忽略"噪声，这本身就是干扰。

刘小排将这一洞察概括为：**AI 能力上限 = 上下文质量。** 关键不是给了多少 token，而是给的信息是否**相关、结构化、高密度**。

> **实际含义**：贴 50 页文档让模型自己找重点，相当于让一个人在嘈杂的体育场里听清耳语。给 3 页相关章节 + 你的具体问题 + 期望输出格式，才是有效沟通。

**多模态输入的信息密度差异**

同一信息可用不同模态表达，而模态选择直接影响模型理解准确率：

| 信息类型 | 低效率表达 | 高效率表达 | 为什么 |
|---------|-----------|-----------|--------|
| 代码变更 | 重贴整个文件 | `git diff` | diff 只显示变更，消除"哪些行改了"的猜测 |
| UI 对齐问题 | 文字描述"左边按钮偏了 2 像素" | 截图 + 红圈标注 | 截图保留空间关系，文字描述歧义大 |
| 测试失败 | "测试挂了" | 失败断言 + 错误栈 + 相关代码 | 模型需要证据链来定位 root cause |
| API 响应 | "返回了错误" | 完整 JSON 响应体 | 结构化数据包含状态码、错误字段、嵌套信息 |

**Context Drift：信号如何被噪声淹没**

*Agent Harness Engineering: A Survey*（Section 5.7）深入分析了 Context Drift 的形成机制：

> "As agent sessions accumulate tool results, intermediate reasoning chains, and user corrections, the signal-to-noise ratio degrades non-uniformly. Key decisions made early in the session retain physical presence in the context window but suffer attention dilution as later tokens compete for the same fixed attention budget."

翻译：随着会话累积工具结果、中间推理链和用户纠正，信噪比非均匀地退化。早期做出的关键决策虽然在窗口中物理存在，但注意力被后续 token 竞争稀释。

第11章引用 Hong et al. (2025) 的发现——标称 200K 的窗口在 50K 就开始退化。这个退化不仅因为容量压力，更因为 **Context Drift**：早期关键信息虽然没被压缩掉，但模型"看不见"它了。

**怎么做：最小充分上下文原则**

| 场景 | 不要这样做 | 要这样做 |
|------|-----------|---------|
| 问代码问题 | 贴整个文件 | 贴相关函数 + 调用栈 + 你的疑问 |
| 问文档问题 | 贴整份 PDF | 贴相关章节 + 页码引用 + 具体问题 |
| 修 bug | "它报错了" | 错误信息 + 触发条件 + 已尝试的修复 |
| 改需求 | 重述整个项目背景 | 引用之前的决策 + 变更点 + 影响范围 |

---

## 12.2 注入位置决定注意力权重

### 问题：为什么写在 CLAUDE.md 里的规则，Claude 有时候遵守有时候不遵守？

第11章 11.2 节展示了上下文窗口的组装全景图，提到 System Prompt 和 CLAUDE.md 处于不同注入顺序。本节解释**为什么这个顺序会直接影响模型是否遵守你的指令。**

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 7.1）通过源码逆向还原了关键架构细节：

> "The system prompt assembly combines system context with the base prompt via asSystemPrompt(). User context (CLAUDE.md and date) is prepended to the message array via prependUserContext(). This separation means CLAUDE.md content occupies a different structural position in the API request than the system prompt, potentially affecting model attention patterns."

翻译：系统提示通过 `asSystemPrompt()` 组装，用户上下文通过 `prependUserContext()` 前置到消息数组。这意味着 CLAUDE.md 在 API 请求中占据**与系统提示不同的结构位置**，影响模型的注意力分配模式。

**为什么会有差异**：Anthropic API 的请求结构区分 `system` 字段和 `messages` 数组。模型在 RLHF 训练中对 system prompt 的"权威性"有更强的对齐——违反 system prompt 的概率显著低于忽略 user message 中的建议。这不是文档说明，是训练动态的结果：system prompt 在整个对话中被一致地强化为"高优先级约束"。

**结论**：CLAUDE.md（通过 `prependUserContext()`）占据 User Context 位置，模型对它的遵守是**概率性**的——大多数时候会遵守，但长上下文中可能忽略；output styles 和 `--append-system-prompt`（通过 `asSystemPrompt()`）占据 System Prompt 位置，遵守是**确定性**的——模型几乎不可能违反。

| 注入位置 | 内容示例 | 模型的遵守模式 | 适用规则类型 |
|---------|---------|-------------|------------|
| **System Prompt 位置** | output styles、`--append-system-prompt` | 确定性（几乎不可能违反） | 安全策略、输出格式约束、核心架构边界 |
| **User Context 位置** | CLAUDE.md、日期、环境信息 | 概率性（通常遵守，可能忽略） | 代码风格偏好、命名惯例、项目约定 |
| **Conversation 位置** | 对话历史、工具结果 | 依位置递减（U 形曲线） | 临时指令、当前任务描述 |

**怎么做：按优先级分配指令位置**

1. **绝对不能违反的规则** → System Prompt 位置
   - 安全策略（如"禁止修改生产配置"）
   - 输出格式约束（如"所有代码块必须带语言标识符"）
   - 通过 `--append-system-prompt` 注入

2. **强烈建议的项目约定** → CLAUDE.md
   - 代码风格、架构决策
   - 注意：如果模型在长上下文中开始忽略这些约定，不要把原因归结为"prompt 写得不好"——这是结构位置的固有限制

3. **临时性、任务特定的指令** → 当前对话消息
   - 本次任务的特殊要求
   - 注意：这些指令在 U 形曲线的谷底最弱，重要约束不要只放在对话历史中

---

## 12.3 四种扩展机制的上下文成本：Why Four Mechanisms?

### 问题：为什么 Claude Code 需要 Hooks、Skills、Plugins、MCP 四种扩展机制？一个通用插件系统不够吗？

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 6.3）专门讨论了这个问题。论文指出，四种机制在"功能表达力"和"上下文成本"两个维度上形成 **graduated context-cost ordering（渐进式上下文成本排序）**。

**为什么不能统一**：统一为任何一种机制都会牺牲覆盖场景。
- 统一为 Hooks：事件触发模型无法"提前知道"工具存在，无法表达带复杂 schema 的工具调用
- 统一为 MCP：简单场景也要支付完整 tool schema 的上下文开销，造成浪费
- 统一为 Skills：frontmatter 描述不足以表达需要运行时参数的外部系统调用

**结论：四种机制的上下文成本对比**

| 机制 | 上下文成本 | 常驻 prompt 内容 | 设计理由 |
|------|-----------|----------------|---------|
| **Hooks** | **零** | 默认不注入 | 事件驱动（pre-command、post-compact 等），不需要模型提前知道存在，触发时由 harness 直接执行 |
| **Skills** | **低** | 仅 frontmatter descriptions | 可复用工作流。模型只需知道"有这个技能、能做什么"，正文在调用时才加载 |
| **Plugins** | **中** | 取决于捆绑内容 | 功能扩展，比 Skills 更重量级，可能包含完整的工具定义和配置 |
| **MCP** | **高** | 完整 tool schemas | 外部系统集成。每个 MCP 工具的 name、description、parameters schema 都必须常驻，供模型规划调用 |

**为什么 MCP 成本最高**：模型在决定调用工具之前，需要"看到"所有可用工具的 schema。MCP 工具的参数定义（JSON Schema）可能很长——一个数据库查询 MCP 可能包含数十个字段的类型定义。这些 schema 在**每次模型调用**时都占用 token，无论该工具本轮是否被使用。

**怎么做：按场景选择机制，最小化上下文税**

| 你的需求 | 选择 | 理由 |
|---------|------|------|
| compact 后自动执行某脚本 | Hooks | 零上下文开销，harness 直接处理 |
| 可复用的代码审查流程 | Skills | 仅 frontmatter 常驻，正文按需 |
| 需要复杂配置的功能扩展 | Plugins | 中等成本，功能表达力足够 |
| 连接外部 API/数据库 | MCP | 高成本但必需——**只连接真正需要的** |

**关键实践**：不要把能用 Hooks 做的事做成 MCP。你是在为每个模型调用支付不必要的上下文税。定期审计已连接的 MCP 服务器——每个 unused MCP 都在 silently 消耗你的 Smart Zone。

---

## 12.4 Path-scoped Rules：按需加载

### 问题：我的项目有 20 个子目录，每个都有特定规则。全写进 CLAUDE.md 会不会太长？

会。第11章已说明 CLAUDE.md 每多一行都消耗每次模型调用的 token，而且修改任何内容都会触发 Prompt Cache 失效。

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 7.1）的 **Late Injection（延迟注入）** 机制解决了这个问题。

**为什么 lazy loading 有效**：Prompt Cache 的效率依赖于前缀稳定性。启动时加载的规则越多，前缀越长，任何规则修改导致缓存失效的成本越高。Late injection 把规则加载推迟到 Agent **实际读取相关文件时**，避免了"为可能用不到的规则付费"。

具体来说：
- **启动时加载**：root → CWD 路径上的无条件规则（全局适用的规则）
- **懒加载**：嵌套目录的 path-scoped rules 在 Agent 读到匹配文件时才注入

**模型指令的动态演化**

path-scoped rules 不仅是"省 token"的机制，它还让模型指令能**随代码库探索动态演化**。当 Agent 在根目录时，它只加载通用规则；当它深入 `src/api/` 目录读取接口文件时，API 规范规则才注入；当它转到 `tests/` 目录时，测试约定规则才出现。

这模拟了人类开发者的认知过程——你不会在思考架构时同时加载所有测试细节，而是在需要时调取。

**怎么做：分层规则设计**

```
根目录 CLAUDE.md          ← 启动时加载（架构、安全基线、通用风格）
├── .claude/
│   └── rules/
│       ├── api.md        ← path: "src/api/**/*.ts"，懒加载
│       ├── test.md       ← path: "tests/**/*.spec.ts"，懒加载
│       └── db.md         ← path: "src/db/**/*.ts"，懒加载
```

**缓存友好的设计**

呼应第11章 11.5 节的 Prompt Cache 三规则：
1. **保持前缀稳定**：把不常变的全局规则放根目录 CLAUDE.md
2. **延迟加载可变规则**：把可能频繁调整的子目录规则写成 path-scoped
3. **减少失效面**：修改 `api.md` 只影响涉及 API 文件的对话轮次，不使整个前缀失效

---

## 12.5 子代理隔离：守卫 Smart Zone

### 问题：我需要让 Claude 探索一个陌生代码库，但不想让它把主会话搞乱。

**Agent Harness Engineering: A Survey**（Section 5.2）将此问题归纳为从 **Prompt Engineering** 到 **Context Engineering** 的范式转移：

> "Early LLM usage focused on crafting better single prompts. As agent systems complexify, optimizing one prompt's wording yields diminishing returns when the context window contains thousands of tokens of tool results and file contents. The emerging discipline is Context Engineering: designing the information environment, not just the instruction."

翻译：早期 LLM 使用聚焦于打磨单条 prompt。随着 Agent 系统复杂化，当窗口包含数千 token 的工具结果和文件内容时，单条 prompt 的优化收益递减。新兴学科是 **Context Engineering**：设计信息环境，而不仅是优化指令。

**为什么是范式转移**：单条 prompt 再完美，也抵消不了整体信息环境的恶化。Context Engineering 的核心洞见是：**信息放在哪个窗口，和写什么内容同等重要。**

**子代理隔离：Context Engineering 的关键实践**

把探索、研究、验证等任务分配到**独立上下文窗口**，主会话只接收结构化摘要。这实现了双重收益：

1. **节省主窗口 token**：探索过程的中间文件读取、推理链不进入主窗口
2. **避免污染**：探索中的错误假设、试错路径不会污染主会话的决策链

**核心数字**：Agent teams 的总 token 消耗约为标准会话的 **7 倍**（*Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems* 的实证数据）。但这 7 倍 token 被隔离在子代理窗口，主会话保持在 Smart Zone。

**Smart Zone vs Dumb Zone 的实战意义**

第11章已介绍 Matt Pocock 的 Smart Zone（~100K token 以下）和 Dumb Zone（~100K 以上）概念。

> "Every time you add a token to an LLM, it's like you're adding a team to a football league. The number of matches scales quadratically. Each token has an attention relationship to every other token."

为什么超过阈值后模型变"蠢"：O(n²) 注意力计算和 U 形注意力曲线意味着，超过 ~100K 后，新增 token 的信息收益被注意力稀释抵消。模型不是处理能力变弱了，是**关键信息拿不到足够的注意力权重了**——就像在一个 100 人的会议室里，核心发言人的声音被背景噪音淹没。

**怎么做：主会话永不出 Smart Zone**

| 任务类型 | 处理方式 | 主会话接收 |
|---------|---------|-----------|
| 探索陌生代码库 | 派 Explore agent | 目录结构摘要 + 关键文件列表 |
| 研究技术方案 | 派 Research agent | 方案对比 + 推荐结论 |
| 多文件批量重构 | 派 Refactor agent | 变更清单 + 影响分析 |
| 长测试运行分析 | 派 Test agent | 失败摘要 + 修复建议 |
| 精细代码编辑 | 主会话亲自做 | 直接编辑结果 |

**与 /compact 的区别**

子代理隔离和 `/compact` 都解决上下文压力，但机制不同：

| | 子代理隔离 | /compact |
|--|-----------|---------|
| **时机** | 任务开始前 | 任务进行中（上下文满了之后） |
| **数据保留** | 完整历史在子代理窗口，主会话有摘要 | 原始历史被替换为模型生成的摘要 |
| **信息损失** | 无损失（子代理窗口完整） | 有损压缩 |
| **目标** | 预防性（不让垃圾进主窗口） | 治疗性（窗口满了之后抢救） |

**黄金法则**：大任务的探索阶段永远不要放在主会话。预防优于治疗。

---

## 12.6 避免 Doc Rot：上下文质量的长期维护

### 问题：CLAUDE.md 写完之后是不是一劳永逸？

不是。*Agent Harness Engineering: A Survey*（Section 5.7）指出，当前上下文管理主要依赖压缩（减少体积）和记忆（提取事实），但两者都不能解决"指令过时"问题：

- **压缩不审查内容**：它只缩减体积，不检查被压缩的信息是否仍然正确
- **记忆不更新规则**：它提取偏好和模式，但不修改 CLAUDE.md 中的指令

**Doc Rot：上下文质量的隐性退化**

Doc Rot（文档腐烂）是 Context Drift 的静态版本：

- 代码库在演进，但 CLAUDE.md 是静态文件
- 三周前写的"使用 Jest 测试"在团队迁移到 Vitest 后变成错误指令
- 过时的规则不仅无用——它会与当前实践冲突，导致模型困惑

> 模型困惑的典型表现："用户说用 Jest，但 package.json 里只有 vitest。我该听哪个？"

Doc Rot 不会像上下文满那样触发明显症状（如模型重复自己），而是表现为**"模型越来越频繁地违反规则"**——其实规则本身已经过时了。

**维护策略：三条规则**

**规则一：定期审查**

每周打开 `CLAUDE.md`，执行以下检查：
- 指令是否与当前代码库一致？
- 引用的文件路径是否仍然有效？
- 工具链版本是否已更新？
- 是否有互相矛盾的指令？

**规则二：迁移机制——从规则到习惯到 hooks**

当一条规则已经被团队正确执行多次（形成习惯），考虑：
- **删除**：如果团队已经内化了，不需要再告诉模型
- **转 hooks**：如果需要强制执行，写成 hook（如 pre-commit 风格检查）

这减少了常驻 token，降低了缓存失效面。

**规则三：写入触发——第二次纠正**

呼应第10章的黄金法则：纠正两次以上 → 写进 CLAUDE.md。

但写入时要注意**替换过时指令**，而非简单追加：
```markdown
## 测试框架
- ~~使用 Jest~~ ❌ 过时
- 使用 Vitest ✓ 当前
```

**与第10章的衔接**

第10章讲"信息该放哪一层"（Compact vs CLAUDE.md vs Auto Memory）。本章补充：**放进去之后还要维护**。四层持久化体系不是"写一次就忘"的档案柜，是需要定期整理的工作台。
