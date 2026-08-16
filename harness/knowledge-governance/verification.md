# Verification Loops

> 不把“另一个 AI 说没问题”当作验证。让 AI 的结论变成可检查的证据；让人审查证据、风险和取舍，而不是逐行兜底。

## Why a Loop Is Needed

AI 的首次产出不等于完成。即使由另一个 AI review，也可能因为共享盲点、误读规格或缺少运行时证据而遗漏错误。

可靠的工作流应形成闭环：

```text
人定义目标与验收标准
→ AI 实现或撰写
→ 确定性验证
→ 独立 AI 尝试证伪并举证
→ 按风险决定人工批准
→ 真实错误沉淀为后续回归验证
```

## Define Completion Evidence First

每个重要任务除了描述“做什么”，还应说明“如何证明做对”。

```md
## Acceptance criteria

- 用户只能访问被授权的资源。
- 原有接口和响应格式保持兼容。
- 相关集成测试必须通过。
- 文档中的功能声明必须有发布、测试或运行结果支持。
```

能自动判断的交给测试、lint、类型检查、链接检查或运行时验证；不能完全自动判断的交给 rubric、典型案例或人工判断。

## Require a Proof Bundle

AI 的交付应附带证据，而不只是结论：

- 改动摘要；
- 已运行的命令及结果；
- 每项验收标准对应的证据；
- 可复现的失败测试、脚本或运行结果；
- 尚未验证的假设与需要人工确认的风险。

这让 review 的对象从“AI 自己说完成了”变成可检查的事实。

## Keep AI Review Narrow and Independent

不要使用宽泛的“请 review 一下”。应将 reviewer 限定在具体风险，并要求每条发现给出文件位置、攻击路径、复现步骤或其他证据。

```text
只审查跨租户数据访问与授权绕过。
不要根据命名猜测行为。
每条问题必须提供证据；没有证据则标记为无法判断。
```

发现问题的 agent 与验证问题的 agent 不应共享完整推理过程。验证者应尝试基于代码、规格和可复现证据重新证伪，而不是默认接受前一个 agent 的判断。

## Preserve Human Judgment by Risk

| 验证对象 | 主责任 |
| --- | --- |
| 构建、类型、lint、明显回归 | 自动化工具 |
| 明确规格是否满足 | 测试和 spec validation |
| 潜在漏洞、遗漏边界、反例 | 独立 AI verifier |
| 产品价值、架构取舍、风险是否可接受 | 人 |
| 权限、支付、隐私、生产变更 | 领域负责人审批 |

人类把控不仅是最后一次 code review，还包括为 agent 设定权限、工具和环境边界。

## Start Small

在每个重要任务开始前，先回答：

1. 验收标准是什么？
2. 用什么证据验证？
3. 哪一个风险必须由人确认？

重复出现的人工检查，才是进入自动化 verification loop 的候选项。

## Build Completion Evidence Before Implementation

验收标准不是“写得不错”或“功能看起来没问题”，而是任务完成后可以观察到的证据。一个实用的标准应同时覆盖：

| 类别 | 需要证明什么 | 常见证据 |
| --- | --- | --- |
| 行为 | 系统是否完成用户任务 | 集成测试、端到端测试、运行结果 |
| 兼容性 | 原有能力是否被破坏 | 回归测试、接口契约、快照 |
| 边界 | 权限、失败路径和限制是否正确 | 负向测试、PoC、模拟异常 |
| 文档 | 描述是否与真实能力一致 | 代码、测试、正式发布信息、可运行示例 |
| 风险 | 哪些结论仍依赖假设 | 明确的未验证项与人工审批 |

这并不要求每项都先写完整自动化测试。它要求团队在动手前承认：哪些部分可以被证明，哪些部分只能被评审，哪些部分必须由业务负责人决定。

## Deterministic Checks, Rubrics, and Human Approval Solve Different Problems

| 机制 | 最适合的问题 | 不应承担的问题 |
| --- | --- | --- |
| lint / schema / link check | 格式、术语、引用、结构和静态规则 | 产品价值与领域取舍 |
| test / runtime verification | 行为、兼容性、权限、回归 | 没有明确 oracle 的体验判断 |
| spec validation | 是否满足明确、可观察的规格 | 规格本身是否完整或合理 |
| AI rubric | 遗漏、反例、覆盖度、表达和开放式质量 | 替代证据或最终风险责任 |
| human approval | 价值、权衡、模糊边界和高风险授权 | 重复的机械检查 |

如果一个要求可以由脚本稳定判断，却仍然写在 prompt 中要求 AI “记得遵守”，那它还没有真正进入验证系统。

## A Useful Review Contract

为独立 verifier 设计输入时，至少给出：

```text
审查范围：只检查什么风险？
允许的证据：代码、测试、运行结果、规格、PoC
报告标准：每个问题必须包含什么证据？
不确定策略：什么情况下输出 Unknown？
严重程度：什么会阻塞，什么只是建议？
```

例如：

```text
只审查租户隔离和授权绕过。
不要根据变量名或注释猜测行为。
每个发现必须附上文件位置、攻击路径或可运行复现。
证据不足时返回 Unknown，不要把猜测报告成漏洞。
```

这类窄职责 reviewer 通常比一个“万能 reviewer”更容易校准，也更容易判断它是否真的有效。

## Verification Is a Learning Loop

验证不仅检查本次交付，也生产未来规则：

```text
人工或生产环境发现错误
→ 记录失败场景和影响
→ 判断能否写成测试、lint、spec check 或 rubric
→ 加入回归集
→ 后续任务持续运行
```

新的错误不应只变成“下次提醒 AI 小心一点”。如果它有重复价值，就应被转化成可执行检查、评审案例或明确的人工审批边界。

## Avoid the Illusion of a Single Quality Gate

没有单个测试、单个 AI reviewer 或单个人类审批可以捕捉所有失败。可靠系统依赖多层防线：确定性工具提供快速信号，独立 AI 提供反例搜索，人工处理目标和风险，生产反馈暴露分布外问题。

因此，验证的目标不是产生一个“通过”标签，而是不断缩小未被证明的部分，并让残余风险对人可见。

## Sources

- [Building verification loops in Claude Code with skills](https://claude.com/blog/building-verification-loops-in-claude-code-with-skills)
- [Using LLMs to secure source code](https://claude.com/blog/using-llms-to-secure-source-code)
- [How Anthropic secures its AI-native software development lifecycle](https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle)
