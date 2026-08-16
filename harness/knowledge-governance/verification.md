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

## Sources

- [Building verification loops in Claude Code with skills](https://claude.com/blog/building-verification-loops-in-claude-code-with-skills)
- [Using LLMs to secure source code](https://claude.com/blog/using-llms-to-secure-source-code)
- [How Anthropic secures its AI-native software development lifecycle](https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle)
