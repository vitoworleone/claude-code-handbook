# 第14章 Hooks 系统

> 状态：初稿

Hooks 是 Claude Code 的**事件驱动拦截框架**。与 Skills（能力扩展）和 Memory（习惯指导）不同，Hooks 不扩展 Claude "能做什么"，而是在 Claude 执行过程中的**关键时刻介入**——拦截工具调用、注入额外上下文、阻止危险操作、或在特定事件发生时触发外部动作。本章先建立 Hooks 的概念模型与事件体系，再通过实战案例展示如何将 Hooks 用于安全防护、自动化工作流和团队协作治理。

---

## 14.1 Hook 是什么

### 14.1.1 核心定义

Hook 是一种**声明式的事件监听器**。你定义一个规则："当事件 X 发生时，执行动作 Y"，Claude Code 在运行时检测这些事件，并在触发时调用你指定的动作。

这个定义中有三个关键要素：

1. **事件（Event）**：Claude Code 运行时的特定时刻。例如 "工具即将执行"（PreToolUse）、"工具执行完毕"（PostToolUse）、"会话开始"（SessionStart）。
2. **条件（Condition）**：可选的过滤逻辑。例如 "仅当工具是 `Edit` 且目标文件匹配 `*.env` 时触发"。
3. **动作（Action）**：事件发生时要执行的具体操作。例如 "拒绝该工具调用"、"发送 Slack 通知"、"重写工具参数"。

**Hook 与 Skill 的核心区别**：

| 维度 | Skill | Hook |
|------|-------|------|
| 介入时机 | 被显式调用时注入上下文 | 在特定事件发生时自动触发 |
| 作用方式 | 扩展 Claude 的能力（"我知道怎么做 X"）| 干预 Claude 的执行（"当 Y 发生时，做 Z"）|
| 控制权 | 指导 Claude 的行为 | 拦截或改写 Claude 的行为 |
| 典型用途 | 封装特定任务的工作流程 | 安全防护、审计、自动化、治理 |

**一句话概括**：Skill 告诉 Claude "如何做事"，Hook 告诉 Claude "在特定时刻该做什么"。

### 14.1.2 四种命令类型

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Claude Code 的扩展机制时指出，Hooks 系统被设计为支持 **4 种命令类型**。为什么是 4 种而非 1 种？因为 hooks 需要处理不同复杂度的干预场景——简单的通知用 shell 命令就够了，复杂的安全决策需要 LLM 的语义理解能力。

#### `command` —— Shell 命令

- **是什么**：在 hook 触发时执行一条 shell 命令（或调用外部程序）。
- **适用场景**：需要执行系统级操作的简单 hook。例如发送系统通知、记录审计日志、调用格式化工具。
- **特点**：执行最快，成本最低，但缺乏语义理解能力。

**示例**：当 Claude 完成文件编辑后，自动运行 `prettier` 格式化。

#### `prompt` —— LLM Prompt

- **是什么**：在 hook 触发时，将指定的 prompt 发送给 LLM，根据返回结果决定后续动作。
- **适用场景**：需要语义理解的决策场景。例如判断一次代码修改是否引入了安全风险、评估工具调用的合理性。
- **特点**：利用了 LLM 的理解能力，但会产生额外的 token 成本和延迟。

**示例**：在 `PreToolUse` 时，让 LLM 判断 "这个 `rm` 命令是否安全"。

#### `http` —— HTTP 请求

- **是什么**：在 hook 触发时向指定的 URL 发送 HTTP 请求。
- **适用场景**：需要与外部服务集成的场景。例如将审计日志发送到 SIEM、在 Slack 中通知团队成员、调用内部的审批 API。
- **特点**：实现与外部基础设施的解耦集成。

**示例**：当 Claude 执行了 `Bash` 工具后，将命令和输出发送到团队的审计系统。

#### `agent` —— Agentic Verifier

- **是什么**：在 hook 触发时委派给一个专门的 Agent 进行验证或处理。
- **适用场景**：需要独立推理的复杂验证任务。例如"让另一个 agent 检查这次代码修改是否正确"、"让安全 agent 审查这个操作"。
- **特点**：最灵活但也最昂贵——创建了一个独立的 agent 上下文来完成验证。

**示例**：在 `PostToolUse` 时，让一个专门的代码审查 agent 检查刚才的编辑是否符合团队规范。

**为什么需要这四种类型？**

报告作者指出，这反映了 hook 设计中的**成本-能力权衡**：

| 类型 | 执行成本 | 语义能力 | 适用复杂度 |
|------|---------|---------|-----------|
| `command` | 最低 | 无 | 简单系统操作 |
| `http` | 低 | 无 | 外部服务集成 |
| `prompt` | 中等 | 有 | 需要理解的决策 |
| `agent` | 最高 | 最强 | 复杂验证任务 |

如果你只需要记录日志，用 `command` 或 `http` 即可；如果你需要判断"这个操作是否安全"，用 `prompt`；如果你需要"完整审查刚才的修改"，用 `agent`。选择正确的类型可以避免不必要的成本。

> **注**：除了这四种用户可配置的命令类型，系统还支持 `callback` 类型的 hooks。这类 hook 是 SDK/内部插桩使用的非持久回调，主要用于 Claude Code 自身的开发和调试，不对终端用户开放配置。

### 14.1.3 配置来源

Hooks 可以从四个来源加载，优先级由高到低：

| 来源 | 配置位置 | 典型用途 |
|------|---------|---------|
| **Managed policy** | 企业/组织级策略 | 团队强制执行的安全规则（如禁止删除生产数据库） |
| **Settings.json** | `~/.claude/settings.json` 或项目 `.claude/settings.json` | 个人或项目的自定义 hook |
| **Plugins** | 插件内部定义 | 插件附带的 hook（如某个插件注册 `FileChanged` hook 来自动重新索引） |
| **Skill hooks** | Skill 被调用时动态注册 | 临时性的、与特定 Skill 绑定的 hook |

**为什么需要四种来源？**

这提供了一种**渐进式治理**的能力：

- **Managed policy** 是强制性的——企业管理员可以定义全公司必须遵守的安全规则，个人用户无法覆盖。
- **Settings.json** 是个人或项目的——开发者可以根据自己的习惯配置自动化工作流。
- **Plugins** 是功能性的——插件作者可以在安装时自动注册必要的 hook。
- **Skill hooks** 是临时性的——只在 Skill 执行期间存在，Skill 结束后自动注销，避免污染全局状态。

**Skill hooks 的特殊性**：

Skill 可以在被调用时通过 `hooks` frontmatter 字段动态注册 hook。这意味着一个 Skill 不仅可以注入指令内容，还可以在执行期间"临时接管"某些事件的拦截权。例如，一个"数据库迁移" Skill 可以在执行期间注册 `PreToolUse` hook，阻止任何非迁移相关的 `Edit` 操作——这提供了一种**执行期沙箱**的能力。

---

## 14.2 Hooks 在 Agent Loop 中的位置

### 14.2.1 三个插入点

《Token Economy, Hit Rate, Tools, Skills and Context》报告中的 Figure 5 展示了 Claude Code 的 agent loop 有三个主要阶段。报告在分析 hooks 的插入点设计时指出，hooks 的分布不是随机的，而是遵循"**干预能力递增、干预成本递增**"的原则：

- **`assemble()` 阶段**：干预能力最弱（只能注入文本），但成本最低（不浪费模型推理）
- **`execute()` 阶段**：干预能力最强（可以阻止实际操作），但成本最高（模型已完成推理，拦截意味着浪费一次思考）

**为什么会有这个设计？**

报告作者解释，如果将所有拦截都放在 `execute()` 阶段，虽然可以获得最大控制权，但会导致严重的**推理浪费**——模型已经花费 token 思考并决定调用某个工具，如果此时才发现应该拦截，这次推理就白费了。相反，如果将拦截都放在 `assemble()` 阶段，虽然节省了推理成本，但缺乏对实际执行的干预能力（`assemble()` 阶段工具调用尚未发生，无法阻止已发生的错误）。

因此，设计者将 hooks 分布在不同阶段，让用户可以根据**干预的必要性和成本敏感度**选择正确的插入点：

Hooks 的插入点分布在这三个阶段中：

```
┌─────────────────────────────────────────────────────────────┐
│  Agent Loop                                                  │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  assemble()  │───→│   model()    │───→│  execute()   │   │
│  │  上下文组装   │    │   模型推理    │    │   工具执行    │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│         ↑                    ↑                  ↑           │
│    SessionStart        (间接控制)          PreToolUse       │
│    UserPromptSubmit    工具池过滤          PostToolUse      │
│    InstructionsLoaded                      PostToolUseFailure│
│    ConfigChange                            Stop             │
│    PreCompact / PostCompact                                │
│    ...                                                     │
└─────────────────────────────────────────────────────────────┘
```

**`assemble()` 阶段**：在上下文被组装成最终发送给模型的消息之前触发。此阶段的 hooks 可以**注入、修改或删除**上下文内容。

**`model()` 阶段**：Hooks 不直接插入此阶段，但通过 `PreToolUse` 的 `permissionDecision: 'deny'` 可以**间接控制工具池**——如果一个工具在 `PreToolUse` 阶段被拒绝，它就不会进入 `model()` 阶段的工具选择。

**`execute()` 阶段**：在工具实际执行前后触发。这是 hooks 最活跃的插入点，也是**干预能力最强**的阶段——可以直接阻止操作、修改参数、或在操作完成后追加动作。

### 14.2.2 事件与插入点的对应关系

| 插入点 | 主要事件 | 干预能力 |
|--------|---------|---------|
| `assemble()` | `SessionStart`, `UserPromptSubmit`, `InstructionsLoaded`, `ConfigChange`, `PreCompact`, `PostCompact` | **注入上下文**（低影响，无执行风险） |
| `model()`（间接）| `PreToolUse` 的 deny 决策 | **过滤工具池**（中等影响，阻止未发生的操作） |
| `execute()` | `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`, `Stop` | **拦截/改写执行**（高影响，直接操作执行流） |

### 14.2.3 执行顺序与优先级

当多个来源定义了同一个事件的 hook 时，Claude Code 按以下顺序执行：

1. **Managed policy hooks** 最先执行（企业级强制规则）
2. **Settings.json hooks** 其次执行（用户自定义规则）
3. **Plugin hooks** 再次执行（插件注册的功能性 hook）
4. **Skill hooks** 最后执行（临时性 hook）

**为什么是这个顺序？**

这确保了**治理优先级高于个人偏好**。例如，如果企业的 managed policy 定义了 "禁止修改 `production/` 目录"，个人用户无法在 `settings.json` 中覆盖这个规则——managed policy 的 `PreToolUse` hook 先执行并返回 `deny`，后续的 hooks 就不会再被调用。

**同一来源内的多个 hook**：

如果同一来源（如 `settings.json`）中定义了多个同事件 hook，它们通常按定义顺序依次执行。但需要注意：如果前一个 hook 返回了 `deny`，后续 hook 可能不会被调用（具体行为取决于事件类型和 Claude Code 版本）。

---

## 14.3 事件分类速查

Claude Code 共定义了 **27 个 hook 事件**。《Token Economy, Hit Rate, Tools, Skills and Context》报告在解析 `coreTypes.ts` 中的 hooks 类型定义时指出，其中 **15 个事件拥有事件特定的 output schema**。

**为什么会有这个设计差异？**

报告作者分析，这 27 个事件在 Claude Code 的 agent loop 中扮演着不同的角色。其中 12 个事件（如 `SessionStart`、`FileChanged`、`WorktreeCreate`）本质上属于"**通知型事件**"——它们的作用是告知外部系统"某事发生了"，不需要返回值来影响 Claude 的行为。而另外 15 个事件（如 `PreToolUse`、`PostToolUse`、`PermissionRequest`）属于"**决策型事件**"——它们发生在 Claude 需要做出关键决策的时刻，hook 的返回值可以直接改变决策结果。

例如 `PreToolUse` 的 output schema 包含 `permissionDecision`（允许/拒绝/询问）和 `updatedInput`（修改后的参数），这些字段直接决定了工具调用是否执行、以什么参数执行。如果没有 event-specific output schema，hook 只能被动观察，无法主动干预。

**这个结论能帮你做什么？**

当你选择要监听哪个 hook 事件时，**优先关注那 15 个有 output schema 的事件**——它们是你的"杠杆点"，可以通过返回值真正改变 Claude 的行为。而那 12 个通知型事件更适合用于审计、日志、监控等观察性用途。如果你发现某个场景无法通过现有 hook 解决，检查是否是因为你选择了"通知型事件"而错过了"决策型事件"。

以下按生命周期分组，每个事件给出「触发时机」和「典型用途」：

### 工具授权事件组（5 个，均有 event-specific output schema）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `PreToolUse` | 模型决定调用某个工具，但工具**尚未执行** | 拦截危险操作、重写工具参数、请求额外确认 |
| `PostToolUse` | 工具**已成功执行** | 注入额外上下文、触发后续自动化（如格式化）、记录审计日志 |
| `PostToolUseFailure` | 工具**执行失败**（抛出异常或返回错误） | 注入错误处理指导、自动重试逻辑、通知开发者 |
| `PermissionRequest` | Claude 准备向用户请求交互式权限确认 | 在对话框弹出前解析条件、自动批准低风险操作、丰富请求信息 |
| `PermissionDenied` | 用户在交互式确认中拒绝了权限请求 | 提供替代方案建议、记录拒绝原因、触发降级策略 |

### 会话生命周期事件组（5 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `SessionStart` | 新 Claude Code 会话开始时 | 注入项目上下文、加载团队规范、初始化环境 |
| `SessionEnd` | 会话正常结束时 | 保存状态、生成会话摘要、清理临时文件 |
| `Setup` | 项目首次设置或配置变更后 | 安装依赖、验证环境、生成初始配置 |
| `Stop` | Claude 准备停止响应（任务完成或用户中断） | 阻止停止（实现 "keep going" 模式）、保存中间结果 |
| `StopFailure` | 停止操作失败 | 错误恢复、强制清理 |

### 用户交互事件组（3 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `UserPromptSubmit` | 用户提交新消息时 | 预处理用户输入（如自动补全上下文）、注入系统指令 |
| `Elicitation` | Claude 需要向用户请求额外信息时 | 预处理问题、提供默认值、自动从环境推断 |
| `ElicitationResult` | 用户回答了 Elicitation 请求后 | 验证回答、转换格式、触发后续逻辑 |

### 子代理协调事件组（5 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `SubagentStart` | 子代理（subagent）启动时 | 注入子代理上下文、限制子代理权限、审计子代理调用 |
| `SubagentStop` | 子代理完成任务或停止时 | 收集子代理结果、记录性能指标、清理子代理资源 |
| `TeammateIdle` | 队友代理（teammate）空闲时 | 任务分发、负载均衡 |
| `TaskCreated` | 新任务被创建时 | 任务分类、优先级分配、通知相关人员 |
| `TaskCompleted` | 任务完成时 | 结果汇总、质量检查、触发下游工作流 |

### 上下文管理事件组（4 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `PreCompact` | 上下文即将被压缩（compaction）前 | 保存即将丢失的信息、标记重要上下文 |
| `PostCompact` | 上下文压缩完成后 | 验证压缩质量、注入压缩摘要、恢复被误删的信息 |
| `InstructionsLoaded` | 系统指令（instructions）被加载时 | 动态修改指令、注入版本信息、加载团队规范 |
| `ConfigChange` | 配置发生变更时 | 验证新配置、热重载、通知相关组件 |

### 工作空间事件组（4 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `CwdChanged` | 当前工作目录变更时 | 加载新目录的规范、切换环境变量、通知文件监视器 |
| `FileChanged` | 监控的文件发生变更时 | 自动重新加载、触发构建、更新索引 |
| `WorktreeCreate` | 新的 git worktree 被创建时 | 初始化 worktree 环境、同步配置 |
| `WorktreeRemove` | git worktree 被移除时 | 清理 worktree 资源、归档数据 |

### 通知事件组（1 个）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `Notification` | 需要向用户发送通知时 | 自定义通知渠道（Slack、邮件、系统通知）、格式化通知内容 |

---

## 14.4 关键事件深度解析

本章不逐一详解所有 27 个事件——那样会写成一本参考手册而非用户指南。我们聚焦于**工具授权事件组**，因为：

1. 它们拥有最丰富的 output schema（`permissionDecision`、`updatedInput`、`additionalContext` 等）
2. 它们是读者最可能实际使用的——安全防护、参数改写、审计日志
3. 它们与第13章的 `tools` frontmatter 形成互补：Skill 的 `tools` 字段限制"能用什么"，Hooks 的工具授权事件限制"什么时候能用"

### 14.4.1 工具授权事件组

#### `PreToolUse`

**触发时机**：模型已经决定调用某个工具，参数已确定，但工具**尚未执行**。

**输出 schema**（关键字段）：

```typescript
{
  permissionDecision: 'allow' | 'deny' | 'ask',  // 是否允许执行
  updatedInput?: object,                           // 修改后的工具参数
  additionalContext?: string,                      // 注入给模型的额外上下文
  message?: string                                 // 给用户的解释信息
}
```

**三种决策的含义**：

- **`allow`**：放行，工具按原计划执行。这是默认行为。
- **`deny`**：拒绝，工具不执行。Claude 会收到一个错误响应，提示操作被拒绝。
- **`ask`**：将决策权交还给用户。Claude 会弹出交互式确认对话框。

**`updatedInput` 的威力**：

`PreToolUse` 不仅可以二选一（允许/拒绝），还可以**重写工具参数**。这是 hooks 中最强大的能力之一——"放行但修正"。

**示例**：一个 `PreToolUse` hook 拦截 `Bash` 工具的 `rm` 命令：

```json
{
  "event": "PreToolUse",
  "condition": "tool.name == 'Bash' && tool.input.command.contains('rm')",
  "action": {
    "type": "command",
    "command": "echo '{\"permissionDecision\": \"allow\", \"updatedInput\": {\"command\": \"'${tool.input.command}' -i\"}}'"
  }
}
```

这个 hook 检测到 `rm` 命令时，不是简单拒绝，而是自动加上 `-i`（交互模式），让用户在删除每个文件前确认。

**为什么 `updatedInput` 比单纯的 `deny` 更有价值？**

因为实际场景中，很多"危险操作"只需要**参数 sanitization** 而非完全禁止。完全禁止会导致 Claude 无法完成合法任务（如清理临时文件），而参数改写可以在保留功能的同时降低风险。

#### `PostToolUse`

**触发时机**：工具**已成功执行**，结果已返回给 Claude。

**输出 schema**（关键字段）：

```typescript
{
  additionalContext?: string,      // 注入给模型的额外上下文
  updatedMCPToolOutput?: object,   // 修改 MCP 工具的输出
  actions?: Action[]               // 触发后续动作
}
```

**典型用途**：

1. **注入额外上下文**：工具执行后，向 Claude 补充一些它可能没有注意到的信息。例如，在 `Edit` 操作后，自动运行 linter 并将结果注入："注意：刚才的修改引入了 3 个 lint 错误..."

2. **触发后续自动化**：工具执行后自动触发其他操作。例如，在 `Write` 操作后自动格式化文件。

3. **修改 MCP 输出**：如果你通过 MCP 服务器调用了外部工具，可以在结果返回给 Claude 之前修改它（如过滤敏感信息、格式化输出）。

#### `PostToolUseFailure`

**触发时机**：工具**执行失败**（抛出异常、返回非零退出码、或产生错误输出）。

**输出 schema**（关键字段）：

```typescript
{
  additionalContext?: string,   // 注入错误处理指导
  retry?: boolean,              // 是否允许重试
  retryDelay?: number           // 重试延迟（毫秒）
}
```

**典型用途**：

1. **注入错误指导**：当 `Bash` 命令失败时，hook 可以分析错误输出并给 Claude 具体的修复建议。例如，如果 `npm install` 失败是因为网络问题，hook 可以注入："错误原因是网络超时，建议重试或使用镜像源。"

2. **自动重试**：对于瞬态错误（如网络超时、资源锁），hook 可以设置 `retry: true` 让 Claude 自动重试。

3. **降级策略**：当某个操作失败时，提供替代方案。例如，当 `docker build` 失败时，提示 "尝试使用 `--no-cache` 重新构建"。

#### `PermissionRequest`

**触发时机**：Claude 准备向用户展示交互式权限请求对话框之前。

**输出 schema**（关键字段）：

```typescript
{
  permissionDecision: 'allow' | 'deny' | 'ask',  // 覆盖默认行为
  message?: string                                // 修改请求对话框的文案
}
```

**典型用途**：

1. **自动批准低风险操作**：如果权限请求满足某些条件（如只读操作、只影响临时文件），hook 可以自动返回 `allow`，避免打扰用户。

2. **丰富请求信息**：在对话框中注入额外的上下文信息，帮助用户做出更明智的决策。例如，在请求 `Edit` 权限时，自动附加 "这个文件上次修改是 3 天前，由 Alice 提交"。

3. **基于上下文的决策**：如果当前在 "安全模式"（如通过环境变量或配置文件标记），可以自动拒绝某些权限请求。

#### `PermissionDenied`

**触发时机**：用户在交互式权限请求中点击了 "拒绝"。

**输出 schema**（关键字段）：

```typescript
{
  retryGuidance?: string,   // 给 Claude 的重试指导
  alternativeAction?: string // 替代操作建议
}
```

**典型用途**：

1. **提供替代方案**：当用户拒绝某个操作时，hook 可以建议 Claude 尝试其他方法。例如，用户拒绝了直接修改生产配置的请求，hook 可以建议 "创建 PR 而不是直接提交"。

2. **记录拒绝原因**：在企业环境中，可以自动记录谁拒绝了什么操作，用于合规审计。

3. **降级策略**：自动切换到更安全的执行模式。例如，用户拒绝了 `Bash` 权限，hook 可以建议 Claude 使用纯工具操作替代。

### 14.4.2 会话生命周期事件组

虽然这组事件的 output schema 相对简单，但它们是**治理和自动化**的重要入口。

#### `SessionStart`

**触发时机**：新 Claude Code 会话开始时（打开新终端、进入新项目目录、或重新启动）。

**典型用途**：

1. **注入项目上下文**：自动加载项目的 `CLAUDE.md`、`CONTRIBUTING.md` 或团队规范，无需用户手动提及。

2. **环境初始化**：检查必要的工具是否安装（如 `node`、`docker`），如果缺失则提示安装。

3. **加载团队记忆**：从团队知识库中加载与当前项目相关的记忆条目。

#### `SessionEnd`

**触发时机**：会话正常结束（退出终端、切换项目、或显式关闭）。

**典型用途**：

1. **生成会话摘要**：自动总结本次会话的主要工作和决策，写入 `SESSION_LOG.md`。

2. **保存中间状态**：如果会话中有未完成的任务，保存状态以便下次恢复。

3. **清理临时文件**：删除会话期间创建的临时文件或 worktree。

### 14.4.3 上下文管理事件组

#### `PreCompact` / `PostCompact`

**触发时机**：当对话历史接近上下文窗口上限时，Claude Code 会触发 **compaction**（压缩）——将早期的消息摘要化以腾出空间。`PreCompact` 在压缩前触发，`PostCompact` 在压缩后触发。

**为什么这组事件重要？**

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Claude Code 的上下文管理机制时指出，compaction（压缩）是应对 LLM 有限上下文窗口的**必要妥协**。当对话历史增长到一定长度时，Claude Code 必须将早期的消息摘要化，为新的对话内容腾出空间。但这个过程存在一个根本性的信息论问题：**摘要必然伴随信息损失**。

报告作者通过分析 `assemble()` 阶段的上下文压缩算法发现，compaction 的策略通常基于"时间远近"——最早的消息被优先压缩。但最早的消息往往包含用户最初提出的**关键约束**（如"不要修改 `config/` 目录"、"使用 TypeScript 而非 JavaScript"）。如果这些约束在压缩中被过度简化或丢失，Claude 在后续推理中就可能违反用户的初始意图。

`PreCompact` / `PostCompact` 这对 hook 的设计正是为了解决这个问题：

- `PreCompact` 让外部逻辑有机会在压缩前**标记不可丢失的信息**（例如将关键约束写入一个持久化的约束列表，或在消息中添加特殊标记使其被压缩算法保留）
- `PostCompact` 让外部逻辑在压缩后**验证信息完整性**（检查关键约束是否仍然存在于上下文中，如果丢失则重新注入）

**这个结论能帮你做什么？**

如果你发现 Claude 在长对话后期开始"忘记"用户早期提出的要求，这很可能是因为 compaction 导致了约束丢失。通过 `PreCompact` hook，你可以建立一个"关键约束清单"，在每次压缩前自动将当前约束写入一个持久化存储；通过 `PostCompact` hook，你可以检查这些约束是否仍在上下文中，如果不在就重新注入。这比依赖 Memory（概率性的）或反复提醒用户（体验差）更可靠。

**典型用途**：

1. **保存关键约束**：在 `PreCompact` 时，将用户明确提出的约束（如"不要修改 `config/` 目录"）写入一个持久化的约束列表，避免在压缩中丢失。

2. **验证压缩质量**：在 `PostCompact` 时，检查压缩后的摘要是否仍然包含关键信息，如果丢失则注入提醒。

3. **监控压缩频率**：记录 compaction 发生的频率和压缩比例，用于优化提示策略（如果频繁压缩，说明上下文使用效率低）。

---

## 14.5 常见玩法

本节通过**可复制的配置片段**展示 Hooks 的实际用法。每个案例包含：场景描述、配置代码、工作原理说明。

### 14.5.1 敏感文件保护

**场景**：阻止 Claude 修改 `.env`、密钥文件、或生产环境配置。

**配置**（`~/.claude/settings.json`）：

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "condition": "tool.name == 'Edit' || tool.name == 'Write'",
      "action": {
        "type": "command",
        "command": "if echo '${tool.input.file_path}' | grep -qE '\.(env|key|pem)$|/secrets?/|/production/'; then echo '{\"permissionDecision\": \"deny\", \"message\": \"修改敏感文件需要手动确认。请使用 git 或直接编辑。\"}'; else echo '{\"permissionDecision\": \"allow\"}'; fi"
      }
    }
  ]
}
```

**工作原理**：

- 在每次 `Edit` 或 `Write` 操作前，检查目标文件路径是否匹配敏感模式
- 如果匹配，返回 `deny` 并附带解释信息
- Claude 会收到一个错误，提示它无法修改该文件

**为什么用 `command` 而非 `prompt`**：

文件路径匹配是纯字符串操作，不需要语义理解。`command` 的执行成本远低于 `prompt`（不需要额外的 LLM 调用），且延迟更低。

### 14.5.2 自动格式化

**场景**：在 Claude 完成文件编辑后，自动运行项目的格式化工具（如 `prettier`、`black`、`gofmt`）。

**配置**（项目级 `.claude/settings.json`）：

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "condition": "tool.name == 'Edit' || tool.name == 'Write'",
      "action": {
        "type": "command",
        "command": "npx prettier --write '${tool.input.file_path}' 2>/dev/null || true"
      }
    }
  ]
}
```

**工作原理**：

- 在每次 `Edit` 或 `Write` 成功后，触发 `prettier` 格式化被修改的文件
- `2>/dev/null || true` 确保即使文件类型不被 prettier 支持，也不会导致 hook 失败

**进阶：注入格式化结果**

如果你希望 Claude 知道格式化后的变化，可以使用 `prompt` 类型：

```json
{
  "event": "PostToolUse",
  "condition": "tool.name == 'Edit'",
  "action": {
    "type": "prompt",
    "prompt": "文件 ${tool.input.file_path} 已被编辑。请检查是否需要运行格式化工具（如 prettier/eslint）。如果需要，执行格式化并确认结果。"
  }
}
```

这种方式更智能——Claude 可以判断是否真的需要格式化（避免对二进制文件或不支持格式的文件运行格式化），但成本更高（额外的 LLM 调用）。

### 14.5.3 危险命令拦截

**场景**：拦截潜在的破坏性 shell 命令（`rm -rf /`、`DROP TABLE`、`TRUNCATE` 等）。

**配置**（`~/.claude/settings.json`）：

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "condition": "tool.name == 'Bash'",
      "action": {
        "type": "command",
        "command": "if echo '${tool.input.command}' | grep -qiE 'rm +-rf */|DROP +TABLE|TRUNCATE|mkfs|dd +if=|>: */dev/'; then echo '{\"permissionDecision\": \"deny\", \"message\": \"检测到潜在破坏性命令。请使用更安全的方式完成此操作。\"}'; else echo '{\"permissionDecision\": \"allow\"}'; fi"
      }
    }
  ]
}
```

**局限性**：

正则匹配是**启发式**的，可能误报（如 `rm -rf ./temp/` 其实是安全的临时清理）也可能漏报（如命令被拆分成变量）。对于更严格的场景，可以改用 `prompt` 类型让 LLM 判断命令的风险等级：

```json
{
  "event": "PreToolUse",
  "condition": "tool.name == 'Bash'",
  "action": {
    "type": "prompt",
    "prompt": "请判断以下 shell 命令的风险等级（low/medium/high/critical）：${tool.input.command}。如果是 high 或 critical，建议拒绝执行并说明原因。"
  }
}
```

这种方式更准确但成本更高。建议组合使用：`command` 做快速过滤（拦截明显的危险命令），`prompt` 处理边界情况。

### 14.5.4 自定义通知

**场景**：当 Claude 完成耗时较长的任务时，发送系统通知。

**配置**（macOS 示例）：

```json
{
  "hooks": [
    {
      "event": "Stop",
      "condition": "session.duration > 300000",
      "action": {
        "type": "command",
        "command": "osascript -e 'display notification \"Claude 已完成长时间运行的任务\" with title \"Claude Code\"'"
      }
    }
  ]
}
```

**跨平台版本**（使用 `Notification` 事件）：

```json
{
  "hooks": [
    {
      "event": "Notification",
      "action": {
        "type": "http",
        "url": "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK",
        "method": "POST",
        "body": {
          "text": "Claude Code 通知: ${notification.message}"
        }
      }
    }
  ]
}
```

**工作原理**：

- `Stop` 事件在 Claude 准备结束当前任务时触发
- `session.duration > 300000` 条件确保只在耗时超过 5 分钟的任务完成后通知
- `osascript` 是 macOS 的系统通知工具；Linux 可以用 `notify-send`，Windows 可以用 PowerShell 的 `BurntToast`

### 14.5.5 审计日志（企业场景）

**场景**：记录所有 `Bash` 和 `Edit` 操作到中央审计系统。

**配置**（`~/.claude/settings.json`）：

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "condition": "tool.name == 'Bash' || tool.name == 'Edit'",
      "action": {
        "type": "http",
        "url": "https://audit.company.com/api/v1/claude-actions",
        "method": "POST",
        "headers": {
          "Authorization": "Bearer ${env.AUDIT_TOKEN}"
        },
        "body": {
          "user": "${env.USER}",
          "project": "${cwd}",
          "tool": "${tool.name}",
          "input": "${tool.input}",
          "timestamp": "${now}"
        }
      }
    }
  ]
}
```

**工作原理**：

- 每次 `Bash` 或 `Edit` 操作成功后，将操作详情发送到审计系统
- 使用环境变量注入敏感信息（如 `AUDIT_TOKEN`），避免硬编码密钥
- 可以扩展到 `PreToolUse` 来记录"被拒绝的操作"（安全事件的完整视图）

---

## 14.6 边界区分：Memory / Skill / Hook / Permission rules

Claude Code 提供了四种机制来影响其行为，但它们的设计意图和适用场景截然不同。理解它们的边界可以帮助你选择正确的工具。

### 对比矩阵

| 维度 | Memory | Skill | Hook | Permission rules |
|------|--------|-------|------|------------------|
| **作用方式** | 注入指导性文本 | 注入任务指令 | 拦截/改写执行 | 基于规则的硬拦截 |
| **生效时机** | 常驻上下文 | 按需注入 | 特定事件触发 | 工具调用前 |
| **确定性** | 概率性（Claude 可能遵循也可能不遵循）| 概率性（Claude 可能不调用）| 确定性（强制生效）| 确定性（强制生效）|
| **干预粒度** | 全局指导 | 任务级别 | 事件级别 | 操作级别 |
| **配置复杂度** | 低（纯文本）| 中（frontmatter + 正文）| 高（事件 + 条件 + 动作）| 低（规则列表）|
| **典型用途** | 习惯养成、偏好表达 | 能力扩展、工作流封装 | 安全防护、自动化、治理 | 简单访问控制 |

### 如何选择？

**场景 1：我希望 Claude 在审查代码时关注安全性**

- ❌ 不要用 Hook：这不是"拦截"，而是"指导"
- ✅ 用 Memory：在 `~/.claude/memory.md` 中写 "在代码审查时，请特别关注 SQL 注入和 XSS 风险"
- ✅ 或用 Skill：创建一个 `security-review` Skill，包含详细的安全检查清单

**场景 2：我希望阻止 Claude 修改 `.env` 文件**

- ✅ 首选 Permission rules：在 `settings.json` 中定义 `"permissions": [{"action": "deny", "path": "*.env"}]`——这是最简单的方案
- ✅ 也可以用 Hook：如果需要在拒绝时执行额外动作（如发送通知、记录日志），用 `PreToolUse` hook
- ❌ 不要用 Memory：Memory 是概率性的，无法保证拦截

**场景 3：我希望在 Claude 完成编辑后自动格式化代码**

- ❌ 不要用 Permission rules：这不是访问控制
- ❌ 不要用 Memory：Memory 无法触发动作
- ✅ 用 Hook：`PostToolUse` hook 在 `Edit`/`Write` 后运行格式化工具
- ✅ 也可以用 Skill：创建一个 `smart-edit` Skill，在指令中明确要求 "编辑后运行格式化"——但 Skill 是概率性的，Hook 是确定性的

**场景 4：我希望团队统一使用特定的代码风格**

- ✅ 用 Skill：创建一个 `format-code` Skill，封装团队的格式化流程
- ✅ 用 Hook：`PostToolUse` hook 强制执行格式化（作为后备保障）
- ✅ 用 Memory：在团队共享记忆中描述代码风格偏好
- **最佳实践**：三者结合——Skill 提供"怎么做"，Memory 提供"为什么这么做"，Hook 提供"不这么做会怎样"的强制保障

### 一句话决策指南

| 你想做什么 | 使用 |
|-----------|------|
| "我希望 Claude 记住并倾向于..." | Memory |
| "我需要 Claude 能执行某种特定任务..." | Skill |
| "我必须在某个关键时刻强制拦截或自动触发..." | Hook |
| "我只需要简单的文件/命令黑白名单..." | Permission rules |
