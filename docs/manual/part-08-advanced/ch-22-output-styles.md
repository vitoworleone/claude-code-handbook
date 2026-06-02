# 第22章 输出风格与模式

输出风格不是模型能力开关，而是交互契约。它决定 Claude Code 用什么角色、语气和输出结构回应你。

如果你总是在每一轮重复说"多解释一点""主动给下一步""不要直接写完，让我练习"，那就应该把这些要求沉淀成输出风格。

---

## 22.1 输出风格解决的是什么问题

Claude Code 的默认模式是标准软件工程助手。它会解释必要信息、调用工具、修改文件，并在最后汇总结果。

但真实工作里，你对"回答方式"的期待并不总是一样。

学习一个陌生代码库时，你希望它解释每一步为什么这样做。赶交付时，你希望它少讲道理，直接推进。培养新人时，你希望它留下练习空间，而不是把代码全部写完。

这些差异不属于"项目规则"，也不属于"工具权限"。它们是回答风格。输出风格把这种回答偏好从临时提示词里抽出来，变成可切换的预设。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在分析 Claude Code 的上下文装配时指出，模型调用前的上下文来源包括 system prompt、环境信息、CLAUDE.md、路径规则、auto memory、工具元数据、对话历史、工具结果和 compact summary。

这篇论文的背景是：作者想解释 Claude Code 不是一次简单模型调用，而是一个会持续装配上下文、调用工具、压缩历史、恢复状态的 agent harness。

它在 Section 7.1 得出的结论是：output style modifications 和 `--append-system-prompt` 一起进入 system prompt 层，而 CLAUDE.md 则作为 user context 进入消息数组。

这个结论的依据来自上下文装配顺序：论文列出了系统提示、输出风格、环境信息、CLAUDE.md 层级、记忆、工具元数据和历史消息分别进入上下文的位置。

由此可以推断：输出风格主要改变模型的行为边界、角色和格式偏好，不改变工具能力，也不替代项目知识。它适合沉淀"怎么回答"，不适合沉淀"这个项目怎么构建"。

---

## 22.2 四种内置输出风格

Claude Code 常见的内置输出风格可以理解为四种工作姿态。

| 风格 | 适合场景 | 行为特征 |
| --- | --- | --- |
| Default | 日常开发 | 标准软件工程助手，解释适中，行动克制 |
| Proactive | 探索性任务、连续推进 | 更主动提出下一步，合理假设，偏向行动 |
| Explanatory | 学习代码库、技术迁移 | 完成任务时解释实现选择和代码库模式 |
| Learning | 教学、培养能力 | 分享洞察，但会留下 `TODO(human)` 让你自己实现小段代码 |

Default 适合大多数稳定任务。你已经知道要做什么，只需要 Claude Code 正常读代码、改代码、跑验证。

Proactive 适合目标明确但路径未完全展开的任务。比如"梳理这个模块的风险并给出修复建议"，你希望它不只回答当前问题，还能主动暴露下一步。

Explanatory 适合需要建立认知的任务。比如接手遗留系统、学习新的框架模式、理解某个 API 为什么这样设计。

Learning 适合训练人。它的价值不是降低你的参与度，而是刻意提高你的参与度。

---

## 22.3 Learning 模式：让 AI 不把活全干完

Learning 模式最特别。它不会把代码全部写完，而是在关键位置留下 `TODO(human)`，并给出提示，让你补上小段实现。

这看起来像"效率下降"，但它解决的是另一个问题：使用 AI 时，人很容易只接受结果，不理解结构。短期看，代码完成了；长期看，人的判断力下降了。

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》在讨论长期能力保存时引用了一组研究：AI 工具可能提升使用者的主观效率感，但也可能带来代码复杂度上升、理解下降和能力外包的问题。

这组引用的背景是：论文并不只评估 Claude Code 的短期生产力，而是追问 agent 系统是否会削弱人的长期工程能力。

它得到的结论是，能力放大和长期可持续之间存在张力。AI 能帮助人做更多事，但如果人不再练习判断、理解和验证，团队能力会被表面产出掩盖。

Learning 模式正是对这个张力的一种产品化回应。它把"人必须理解一部分关键逻辑"写进交互模式，让 AI 从替代者变成教练。

适合使用 Learning 模式的场景包括：

- 新人学习项目结构
- 代码审查培训
- 学习新框架或新语言
- 让 junior developer 练习边界条件
- 团队希望保留人的实现能力，而不是只追求吞吐量

不适合使用 Learning 模式的场景也很明确：线上故障修复、批量机械迁移、紧急交付、已经充分验证的自动化流水线。

---

## 22.4 自定义输出风格

自定义输出风格适合把团队反复使用的回答模式固定下来。

项目级输出风格放在：

```text
.claude/output-styles/*.md
```

个人全局输出风格放在：

```text
~/.claude/output-styles/
```

一个典型文件可以这样写：

```markdown
---
name: Diagrams first
description: Lead every explanation with a diagram
keep-coding-instructions: true
---

When explaining code, architecture, or data flow, start with a Mermaid diagram
showing the structure, then explain in prose. Keep diagrams under 15 nodes.
```

这里最容易忽略的是 `keep-coding-instructions: true`。

如果保留它，自定义风格会叠加在 Claude Code 内置的软件工程行为之上。也就是说，它仍然知道自己是 coding agent，只是回答方式变化。

如果不保留它，自定义风格可能替换掉默认编码指令。这适合写作助手、数据分析师等非编程角色，但不适合日常代码开发。

---

## 22.5 输出风格与提示技巧

输出风格解决的是长期稳定的回答偏好。单轮提示仍然要写清楚。

官方最佳实践里有四类提示技巧，和输出风格配合使用效果最好。

第一，限定范围。不要说"给 `foo.py` 添加测试"，而要说"为 `foo.py` 添加测试，覆盖用户已注销的边界情况，避免 mock"。

第二，指向来源。不要问"为什么这个 API 很奇怪"，而要说"查看这个 API 的 git 历史，总结它形成当前设计的过程"。

第三，参考模式。不要说"添加一个日历组件"，而要说"参考主页已有 widget 模式，按 `HotDogWidget.php` 的结构实现日历组件"。

第四，描述症状。不要说"修复登录错误"，而要说"用户报告会话超时后登录失败，请检查 token 刷新逻辑，先写失败测试再修复"。

这些技巧的共同点是：减少模型猜测空间。输出风格只决定"怎么说、怎么推进"，不能替你补全目标、验收标准和证据来源。

---

## 22.6 输出风格、System Prompt Flags 与 CLAUDE.md 的边界

Claude Code 有多种方式影响模型行为，容易混在一起。

| 机制 | 作用位置 | 适合内容 |
| --- | --- | --- |
| Output Styles | system prompt 的风格修改 | 回答角色、语气、输出格式、教学方式 |
| `--system-prompt` | 完全替换 system prompt | 脚本化、非标准角色、实验性运行 |
| `--append-system-prompt` | 追加 system prompt | 一次性脚本或自动化中的系统级约束 |
| CLAUDE.md | user context / 项目记忆 | 构建命令、代码风格、仓库约定、常见陷阱 |

《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》对这里的边界给了一个重要解释：CLAUDE.md 是 user context，不是 system prompt。

这个结论的背景是论文在拆解上下文层级。它发现 Claude Code 把 CLAUDE.md、日期等用户上下文 prepend 到消息数组，而不是混入 system prompt。

它的推论是：CLAUDE.md 更像项目说明和长期约定，遵循程度仍带有概率性；真正的确定性边界要靠权限系统和工具策略。

因此，不要把安全规则只写进输出风格或 CLAUDE.md。比如"不要删除生产数据"应当进入 deny rules、权限配置或 hook，而不是只写成一句提醒。

---

## 22.7 Proactive 模式与 Auto 模式不是一回事

Proactive 和 Auto 很容易被混淆。

Proactive 是输出风格。它影响 Claude Code 如何说话、如何建议下一步、是否更主动推进。

Auto 是权限行为。它影响某些工具调用是否自动批准、是否需要用户确认。

一个很主动的 Claude Code 仍然可能在每次 Bash 命令前询问你。一个处在 Auto 权限模式下的 Claude Code，也可以使用 Default 输出风格，回答得很克制。

实践上可以这样组合：

| 目标 | 输出风格 | 权限模式 |
| --- | --- | --- |
| 学习新项目 | Explanatory 或 Learning | default |
| 批量机械迁移 | Default | acceptEdits 或 auto |
| 探索架构风险 | Proactive | default |
| 后台验证流程 | Default | 受限 allowlist |

关键原则是：风格负责认知体验，权限负责执行边界。二者不能互相替代。

---

## 本章引用的论文与材料

- 《Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems》：用于解释输出风格在 context window assembly 中的位置，以及它和 CLAUDE.md、system prompt 的结构差异。
- 《Agent Harness Engineering: A Survey》：用于解释从 prompt engineering 到 context engineering、再到 harness engineering 的演进，说明输出风格只是更大 harness 体系中的一层。
- Claude Code 官方最佳实践：用于整理提示技巧、丰富内容输入、验证驱动和 CLAUDE.md 的配置边界。
- 《Claude Code 实战方法论》：用于补充 Learning 模式、四种输出风格和自定义输出风格的实操描述。
