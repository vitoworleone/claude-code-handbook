# 第13章 Skills 系统

> 状态：初稿

Skills 是 Claude Code 的**声明式能力扩展机制**。与 Command 相比，它提供更精细的调用控制、更低的上下文成本、以及更强的封装能力——支持文件 bundle、执行隔离、模型覆盖和动态上下文注入。本章前半部分系统讲解 Skills 的概念、字段与工作机制，后半部分通过两个完整案例展示如何编写生产级 Skill。

---

## 13.1 Skill 是什么

### 13.1.1 基本构成

一个 Skill 在文件系统上表现为**一个目录或一个文件**，其核心是名为 `SKILL.md` 的 Markdown 文件，采用 YAML frontmatter + Markdown 正文的结构：

```markdown
---
name: my-skill
description: 当用户需要执行 X 操作时，调用此 Skill
---

# 指令正文

这里写 Skill 被调用后 Claude 应该执行的具体步骤...
```

**Skill 的完整构成包括：**

| 组成部分 | 必需 | 说明 |
|---------|------|------|
| `SKILL.md` | ✅ | 核心文件，包含 frontmatter（声明式配置）和正文（指令内容） |
| 支持文件 | ❌ | 与 `SKILL.md` 同目录下的辅助文件（模板、脚本、数据文件等），可被正文引用 |

当 Skill 以**目录形式**存在时（`SKILL.md` + 同目录下的其他文件），称为 **bundled skill**。这种形式让 Skill 可以自包含地携带复杂资源——例如一个代码审查 Skill 可以 bundle 自己的检查清单、一个 API 测试 Skill 可以 bundle 示例请求模板。

### 13.1.2 调用机制：SkillTool meta-tool

Skills 不是由用户直接粘贴到对话中调用的。Claude Code 内部维护了一个 **SkillTool**（meta-tool），它负责：

1. **发现**：扫描所有生效的存储位置，加载可用的 Skills
2. **注册**：将每个 Skill 的 `name` 和 `description` 注册到当前对话的上下文中
3. **路由**：当 Claude 决定调用某个 Skill 时，SkillTool 负责将 SKILL.md 的正文（及支持文件）注入到当前消息栈中

这意味着 Skill 的调用对 Claude 来说是**透明的**——Claude 看到的不是 "我要调用一个外部工具"，而是 "我的上下文中突然多了一段关于如何执行某任务的指令"。这种设计与 Agent 形成了本质区别。

### 13.1.3 与 Agent 的本质区别：上下文复用 vs 上下文隔离

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Claude Code 的上下文组装机制时指出，Skills 和 Agents 的根本差异在于**上下文注入点**的不同：

- **Skill**：通过 `assemble()` 阶段的 **in-place injection**（原地注入），将 Skill 内容直接插入到**当前对话上下文**中。调用完成后，Skill 的执行结果留在当前消息栈中，成为后续推理的一部分。
- **Agent**：创建**全新的消息栈**（new conversation context），在隔离的环境中执行任务。Agent 的执行过程对当前对话不可见，只返回最终结果。

**为什么会有这个设计差异？**

报告作者指出，这反映了两种不同的问题分解策略：

- **Skill 对应"能力扩展"**——让当前 Claude 实例临时获得某项专门能力（如读取特定日志格式、执行特定的代码审查流程）。因为 Skill 需要与当前任务紧密协作（可能需要访问当前对话中已经讨论过的变量、文件路径、决策），所以必须共享上下文。

- **Agent 对应"任务委派"**——将一块独立的、边界清晰的工作交给另一个执行单元（如"分析这个目录下的所有测试文件并生成报告"）。Agent 需要独立思考和尝试（可能包含多轮工具调用），其内部的试错过程不应污染当前对话的上下文。

**这个结论能帮你做什么？**

当你面临"用 Skill 还是 Agent"的选择时，可以依据以下判断标准：

| 问题 | 选择 Skill | 选择 Agent |
|------|-----------|-----------|
| 执行结果需要参与当前对话的后续讨论？ | ✅ | ❌ |
| 需要访问当前对话中已建立的上下文（已提及的文件、决策）？ | ✅ | ❌ |
| 执行过程可能包含多轮试错，且试错过程不应污染主对话？ | ❌ | ✅ |
| 需要并行执行多个独立任务？ | ❌ | ✅ |
| 需要精细控制可用工具集和模型？ | ✅（通过 frontmatter） | ✅（通过 agent 定义） |

### 13.1.4 与 Command 的初步对比

Command 是 Claude Code 中另一种指令触发机制，通常定义在 `.claude/commands.json` 或 `.claude/commands/` 目录中。Skill 在设计上被视为 Command 的**演进替代**。

《Token Economy, Hit Rate, Tools, Skills and Context》报告在对比四种上下文注入机制（Tools / Skills / Agents / Resources）时，从**控制能力**、**封装能力**和**成本效率**三个维度进行了系统评估。报告指出，Skills 是唯一在这三个维度上同时达到"高控制能力 + 强封装能力 + 低成本"的组合：

1. **支持文件 bundle**（SKILL.md + 支持文件）—— 解决了 Command 只能携带纯文本指令、无法附带模板/数据文件的问题
2. **支持 frontmatter 级别的精细控制**（工具权限、模型覆盖、执行上下文）—— 解决了 Command 缺乏声明式配置、只能依赖 prompt engineering 的局限
3. **上下文成本为 Low**（仅 description 常驻，正文按需注入）—— 解决了 Command 需要常驻完整指令文本导致的上下文膨胀问题

**为什么会有这个结论？**

报告作者在分析 Claude Code 的演进历史时指出，Command 机制是早期设计，主要用于快速触发固定指令；而随着用户对"更精细控制"和"更低上下文成本"的需求增长，Command 的设计逐渐暴露其局限——它既没有 frontmatter 的声明式接口（无法控制工具权限、模型选择、执行隔离），也没有分层加载机制（每次对话都要加载完整指令文本）。Skills 的设计正是为了回应这些局限，同时保留了 Command 的"快速触发"能力（通过 `/skill-name`）。

**这个结论能帮你做什么？**

这个对比提供了一个清晰的**迁移判断标准**：如果你的 Command 只是偶尔使用的简单指令，保持现状即可；但如果它满足以下任一条件，就应该迁移为 Skill：

- 被**频繁使用**（上下文成本累积显著）
- 需要**限制工具权限**（如只读操作、禁止 shell 执行）
- 需要**模型降级**以节省成本（如简单任务用 Haiku 而非默认模型）
- 需要**执行隔离**（fork）以防止副作用污染主对话
- 需要**携带支持文件**（模板、检查清单、示例数据）

| 维度 | Skill | Command |
|------|-------|---------|
| 定义文件 | `SKILL.md`（YAML frontmatter + Markdown） | `commands.json` 或纯文本文件 |
| 文件 bundle | ✅ 支持 | ❌ 不支持 |
| 工具权限控制 | ✅ `tools` frontmatter | ❌ 无 |
| 模型覆盖 | ✅ `model` frontmatter | ❌ 无 |
| 执行隔离（fork）| ✅ `execution_context` frontmatter | ❌ 无 |
| 常驻上下文成本 | Low（仅 description）| High（完整文本）|
| 用户触发方式 | `/skill-name` 或对话中自然提及 | `/command-name` 或斜杠命令 |
| Claude 自主触发 | ✅ 可配置 | ❌ 不支持 |

---

## 13.2 存储位置与作用域优先级

### 13.2.1 四层存储位置

Skills 可以存放在四个层级，对应不同的生效范围：

| 层级 | 存储路径 | 生效范围 | 典型用途 |
|------|---------|---------|---------|
| **企业级** | 企业部署配置 | 该企业下所有用户的所有项目 | 组织统一的编码规范、合规检查 |
| **用户级** | `~/.claude/skills/` | 当前用户的所有项目 | 个人偏好的工具封装（如个人博客发布流程） |
| **项目级** | `<project>/.claude/skills/` | 当前项目 | 项目特定的构建脚本、领域特定的代码生成 |
| **插件级** | 插件内部目录 | 安装该插件的项目 | 插件附带的能力（如 MCP 服务器相关的 Skill） |

### 13.2.2 优先级解析规则

当不同层级存在**同名 Skill** 时，Claude Code 按以下优先级解析（高优先级覆盖低优先级）：

```
企业级 > 用户级 > 项目级 > 插件级
```

这意味着：

- 如果企业的管理员定义了一个名为 `code-review` 的 Skill，那么该企业的所有用户都将使用企业版本，即使他们在自己的 `~/.claude/skills/` 中也定义了同名 Skill。
- 如果项目根目录下有 `.claude/skills/code-review/SKILL.md`，而用户家目录下也有 `~/.claude/skills/code-review/SKILL.md`，则当前项目使用项目级版本，其他项目使用用户级版本。

**这个优先级设计的意图是什么？**

它提供了一种**渐进式覆盖**的能力：组织可以在全局层面建立基线规范，而项目团队可以在特定项目中进行定制，个人用户又可以在此基础上叠加自己的偏好。每一层只覆盖它关心的差异，不需要复制完整的 Skill 定义。

### 13.2.3 快速判断：我的 Skill 到底生效了吗？

当你放置了一个 Skill 但不确定它是否被加载时，可以通过以下方式验证：

1. **检查命名**：确保目录或文件名为 Skill 的 `name` 字段值（或 `display_name`，取决于具体实现版本）。
2. **检查 frontmatter 格式**：YAML frontmatter 必须用 `---` 包围，且 `name` 和 `description` 字段为必需。
3. **检查层级位置**：项目级 Skill 必须位于项目根目录的 `.claude/skills/` 下（不是任意子目录）。
4. **观察触发**：如果 `claude_invokable: true`，在相关对话中观察 Claude 是否自动提议使用该 Skill；如果 `user_invokable: true`，尝试用 `/skill-name` 直接调用。

---

## 13.3 Frontmatter 字段详解

Skill 的强大能力主要来自其 YAML frontmatter 的声明式配置。以下按**控制维度**分组讲解，每个字段回答三个问题：**是什么？什么时候需要？如果不填会怎样？**

### 13.3.1 身份与元数据字段

这些字段决定了 Skill 的基本身份和 Claude 对它的认知。

#### `name`

- **是什么**：Skill 的内部标识符，用于 SkillTool 路由和目录命名。只能包含字母、数字、连字符和下划线。
- **什么时候需要**：**必填**。所有 Skill 必须有唯一的 `name`（在同一优先级层级内）。
- **如果不填**：Skill 无法被加载，Claude Code 启动时会报错或忽略该文件。
- **命名建议**：使用动词-名词结构，如 `generate-api-docs`、`run-security-scan`。避免过于泛泛的名称如 `helper`。

#### `display_name`

- **是什么**：用户可见的 Skill 名称，显示在 Skill 列表和自动补全中。
- **什么时候需要**：**可选**。如果省略，Claude Code 通常会使用 `name` 的格式化版本（如将连字符替换为空格并首字母大写）。
- **如果不填**：使用 `name` 的默认格式化形式。
- **命名建议**：使用简洁的自然语言，如 "生成 API 文档"、"运行安全扫描"。如果 Skill 面向非技术用户，display_name 可以比 name 更友好。

#### `description`

- **是什么**：一段告诉 Claude "**什么时候应该调用这个 Skill**" 的说明文字。这是整个 Skill 机制中最关键的字段之一。
- **什么时候需要**：**必填**。
- **如果不填**：Skill 无法被加载。
- **为什么它如此关键？**

  《Token Economy, Hit Rate, Tools, Skills and Context》报告分析了 Claude Code 的上下文组装机制后指出，**只有 `description` 字段会常驻系统提示（system prompt）**。这意味着：Claude 在每一轮推理中都能看到所有可用 Skills 的 `description`，并据此判断 "用户当前的需求是否匹配某个 Skill 的能力？" 如果匹配，Claude 就会通过 SkillTool 触发该 Skill 的完整内容注入。

  因此，`description` 不是给人类用户看的"功能介绍"，而是给 Claude 看的**触发条件说明**。它应该明确描述 Skill 的适用场景，而不是笼统地说 "这是一个很好用的工具"。

- **好的 description 示例**：

  ```yaml
  description: 当用户要求审查代码、检查潜在 bug、或评估代码质量时，使用此 Skill 执行结构化的代码审查流程。
  ```

- **不好的 description 示例**：

  ```yaml
  description: 代码审查 Skill，可以帮助你审查代码。
  ```

  第二个示例的问题在于：它没有告诉 Claude "什么时候"调用，只是重复了 Skill 的名称。Claude 看到这段描述后，很难在合适的时机触发该 Skill。

### 13.3.2 调用控制字段（谁可以触发）

这些字段控制 Skill 的可见性和触发方式。

#### `user_invokable`

- **是什么**：布尔值。若为 `true`，用户可以通过 `/skill-name` 的斜杠命令语法直接触发该 Skill。
- **什么时候需要**：如果你希望用户能够主动、显式地调用此 Skill，设为 `true`。
- **如果不填**：默认值取决于 Claude Code 版本，通常为 `true`。
- **使用场景**：
  - 设为 `true`：用户明确知道 "我要执行代码审查"，并主动输入 `/code-review`。
  - 设为 `false`：Skill 是一个"后台能力"，用户不直接感知它的存在，但 Claude 会在需要时自动调用（配合 `claude_invokable: true`）。

#### `claude_invokable`

- **是什么**：布尔值。若为 `true`，Claude 可以在对话过程中自主决定调用该 Skill（无需用户显式指令）。
- **什么时候需要**：如果你希望 Claude 能"智能地"在合适时机使用该能力，设为 `true`。
- **如果不填**：默认值取决于 Claude Code 版本，通常为 `true`。
- **使用场景**：
  - 设为 `true`：当用户说 "帮我看看这段代码有什么问题"，Claude 识别到这与 `code-review` Skill 的 `description` 匹配，自动触发该 Skill。
  - 设为 `false`：Skill 只能在用户明确要求时使用（如一些敏感操作：部署到生产环境、删除数据）。

**两个字段的组合效果：**

| `user_invokable` | `claude_invokable` | 效果 |
|-----------------|-------------------|------|
| `true` | `true` | 用户可主动调用，Claude 也可自动触发（最常见的配置） |
| `true` | `false` | 只有用户能触发，Claude 不会自动使用（适合敏感操作） |
| `false` | `true` | "隐形 Skill"——用户无法直接调用，但 Claude 会在需要时自动使用（适合底层能力封装） |
| `false` | `false` | Skill 完全不可调用（通常用于调试或临时禁用） |

### 13.3.3 执行环境字段（工具权限、模型覆盖、fork）

这些字段决定了 Skill 被调用时的**执行沙箱**。

#### `tools`

- **是什么**：一个字符串数组，列出该 Skill 被允许使用的工具。例如 `["Read", "Edit", "Bash"]`。
- **什么时候需要**：当你希望限制 Skill 的能力边界、防止意外副作用时，显式设置此字段。
- **如果不填**：Skill 继承当前对话的全局工具权限（通常是全部可用工具）。
- **为什么这个字段很重要？**

  Skills 的 `tools` 字段提供了一种**最小权限原则**的实现方式。假设你有一个 Skill 的功能只是"读取配置文件并给出建议"，如果它不限制 `tools`，那么在极端情况下，Claude 可能会在执行该 Skill 时意外调用 `Edit` 修改文件——因为 Skill 的指令中说 "分析这个配置"，而 Claude 的推理链可能延伸出 "既然配置有问题，那我直接改了吧"。

  通过将 `tools` 设为 `["Read"]`，你可以确保这个 Skill 是一个**只读分析器**，无论 Claude 的推理链如何延伸，它都无法在该 Skill 的执行范围内修改任何文件。

- **示例配置**：

  ```yaml
  # 一个只读分析型 Skill
  tools: ["Read", "Glob", "Grep"]

  # 一个可以修改代码但不能执行 shell 的 Skill
  tools: ["Read", "Edit", "Write"]

  # 一个需要完整系统访问的 DevOps Skill
  tools: ["Read", "Edit", "Write", "Bash", "Agent"]
  ```

#### `model`

- **是什么**：指定执行该 Skill 时使用的 Claude 模型。可选值取决于 Claude Code 支持的模型版本（如 `opus`、`sonnet`、`haiku`）。
- **什么时候需要**：当你希望为特定 Skill 优化成本-性能平衡时。
- **如果不填**：继承当前对话使用的模型。
- **典型使用场景**：

  - **使用 `haiku`**：Skill 执行的是模板化、低复杂度的任务（如格式化输出、生成标准化报告、简单的文件搜索）。Haiku 速度更快、成本更低，足以胜任。
  - **使用 `sonnet` 或 `opus`**：Skill 涉及复杂推理（如代码审查、架构分析、多步骤调试）。这些任务需要更强的理解和推理能力，使用高级模型可以提高输出质量。

  例如，一个"生成提交信息"的 Skill 可以设为 `model: haiku`，因为它的输入（git diff）和输出（结构化提交信息）之间的映射相对直接；而一个"重构代码以消除技术债务"的 Skill 应该使用默认模型或 `sonnet`，因为它需要理解代码的语义并做出设计决策。

#### `execution_context`

- **是什么**：控制 Skill 的执行隔离级别。若设为 `'fork'`，Skill 将在**隔离的上下文**中执行，不影响当前对话的消息栈。
- **什么时候需要**：当 Skill 的执行过程可能产生大量中间消息、或可能访问敏感信息、或你不希望 Skill 的副作用污染当前对话时。
- **如果不填**：默认为**原地执行**（in-place），Skill 的内容直接注入当前上下文。
- **'fork' 的语义详解：**

  当 `execution_context: 'fork'` 时，Claude Code 会创建一个新的临时上下文来执行该 Skill。这个临时上下文：

  - **继承了当前对话的文件系统状态**（可以看到相同的文件）
  - **拥有独立的工具调用历史**（Skill 内的 Read/Edit/Bash 不会出现在主对话中）
  - **返回时只传递最终结果**（用户和主对话只看到 Skill 的 "输出摘要"，看不到中间推理过程）

  这与 Agent 的隔离不同：Agent 是完全独立的智能体，有自己的目标和行为模式；而 `fork` 只是将 Skill 的执行过程**包裹在一个临时上下文中**，执行完成后上下文被丢弃，结果合并回主对话。

- **什么时候使用 'fork'：**

  - Skill 会执行大量工具调用（如扫描整个代码库），你不希望这些调用记录填满当前对话。
  - Skill 需要尝试多种方案并对比（如 "试试三种不同的实现方式"），只把最佳方案带回来。
  - Skill 涉及敏感操作（如读取密钥文件），你希望限制其副作用范围。

### 13.3.4 高级扩展字段

#### `arguments`

- **是什么**：定义 Skill 被调用时可以接收的参数，类似于函数签名。SkillTool 在调用 Skill 时可以根据当前对话内容自动提取这些参数。
- **什么时候需要**：当 Skill 需要接收动态输入（如文件路径、目标分支、输出格式）时。
- **如果不填**：Skill 被调用时不接收显式参数，所有信息需要从当前对话上下文中推断。
- **示例**：

  ```yaml
  arguments:
    - name: file_path
      description: 需要审查的文件路径
      required: true
    - name: strictness
      description: 审查严格程度（low / medium / high）
      required: false
      default: medium
  ```

  定义 `arguments` 后，Claude 在调用该 Skill 时会尝试从用户输入中提取 `file_path` 和 `strictness`，并将它们注入到 Skill 的上下文中供指令正文引用。

#### `hooks`

- **是什么**：Skill 可以在被调用时**动态注册行为钩子**。Hooks 是一种在 Skill 生命周期的特定阶段执行自定义逻辑的机制。
- **什么时候需要**：当你需要在 Skill 调用前后执行附加操作（如记录日志、修改环境变量、触发通知）时。
- **如果不填**：Skill 按默认行为执行，无额外钩子。
- **注意**：Hooks 的具体语法和支持的钩子点取决于 Claude Code 的版本，建议参考官方文档获取最新信息。常见钩子点包括 `pre_invoke`（调用前）和 `post_invoke`（调用后）。

#### `associated_agents`

- **是什么**：将特定的 Agent 定义与该 Skill 关联。当 Skill 被调用时，可以委派部分工作给关联的 Agent。
- **什么时候需要**：当 Skill 的工作流中某些步骤需要独立的智能体执行（如 "先用这个 Agent 搜集信息，再用 Skill 的主逻辑处理"）时。
- **如果不填**：Skill 不使用关联 Agent，所有工作在当前上下文中完成。

---

## 13.4 调用控制与执行上下文

### 13.4.1 用户可调 vs Claude 可调

前文 13.3.2 中介绍了 `user_invokable` 和 `claude_invokable` 的语义，这里进一步讨论其工程含义。

**用户可调（`user_invokable: true`）的 Skill** 构成了 Claude Code 的**显式命令界面**。它们类似于传统 CLI 中的子命令——用户明确知道自己要做什么，主动触发。这种显式调用适合：

- 操作具有明确边界和预期结果（"运行测试"、"生成文档"）
- 操作可能消耗显著资源（"扫描整个仓库的依赖漏洞"），不应由 Claude 轻率触发
- 操作需要用户确认参数（"部署到哪个环境？"）

**Claude 可调（`claude_invokable: true`）的 Skill** 构成了 Claude Code 的**隐式能力扩展**。Claude 在每一轮推理中都会审视可用的 Skills 及其 `description`，当判断某个 Skill 与当前用户意图匹配时，会自动触发。这种隐式调用适合：

- 能力是通用的、频繁需要的（如代码格式化、拼写检查）
- 触发条件可以从对话内容中明确推断（用户说 "帮我看看这段代码" → 触发代码审查 Skill）
- 操作的副作用小、可预测（如"添加类型注解"比"删除数据库"更适合自动触发）

### 13.4.2 内存注册（Bundled Skills）与文件系统注册

Skills 有两种注册来源：

**文件系统 Skills**：从 `~/.claude/skills/` 或项目 `.claude/skills/` 目录加载的 SKILL.md 文件。这是用户自定义 Skill 的主要方式。

**内存注册 Skills（Bundled Skills）**：内嵌在 Claude Code 二进制中的预置 Skills。这些 Skills 随软件分发，不是从文件系统读取的。Claude Code 内置的 `/code-review`、`/simplify` 等斜杠命令通常就是以 bundled skill 的形式实现的。

**关键特性：同名覆盖**

如果用户在项目级或用户级定义了与 bundled skill 同名的 Skill，文件系统版本会**覆盖**内存注册版本。这允许用户：

- 自定义内置行为（如修改 `/code-review` 的审查标准）
- 在企业级统一覆盖（如强制所有用户使用企业定义的 `security-check` 而非默认版本）

**这对调试的意义：**

如果你发现某个 Skill 的行为与预期不符，首先检查是否有高优先级的同名 Skill 在覆盖它。使用 Claude Code 的 Skill 列表功能（具体命令取决于版本，如 `/skills` 或查看系统提示）可以查看当前生效的是哪个版本的 Skill。

### 13.4.3 execution context: 'fork' 的隔离语义

前文 13.3.3 已介绍 `execution_context: 'fork'` 的基本概念，这里深入其技术细节和使用模式。

**fork 与 Agent 隔离的对比：**

| 维度 | `execution_context: 'fork'` | Agent |
|------|---------------------------|-------|
| 上下文来源 | 继承主对话的文件系统状态 | 独立的初始上下文 |
| 工具调用可见性 | 主对话看不到 fork 中的中间调用 | 完全独立 |
| 返回方式 | 结果合并回主对话 | 返回最终结果或继续独立运行 |
| 配置方式 | frontmatter 字段 | 独立的 Agent 定义文件 |
| 使用场景 | 包裹一个 Skill 的执行过程 | 委派一个完整任务 |

**fork 的适用模式：**

1. **沙箱验证模式**：在 fork 中执行可能有破坏性的操作（如大规模重构），验证通过后再在主对话中应用。
2. **信息搜集模式**：在 fork 中扫描大量文件、执行复杂查询，只把汇总结果带回主对话。
3. **对比实验模式**：在 fork 中尝试多种解决方案，选择最佳方案返回，避免主对话被多个方案的讨论淹没。

---

## 13.5 上下文成本与动态注入

### 13.5.1 为什么 Skill 的常驻成本极低

《Token Economy, Hit Rate, Tools, Skills and Context》报告在分析 Claude Code 的上下文组装机制时，将 Skills 的上下文成本标记为 **Low**。这个结论并非因为 Skill 的内容很短，而是因为 Claude Code 采用了**分层加载策略**：

1. **常驻层（System Prompt）**：只包含每个 Skill frontmatter 中的 `name` 和 `description` 字段。这些文本通常只有几十到几百个 token。
2. **按需层（On-Demand Injection）**：Skill 的完整指令体（SKILL.md 的 Markdown 正文以及任何 bundle 的支持文件）**仅在 Skill 被显式调用时**才注入到上下文中。

**为什么会有这个设计？**

报告作者指出，这是 Claude Code 为解决"能力丰富度 vs 上下文效率"矛盾所做的关键架构决策：

- 一方面，用户希望 Claude 拥有尽可能多的能力（ dozens of Skills），以便在不同场景下都能提供专业帮助。
- 另一方面，LLM 的上下文窗口是有限的，且过长的系统提示会降低推理效率（增加 token 消耗、可能稀释重要指令的注意力）。

如果每次对话开始时就把所有 Skill 的完整内容都塞进系统提示，那么定义 20 个 Skill 可能就意味着每次对话都要额外承担数千甚至上万个 token 的固定开销——这显然不可扩展。

分层加载策略解决了这个问题：Claude 在每一轮推理中都能看到所有 Skills 的 `description`（从而知道"有哪些能力可用"），但只有在真正决定使用某个 Skill 时，才加载其完整内容。这类似于操作系统的**按需分页**（demand paging）——进程的地址空间可以很大，但只有实际访问的页面才会被加载到物理内存中。

**这个结论能帮你做什么？**

这意味着你可以在项目中**大胆定义多个 Skill** 而不用担心拖慢每次对话。例如，你可以为以下场景各定义一个 Skill：

- 代码审查（`code-review`）
- 生成 API 文档（`generate-api-docs`）
- 运行测试并解析结果（`run-tests`）
- 检查依赖安全漏洞（`security-audit`）
- 格式化代码（`format-code`）
- 生成发布说明（`generate-release-notes`）

即使这 6 个 Skill 的正文加起来有 5000 个 token，它们对每次对话初始开销的贡献也几乎为零（只有 6 段 description，可能总共不到 500 个 token）。只有当 Claude 实际调用某个 Skill 时，你才承担该 Skill 的 token 成本——而这正是你需要它工作的时候。

### 13.5.2 按需注入的触发条件

Skill 的按需注入发生在以下时刻：

1. **Claude 主动触发**：Claude 在推理过程中判断某个 `claude_invokable: true` 的 Skill 与当前需求匹配，通过 SkillTool 发起调用。
2. **用户显式触发**：用户输入 `/skill-name` 调用 `user_invokable: true` 的 Skill。

注入后，Skill 的完整内容（SKILL.md 正文 + 支持文件）被附加到当前消息栈中，Claude 基于这些内容执行后续操作。执行完成后，Skill 的指令内容会留在对话历史中（成为后续推理的上下文），但 Skill 本身不会再次自动注入——除非再次被触发。

### 13.5.3 动态命令语法：`!`command``

Skills 支持一种特殊的动态内容注入语法：在 SKILL.md 正文中使用 ``!`command` ``（反引号包裹的 shell 命令前缀以 `!`），可以在 Skill 被调用时**执行该命令并将输出插入到 Skill 内容中**。

**示例：**

```markdown
---
name: git-summary
description: 当用户想要了解当前仓库状态时，生成提交摘要。
---

# 当前仓库状态

以下是最近的提交历史：

!`git log --oneline -10`

# 指令

基于上述提交历史，为用户总结最近的开发进展...
```

当这个 Skill 被调用时，``!`git log --oneline -10` `` 会被替换为实际的 `git log` 输出，然后完整的文本（包含动态获取的日志）才被注入到 Claude 的上下文中。

**为什么这个机制有价值？**

它实现了 Skill 内容的**运行时动态化**。你可以编写在注入时自动收集环境信息的 Skill，而不需要让 Claude 在注入后再去执行工具调用获取这些信息。这减少了往返次数（round-trips），也避免了 Claude 忘记执行某个前置查询的问题。

**常见使用场景：**

- 在代码审查 Skill 中嵌入 ``!`git diff HEAD~1` `` 获取最新变更
- 在项目状态报告 Skill 中嵌入 ``!`find . -name "*.py" | wc -l` `` 获取代码统计
- 在依赖检查 Skill 中嵌入 ``!`npm outdated` `` 获取过期依赖列表

**注意事项：**

- ``!`command` `` 在 Skill **注入时**执行，不是在 Skill **定义时**执行。这意味着每次调用 Skill 都会重新执行命令。
- 命令执行失败（非零退出码）时，其行为取决于 Claude Code 版本——可能插入错误信息，也可能阻止 Skill 注入。建议编写健壮的命令（如 `git log --oneline -10 2>/dev/null || echo "Not a git repository"`）。
- 命令的输出会被直接插入为纯文本，注意转义问题（如果输出包含 Markdown 特殊字符，可能会破坏 SKILL.md 的格式）。

---

## 13.6 案例一：将常用 Command 迁移为 Skill

本案例展示如何将一个现有的 Command 迁移为 Skill，并解释每个 frontmatter 字段的选型理由。

### 场景

你有一个常用的工作流程：在提交代码前，让 Claude 读取 `git diff` 并生成符合 [Conventional Commits](https://www.conventionalcommits.org/) 规范的提交信息。

### 原 Command 定义

假设你之前在 `.claude/commands.json` 中定义了：

```json
{
  "name": "generate-commit",
  "prompt": "Read the current git diff and generate a Conventional Commits style commit message. Analyze the changes to determine the correct type (feat, fix, docs, style, refactor, test, chore). Provide a concise summary and optional detailed description."
}
```

**这个 Command 的局限：**

1. 没有工具权限控制——Claude 在执行时拥有全部工具权限，理论上可能误改文件
2. 没有模型覆盖——简单的提交信息生成任务也用默认模型（可能是 Opus），不够经济
3. 没有执行隔离——如果 Claude 在生成提交信息的过程中尝试其他操作，副作用直接进入当前对话
4. 上下文成本高——每次对话都要完整加载这段 prompt

### 迁移后的 Skill

创建文件 `.claude/skills/generate-commit/SKILL.md`：

```markdown
---
name: generate-commit
display_name: 生成提交信息
description: 当用户要求撰写 git commit message、生成提交说明、或准备提交代码时，读取当前暂存区的 diff 并生成符合 Conventional Commits 规范的提交信息。分析变更内容以确定正确的类型（feat, fix, docs, style, refactor, test, chore），提供简洁的摘要和可选的详细描述。
user_invokable: true
claude_invokable: true
tools: ["Read", "Bash"]
model: haiku
---

# 生成提交信息

## 任务

读取当前 Git 暂存区的变更（`git diff --staged`），生成符合 Conventional Commits 规范的提交信息。

## 步骤

1. 执行 `git diff --staged` 获取变更内容
2. 分析变更涉及的文件和代码改动，判断提交类型：
   - `feat`：新功能
   - `fix`：Bug 修复
   - `docs`：文档变更
   - `style`：代码格式调整（不影响逻辑）
   - `refactor`：代码重构
   - `test`：测试相关
   - `chore`：构建/工具/依赖变更
3. 生成提交信息，格式如下：
   ```
   <type>(<optional scope>): <description>

   [optional body]

   [optional footer(s)]
   ```
4. 如果暂存区为空，提示用户先 `git add` 文件

## 约束

- 摘要行不超过 72 个字符
- 使用祈使句（"Add" 而非 "Added"）
- 不要猜测未明确体现的信息
```

### 字段选型理由

| 字段 | 值 | 理由 |
|------|-----|------|
| `name` | `generate-commit` | 简洁、语义清晰，对应原 Command 名称 |
| `display_name` | `生成提交信息` | 中文界面下用户友好的显示名称 |
| `description` | （较长） | 明确告诉 Claude "什么时候调用"——用户提到 commit、提交、提交信息等关键词时触发 |
| `user_invokable` | `true` | 用户明确知道要生成提交信息，可以主动触发 `/generate-commit` |
| `claude_invokable` | `true` | 用户说 "我准备提交了" 或 "写个提交信息" 时，Claude 可以自动识别并触发 |
| `tools` | `["Read", "Bash"]` | **最小权限**：只需要读取文件和执行 `git diff`。禁止 `Edit`/`Write` 避免在生成提交信息时意外修改代码 |
| `model` | `haiku` | 提交信息生成是模板化任务（分析 diff → 分类 → 格式化输出），Haiku 的能力足够，且速度更快、成本更低 |

### 使用方式

迁移完成后，你可以：

1. **主动调用**：输入 `/generate-commit` 或 `/生成提交信息`
2. **自然语言触发**：说 "帮我写个提交信息"、"准备提交"，Claude 会自动识别并调用

### 进一步优化：使用动态命令

如果你的 Skill 希望**在注入时就获取 diff**（减少 Claude 调用工具的往返），可以修改为：

```markdown
---
name: generate-commit
# ... 同上 ...
---

# 当前暂存区变更

!`git diff --staged 2>/dev/null || echo "暂存区为空"`

# 生成提交信息

基于上述 diff，生成 Conventional Commits 风格的提交信息...
```

这样 Skill 被调用时，diff 内容已经嵌入在注入文本中，Claude 可以直接基于它生成提交信息，无需再执行一次 `Bash` 调用。

---

## 13.7 案例二：编写一个带隔离执行的代码审查 Skill

本案例展示一个**生产级复杂 Skill**：多文件 bundle、fork 隔离执行、模型覆盖和自定义 hooks。

### 场景

你需要为团队定义一个标准化的代码审查流程，要求：

1. 遵循团队制定的审查检查清单（checklist）
2. 审查过程不应污染当前对话（可能涉及大量文件读取）
3. 审查需要较强的代码理解能力，应使用高级模型
4. 审查完成后自动生成结构化报告

### 目录结构

```
.claude/skills/
└── code-review/
    ├── SKILL.md          # 主文件：定义 frontmatter 和审查流程
    └── checklist.md      # 支持文件：团队审查检查清单
```

### checklist.md

```markdown
# 代码审查检查清单

## 正确性
- [ ] 代码是否实现了预期功能？
- [ ] 边界条件是否被处理？
- [ ] 错误处理是否完善？
- [ ] 是否存在并发安全问题？

## 可读性
- [ ] 命名是否清晰且具有描述性？
- [ ] 函数长度是否适当（建议不超过 50 行）？
- [ ] 复杂的逻辑是否有注释说明？
- [ ] 代码格式是否符合项目规范？

## 可维护性
- [ ] 是否存在代码重复？
- [ ] 模块间的依赖是否合理？
- [ ] 是否引入了不必要的复杂度？
- [ ] 测试覆盖率是否充足？

## 性能
- [ ] 是否存在明显的性能瓶颈？
- [ ] 数据库查询是否优化？
- [ ] 是否有不必要的内存分配？

## 安全性
- [ ] 用户输入是否被正确验证和转义？
- [ ] 敏感信息是否被硬编码？
- [ ] 权限检查是否到位？
```

### SKILL.md

```markdown
---
name: code-review
display_name: 代码审查
description: 当用户要求审查代码、检查潜在 bug、评估代码质量、进行 Code Review、或询问"这段代码有什么问题"时，使用此 Skill 执行结构化的代码审查流程。此 Skill 会按照团队检查清单对代码进行系统性分析。
user_invokable: true
claude_invokable: true
tools: ["Read", "Glob", "Grep", "Bash"]
model: sonnet
execution_context: fork
arguments:
  - name: target
    description: 需要审查的文件路径、目录或 git diff 范围
    required: false
  - name: focus
    description: 审查重点（correctness / readability / maintainability / performance / security / all）
    required: false
    default: all
---

# 代码审查流程

## 准备阶段

首先，读取同目录下的 `checklist.md` 获取完整的审查检查清单。

确定审查范围：
- 如果用户指定了 `target` 参数，审查该目标
- 如果未指定且当前是 Git 仓库，审查最近的变更（`git diff HEAD~1`）
- 如果未指定且不是 Git 仓库，提示用户提供目标

## 审查执行

按照以下维度进行系统性审查：

1. **正确性**：功能实现、边界条件、错误处理、并发安全
2. **可读性**：命名、长度、注释、格式
3. **可维护性**：重复代码、依赖关系、复杂度、测试覆盖
4. **性能**：瓶颈、查询优化、内存使用
5. **安全性**：输入验证、敏感信息、权限检查

根据 `focus` 参数调整审查深度：
- `focus=all`：所有维度均衡审查
- `focus=correctness`：重点检查逻辑正确性，其他维度简要提及
- 其他值类似处理

## 输出格式

审查完成后，输出结构化报告：

```
## 代码审查报告

### 审查对象
[文件/变更范围]

### 关键发现
1. **[严重/重要/建议]** [问题描述]
   - 位置：[文件:行号]
   - 建议：[具体改进方案]

2. ...

### 正面评价
[代码中做得好的地方]

### 行动项
- [ ] [需要修复的问题]
- [ ] [建议的改进]
```

## 约束

- 每条发现必须有具体的文件位置
- 区分 "必须修复"、"建议改进" 和 "风格偏好"
- 不要猜测代码意图——只基于实际代码做出判断
- 保持建设性语气，批评具体而非泛泛
```

### 字段选型理由

| 字段 | 值 | 理由 |
|------|-----|------|
| `name` | `code-review` | 标准命名，与常见需求对应 |
| `description` | （较长） | 覆盖多种触发说法："审查代码"、"Code Review"、"这段代码有什么问题"等 |
| `tools` | `["Read", "Glob", "Grep", "Bash"]` | 需要读取文件、搜索代码、执行 git 命令。不需要 `Edit`/`Write`——审查 Skill 应该是只读的 |
| `model` | `sonnet` | 代码审查需要理解代码语义、识别潜在 bug、评估设计决策，需要较强的推理能力。Haiku 可能遗漏 subtle 的问题，Opus 成本过高，Sonnet 是平衡选择 |
| `execution_context` | `fork` | **关键设计**：代码审查可能涉及读取大量文件（如整个模块），fork 隔离确保这些文件读取不会填满当前对话的历史记录。用户只想看到最终的审查报告，不关心中间读了多少个文件 |
| `arguments` | `target`, `focus` | `target` 允许用户指定审查范围；`focus` 允许用户关注特定维度，避免每次都被全量检查清单淹没 |

### 为什么使用 fork？

假设你在审查一个包含 20 个文件的功能模块。如果不使用 fork，Claude 在审查过程中会：

1. 读取 `checklist.md`
2. 读取 `file1.py`（工具调用记录进入对话历史）
3. 读取 `file2.py`（工具调用记录进入对话历史）
4. ...
5. 读取 `file20.py`（工具调用记录进入对话历史）
6. 生成审查报告

结果是，你的对话历史中多了 20+ 条工具调用记录。如果你之后想继续讨论其他话题，这些记录会占用宝贵的上下文窗口，甚至可能稀释后续推理的注意力。

使用 `execution_context: 'fork'` 后：

1. 主对话中只出现 "Claude 调用了 code-review Skill"
2. 所有 20 个文件的读取发生在隔离的 fork 上下文中
3. fork 返回时，只有最终的审查报告合并回主对话

**主对话的上下文保持干净**，你可以无缝继续其他讨论。

### 使用方式

1. **审查最近一次提交**：
   ```
   /code-review
   ```
   或自然语言："帮我审查一下最近的代码"

2. **审查特定文件**：
   ```
   /code-review target=src/auth.py
   ```
   或自然语言："审查一下 src/auth.py"

3. **关注安全性**：
   ```
   /code-review focus=security
   ```
   或自然语言："检查一下这段代码有没有安全问题"

### 扩展思路

这个 Skill 可以进一步扩展：

- **添加 `hooks`**：在审查完成后自动将报告写入 `reviews/` 目录或发送到 Slack
- **添加更多支持文件**：`style-guide.md`（项目特定风格指南）、`common-issues.md`（历史常见错误模式）
- **集成 Agent**：`associated_agents` 关联一个专门负责安全审计的 Agent，在审查过程中自动调用它进行深度安全检查
