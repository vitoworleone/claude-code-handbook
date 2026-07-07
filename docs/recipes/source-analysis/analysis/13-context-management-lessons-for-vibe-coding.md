# 第十三章：Context Management 对 Vibe Coding 与 Skills 设计的启发

[返回总目录](../README.md)

---

## 1. 这篇文档解决什么问题

如果把 Claude Code 的 Context Management 只理解成“上下文太长时做压缩”，那么很容易低估它真正的设计价值。

对想要提升 **vibe coding** 和 **skills 设计** 的人来说，这一层真正值得研究的不是压缩算法本身，而是下面四个判断：

1. 上下文的核心单位不是消息，而是工作状态
2. 稳定上下文要会话级缓存，变化上下文要按需补
3. 压缩系统必须从一开始就为自己预留 headroom
4. skill 设计不能脱离 compaction，因为 skill 一旦占太多 token，就必须考虑压缩后怎么保住它

这四条并不是抽象方法论，而是 Claude Code 在源码里明确体现出来的运行时设计。

---

## 2. 判断一：上下文的核心单位不是消息，而是工作状态

### 2.1 很多系统的默认误区

很多 LLM 应用在设计上下文时，默认单位是“消息”：

- 保留最近 20 条消息
- 超过长度就删前面的消息
- 压缩时只总结聊天历史

这种设计适合问答系统，但不适合 coding agent。

因为对 coding agent 来说，真正重要的往往不是“之前说过什么”，而是：

- 当前仓库处于什么状态
- 最近看过哪些文件
- 当前正在执行什么计划
- 当前打开了哪些工具能力
- 现在是否在某种特殊模式中
- 某个 skill 是否已经被调用，并且仍在当前任务链路中生效

Claude Code 的源码明显是在按“工作状态”组织上下文，而不是按“消息条数”组织上下文。

### 2.2 会话前缀上下文：一开始就不是消息思维

先看 [`src/context.ts`](../src/context.ts)。

[`getSystemContext()`](../src/context.ts) 和 [`getUserContext()`](../src/context.ts) 都不是在“消息流中间”插入内容，而是构造整段会话前缀上下文：

- `getSystemContext()` 注入 git 相关信息
- `getUserContext()` 注入 `CLAUDE.md` 和日期

关键逻辑如下：

```typescript
export const getSystemContext = memoize(async () => {
  const gitStatus =
    isEnvTruthy(process.env.CLAUDE_CODE_REMOTE) ||
    !shouldIncludeGitInstructions()
      ? null
      : await getGitStatus()

  return {
    ...(gitStatus && { gitStatus }),
  }
})

export const getUserContext = memoize(async () => {
  const claudeMd = shouldDisableClaudeMd
    ? null
    : getClaudeMds(filterInjectedMemoryFiles(await getMemoryFiles()))

  return {
    ...(claudeMd && { claudeMd }),
    currentDate: `Today's date is ${getLocalISODate()}.`,
  }
})
```

可以看到，这里注入的不是聊天内容，而是：

- 仓库状态
- 项目约束
- 时间基线

这就是典型的工作台上下文。

### 2.3 `git status` 不是信息展示，而是工作状态快照

[`getGitStatus()`](../src/context.ts) 做的事情很有代表性：

- 获取当前分支
- 获取主分支
- 获取 `git status --short`
- 获取最近 5 次提交
- 获取 git 用户名
- 对超长 status 做截断

源码里还明确说明这是一份“对话开始时的快照”：

```typescript
return [
  `This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.`,
  `Current branch: ${branch}`,
  `Main branch (you will usually use this for PRs): ${mainBranch}`,
  ...(userName ? [`Git user: ${userName}`] : []),
  `Status:\n${truncatedStatus || '(clean)'}`,
  `Recent commits:\n${log}`,
].join('\n\n')
```

这里最重要的设计判断是：

> Claude Code 认为“当前仓库起始状态”是模型应该知道的工作背景，而不是用户必须手工再说一遍的信息。

这对 vibe coding 非常重要。因为很多 coding agent 的表现差，不是模型不会写代码，而是它根本不知道当前工作区处于什么状态。

### 2.4 压缩后恢复的也不是消息，而是工作状态

再看 [`src/services/compact/compact.ts`](../src/services/compact/compact.ts)。

压缩完成后，Claude Code 不会只留下一个 summary，而是会补回一整套工作状态：

```typescript
const [fileAttachments, asyncAgentAttachments] = await Promise.all([
  createPostCompactFileAttachments(...),
  createAsyncAgentAttachmentsIfNeeded(context),
])

const planAttachment = createPlanAttachmentIfNeeded(context.agentId)
const planModeAttachment = await createPlanModeAttachmentIfNeeded(context)
const skillAttachment = createSkillAttachmentIfNeeded(context.agentId)

for (const att of getDeferredToolsDeltaAttachment(...)) { ... }
for (const att of getAgentListingDeltaAttachment(context, [])) { ... }
for (const att of getMcpInstructionsDeltaAttachment(...)) { ... }
```

它恢复的对象包括：

- 最近读过的文件
- 异步 agent 状态
- 当前 plan 文件
- 当前 plan mode
- 已调用 skills
- 当前工具集合
- agent listing
- MCP 指令上下文

这说明 Claude Code 真正保的是“继续干活所需的工作面板”，不是纯历史聊天。

### 2.5 对你的直接启发

如果你想提升自己的 vibe coding 系统，第一条原则应该是：

- 不要问“最近保留多少条消息”
- 要问“模型继续推进任务最少需要保留哪些工作状态”

你应该优先维护的是：

- 项目规则
- 仓库状态
- 当前计划
- 当前已打开文件
- 当前已启用能力
- 当前已调用关键技能

---

## 3. 判断二：稳定上下文要会话级缓存，变化上下文要按需补

### 3.1 不是所有上下文都应该常驻

Claude Code 非常明确地区分了两类上下文：

1. **稳定上下文**
   - 在一个会话中基本不变
   - 适合缓存
2. **变化上下文**
   - 只在某个任务、某个阶段、某次 skill 调用时有意义
   - 适合按需补入

这个区别直接决定 prompt 质量与 token 成本。

### 3.2 稳定上下文：会话级缓存

`context.ts` 几乎是在明示这一点：

- [`getGitStatus()`](../src/context.ts) 使用 `memoize`
- [`getSystemContext()`](../src/context.ts) 使用 `memoize`
- [`getUserContext()`](../src/context.ts) 使用 `memoize`

相关代码：

```typescript
export const getGitStatus = memoize(async (): Promise<string | null> => { ... })
export const getSystemContext = memoize(async (): Promise<{ [k: string]: string }> => { ... })
export const getUserContext = memoize(async (): Promise<{ [k: string]: string }> => { ... })
```

这意味着：

- `git status` 不需要每轮重复计算
- `CLAUDE.md` 不需要每轮重新扫描
- 日期和系统级前缀上下文不需要每轮重复拼接

只有在某些特定状态变化时，缓存才会失效。例如：

```typescript
export function setSystemPromptInjection(value: string | null): void {
  systemPromptInjection = value
  getUserContext.cache.clear?.()
  getSystemContext.cache.clear?.()
}
```

这不是普通的“性能优化”，而是上下文分层策略的一部分。

### 3.3 变化上下文：按需注入 skill / attachment

变化上下文的典型代表就是 skill。

看 [`src/utils/processUserInput/processSlashCommand.tsx`](../src/utils/processUserInput/processSlashCommand.tsx)。

当用户调用 prompt 型 slash command 时，Claude Code 并没有把 skill 内容塞进全局 system prompt，而是把 skill 编译成一组新的消息与 attachment：

```typescript
const messages = [
  createUserMessage({ content: metadata, uuid }),
  createUserMessage({
    content: mainMessageContent,
    isMeta: true,
  }),
  ...attachmentMessages,
  createAttachmentMessage({
    type: 'command_permissions',
    allowedTools: additionalAllowedTools,
    model: command.model,
  }),
]
```

这里有两个关键点：

1. skill 主体内容进入的是一条 `isMeta: true` 的消息
2. skill 对工具权限和模型的影响，通过独立 attachment 传递

也就是说，skill 被当成一次任务性的上下文补丁，而不是永久并入系统人格。

### 3.4 SkillTool 也是同样的思路

在 [`src/tools/SkillTool/SkillTool.ts`](../src/tools/SkillTool/SkillTool.ts) 中，skill 被调用后，也会被直接包成 meta user message：

```typescript
return {
  data: { success: true, commandName, status: 'inline' },
  newMessages: tagMessagesWithToolUseID(
    [createUserMessage({ content: finalContent, isMeta: true })],
    toolUseID,
  ),
}
```

这再次说明：

> Claude Code 认为 skill 是局部任务上下文，不是全局人格配置。

### 3.5 对你的直接启发

这条对 skill 设计特别重要。

不要把所有规则都塞进 system prompt。更好的划分方式是：

#### 放在全局上下文里的内容

- 长期稳定的项目规则
- 角色边界
- 安全策略
- 通用协作约定

#### 放在 skill / attachment 里的内容

- 当前任务 SOP
- 特定工作流模板
- 某类文件的处理准则
- 一次性的检查清单
- 当前任务专属的额外工具权限

一句话概括：

> 稳定规则常驻，任务规则局部注入。

---

## 4. 判断三：压缩系统必须从一开始就为自己预留 headroom

### 4.1 很多系统的问题不是不会压缩，而是压得太晚

如果一个系统总是等到上下文窗口已经贴边，才开始做 compact，那么它很容易进入下面这种死锁：

1. 原始历史太长，继续采样放不下
2. 想请求 summary，也放不下
3. 于是既不能继续对话，也不能完成压缩

Claude Code 很明显是围绕这个问题做了预算设计。

### 4.2 有效窗口不是模型总窗口

看 [`src/services/compact/autoCompact.ts`](../src/services/compact/autoCompact.ts)：

```typescript
const MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

export function getEffectiveContextWindowSize(model: string): number {
  const reservedTokensForSummary = Math.min(
    getMaxOutputTokensForModel(model),
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
  )
  let contextWindow = getContextWindowForModel(model, getSdkBetas())
  return contextWindow - reservedTokensForSummary
}
```

这段代码反映了一个非常关键的思想：

> 可用上下文窗口 = 模型窗口 - 为未来 compact summary 预留的输出空间

也就是说，Claude Code 从一开始就把 compact 当成运行时的一部分，而不是故障应急。

### 4.3 自动压缩阈值也提前留缓冲

继续看 [`autoCompact.ts`](../src/services/compact/autoCompact.ts)：

```typescript
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000

export function getAutoCompactThreshold(model: string): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(model)
  const autocompactThreshold =
    effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS
  return autocompactThreshold
}
```

这意味着 Claude Code 不是等“有效窗口用满了”才压缩，而是在离上限还有一段安全距离时就触发 compact。

### 4.4 还有熔断和递归防护

Claude Code 不只预留 headroom，还防止压缩系统自我伤害。

#### 连续失败熔断

```typescript
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3

if (
  tracking?.consecutiveFailures !== undefined &&
  tracking.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
) {
  return { wasCompacted: false }
}
```

#### 递归防护

```typescript
if (querySource === 'session_memory' || querySource === 'compact') {
  return false
}
```

这两段代码说明 Claude Code 不是“看到超限就压”，而是把 compact 当成一个也会失败、也会死锁、也需要治理的子系统。

### 4.5 对你的直接启发

如果你自己的 agent 会做长会话工作，那么你不能只设计 prompt，还必须设计：

- 有效窗口定义
- compact 预留预算
- 触发阈值
- 连续失败熔断
- 哪些子流程不能触发 compact

换句话说：

> headroom 不是性能优化，而是 agent 稳定性的组成部分。

---

## 5. 判断四：skill 设计不能脱离 compaction

### 5.1 skill 在 Claude Code 里不是一次性文本

Claude Code 一旦调用某个 skill，并不会把它当成“已经过去的提示词”。它会把这个 skill 记录成当前工作状态的一部分。

先看 [`src/bootstrap/state.ts`](../src/bootstrap/state.ts)。

全局状态里专门有一个 `invokedSkills`：

```typescript
// Track invoked skills for preservation across compaction
invokedSkills: Map<
  string,
  {
    skillName: string
    skillPath: string
    content: string
    invokedAt: number
    agentId: string | null
  }
>
```

记录函数如下：

```typescript
export function addInvokedSkill(
  skillName: string,
  skillPath: string,
  content: string,
  agentId: string | null = null,
): void {
  const key = `${agentId ?? ''}:${skillName}`
  STATE.invokedSkills.set(key, {
    skillName,
    skillPath,
    content,
    invokedAt: Date.now(),
    agentId,
  })
}
```

这里的设计非常明确：

- skill 会被记录
- skill 有时间戳
- skill 按 agent 作用域隔离
- skill 不是瞬时的，而是要跨后续会话轮次保留

### 5.2 skill 调用时就注册保留状态

这个记录动作发生在真正的 skill 调用路径上。

#### slash command 路径

[`processSlashCommand.tsx`](../src/utils/processUserInput/processSlashCommand.tsx)：

```typescript
const skillPath = command.source ? `${command.source}:${command.name}` : command.name
const skillContent = result
  .filter((b): b is TextBlockParam => b.type === 'text')
  .map(b => b.text)
  .join('\n\n')

addInvokedSkill(
  command.name,
  skillPath,
  skillContent,
  getAgentContext()?.agentId ?? null,
)
```

#### SkillTool 路径

[`SkillTool.ts`](../src/tools/SkillTool/SkillTool.ts)：

```typescript
addInvokedSkill(
  commandName,
  skillPath,
  finalContent,
  getAgentContext()?.agentId ?? null,
)
```

注意这里保存的是 `finalContent`，不是原始 skill 文件内容。这非常重要，因为它意味着：

- 路径变量替换后的内容会被保留
- skill 运行时注入后的形态会被保留
- compact 恢复时，拿到的是“实际生效过的 skill 内容”

### 5.3 compaction 时会专门恢复 invoked skills

看 [`createSkillAttachmentIfNeeded()`](../src/services/compact/compact.ts)：

```typescript
export function createSkillAttachmentIfNeeded(
  agentId?: string,
): AttachmentMessage | null {
  const invokedSkills = getInvokedSkillsForAgent(agentId)

  const skills = Array.from(invokedSkills.values())
    .sort((a, b) => b.invokedAt - a.invokedAt)
    .map(skill => ({
      name: skill.skillName,
      path: skill.skillPath,
      content: truncateToTokens(
        skill.content,
        POST_COMPACT_MAX_TOKENS_PER_SKILL,
      ),
    }))
    .filter(skill => {
      const tokens = roughTokenCountEstimation(skill.content)
      if (usedTokens + tokens > POST_COMPACT_SKILLS_TOKEN_BUDGET) {
        return false
      }
      usedTokens += tokens
      return true
    })

  return createAttachmentMessage({
    type: 'invoked_skills',
    skills,
  })
}
```

这里有三个关键点：

1. 只恢复当前 agent 作用域下的 skills
2. 最近使用的 skill 优先保留
3. 每个 skill 和整体 skills 都有 token budget

这已经不是“prompt 存档”，而是明确的运行时恢复协议。

### 5.4 Claude Code 甚至专门避免清掉 skill 保留状态

看 [`src/services/compact/postCompactCleanup.ts`](../src/services/compact/postCompactCleanup.ts)：

```typescript
// Note: We intentionally do NOT clear invoked skill content here.
// Skill content must survive across multiple compactions so that
// createSkillAttachmentIfNeeded() can include the full skill text
// in subsequent compaction attachments.
```

这段注释几乎是在直白地告诉我们：

> skill 设计必须考虑 compaction，因为已调用 skill 是需要跨多次 compact 保留下来的运行时资产。

### 5.5 对你的直接启发

如果你想设计自己的 skills，这里有几个非常硬的约束：

#### 1. skill 头部必须最重要

因为 Claude Code 在 compact 时对 skill 会按 token 截断，[compact.ts](../src/services/compact/compact.ts) 采用的是“保留前部”的策略。

这意味着：

- skill 文件最关键的规则应写在前面
- 不要把真正重要的约束埋在文末

#### 2. skill 不应该无限膨胀

一旦 skill 很大，它在 compact 后就会面临：

- 被截断
- 被预算淘汰
- 挤占其他工作状态的 token

所以 skill 不应该写成百科全书，而应该写成：

- 边界明确
- 任务单一
- 可局部调用

#### 3. skill 应按 agent / 任务作用域隔离

Claude Code 专门用 `agentId` 隔离 skill 状态，避免跨 agent 泄漏。

如果你未来要做 subagent / role-specific workflows，也应该照这个思路设计：

- skill 状态按角色隔离
- 恢复时只恢复当前角色相关内容

---

## 6. 对 Vibe Coding 与 Skills 设计的直接方法论

基于上面的源码分析，可以把具体实践建议收敛成下面几条。

### 6.1 不要把所有规则都塞进 system prompt

应该放进 system prompt / 全局上下文的是：

- 长期稳定的人设与边界
- 项目级规则
- 安全限制
- 工作区长期约束

不应该全部塞进去的是：

- 单个任务 SOP
- 一次性流程
- 某种特定工作流的长清单
- 当前任务的额外能力说明

### 6.2 把稳定规则放在全局上下文，把任务性规则放在 skill 或 attachment

更接近 Claude Code 的做法是：

- 全局上下文：基础工作台
- skill：局部任务指导
- attachment：结构化运行时状态补充

### 6.3 skill 最好在需要时补进来，而不是长期常驻

Claude Code 的 skill 注入方式说明它更倾向：

- 调用时注入
- 调用后追踪
- 压缩时按需恢复

而不是：

- 一开始把所有 skill 都挂进全局 prompt

### 6.4 如果 agent 会长会话运行，就必须设计压缩后重建清单

你自己的系统至少应该明确：

- 哪些文件要恢复
- 当前 plan 怎么恢复
- 当前模式怎么恢复
- 已调用 skills 怎么恢复
- 工具可用集怎么恢复
- 当前后台任务状态怎么恢复

如果这些都没有定义，那么你的长会话系统只是在“拖延遗忘”，而不是“管理上下文”。

---

## 7. 最后总结

Claude Code 的 Context Management 给 vibe coding 和 skills 设计带来的真正启发，不是“它会自动压缩上下文”，而是：

1. 它把上下文当作工作台来设计
2. 它明确区分稳定上下文与任务上下文
3. 它从预算阶段就考虑 compact，而不是事后补救
4. 它把 skill 当作会进入运行时状态、并需要跨 compact 保留的对象

一句话总结：

> 想把 coding agent 做得更像真正的工作伙伴，不能只研究 prompt 怎么写，还要研究上下文怎样被组织、怎样被压缩、以及怎样在压缩后继续保持工作状态。
