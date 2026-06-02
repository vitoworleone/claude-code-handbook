# 上下文注入与连续性保持机制调研报告

> 生成时间：2026-05-16
> 调研范围：Claude Code Python Runtime 的 system prompt 组装、transcript 管理、session 恢复机制
> 核心问题：上下文和 MD 文档如何注入？如何保持让模型知道上一轮做了什么？

---

## 核心结论（TL;DR）

模型"知道上一轮做了什么"依赖 **两条独立的信息管道**：

| 管道 | 内容 | 更新频率 | 持久性 |
|---|---|---|---|
| **System Prompt** | 身份定义、行为规则、CLAUDE.md、Memory 索引 | 每轮重新组装（但大部分可缓存） | 跨会话持久（Memory/CLAUDE.md） |
| **Transcript (messages)** | 完整的对话历史（用户输入、模型输出、工具结果） | 每轮追加 | 单会话内累积，可序列化恢复 |

**关键认知**：模型并非"记住"了什么——它每次 API 调用都收到完整的对话历史（transcript），就像一个人每次对话前都把之前的聊天记录重新读一遍。System prompt 是"你是谁、该怎么做事"，transcript 是"你们刚才聊了什么"。

---

## 一、System Prompt 的三层注入架构

System prompt 是整个对话的"元规则层"，由 `build_system_prompt()`（`cc/prompts/builder.py:80`）按固定顺序拼装。它返回 `list[str]`（而非单个字符串），因为 API 层需要将每个段落作为独立的 `cache_control` 段传入，以最大化 prompt caching 命中率。

### 1.1 段落拼装顺序（11 段）

```
静态段落（可缓存，内容固定不变）
  1. Intro          — 身份声明 + 网络安全指令 + URL 生成限制
  2. System         — 系统行为规则（权限模式、hook、prompt injection 防御）
  3. Doing tasks    — 任务执行原则（先读再改、不过度工程、安全优先）
  4. Actions        — 高风险操作确认规范（可逆性评估、破坏性操作清单）
  5. Using tools    — 工具使用偏好（专用工具优先于 Bash）
  6. Tone/Style     — 输出风格（简洁、无 emoji、带行号引用）
  7. Output efficiency — 先给结论再给推理

动态段落（每次请求可能不同）
  8. Environment    — 运行环境信息（cwd、OS、shell、日期）
  9. SUMMARIZE      — "工具结果可能被清除，把关键信息写进回复"

条件段落（仅在启用时注入）
 10. Memory         — 记忆系统行为指令 + MEMORY.md 索引内容
 11. CLAUDE.md      — 用户自定义指令（最高优先级）
```

**为什么 CLAUDE.md 放在最后？** 因为 prompt 中越靠后的指令优先级越高（模型更倾向于遵循最近接收到的指令）。这确保用户自定义规则能覆盖默认行为。

**为什么 Memory 放在 CLAUDE.md 之前？** 因为 CLAUDE.md 是用户主动编写的项目级规则，而 Memory 是模型自动提取的跨会话信息。用户规则应优先于模型推断。

### 1.2 Prompt Caching 的利用

静态段落（1-7）内容固定，API 层可利用 prompt caching 机制缓存这些段落的 token 表示，避免每次请求重复计算。动态段落（8-9）和条件段落（10-11）内容可能变化，放在后面以减少缓存失效范围。

### 1.3 实际拼接代码

```python
# cc/prompts/builder.py:111
sections: list[str | None] = [
    get_intro_section(),          # 静态
    get_system_section(),         # 静态
    get_doing_tasks_section(),    # 静态
    get_actions_section(),        # 静态
    get_using_tools_section(),    # 静态
    get_tone_style_section(),     # 静态
    get_output_efficiency_section(),  # 静态
    compute_env_info(cwd, model),     # 动态
    SUMMARIZE_TOOL_RESULTS,           # 动态
]
if memory_dir:
    sections.append(build_memory_prompt(memory_dir, memory_index_content))
if claude_md_content:
    sections.append(f"# CLAUDE.md\n...{claude_md_content}")
return [s for s in sections if s is not None]  # 过滤 None
```

最终由 `_build_system()`（`main.py:294`）用 `"\n\n".join(parts)` 拼成单个字符串，作为 `system` 参数传入 API。

---

## 二、CLAUDE.md 的多级加载与合并

CLAUDE.md 是用户控制模型行为的主要方式。`load_claude_md()`（`cc/prompts/claudemd.py`）实现了一个 4 级层次加载系统，按优先级从低到高搜索：

### 2.1 四级搜索路径

```
优先级 1（最低）: ~/.claude/CLAUDE.md              — 用户全局配置
优先级 2:          从 cwd 向上搜索 CLAUDE.md       — 项目级配置
优先级 3:          从 cwd 向上搜索 .claude/CLAUDE.md — 项目级（隐藏目录）
优先级 4:          从 cwd 向上搜索 .claude/rules/*.md — 规则片段（可模块化）
优先级 5（最高）:  从 cwd 向上搜索 CLAUDE.local.md   — 本地私有配置（不入 git）
```

**向上搜索**：从当前工作目录开始，逐层向父目录搜索，直到根目录。这允许子项目继承父项目的配置，也可以用本地文件覆盖。

**合并规则**：同一优先级内，越晚找到的文件内容越优先（覆盖前面）。不同优先级之间，高优先级覆盖低优先级。

### 2.2 @include 支持

CLAUDE.md 支持 `@include path/to/file.md` 语法，允许将大文件拆分为模块。解析器会：
1. 找到 `@include` 指令
2. 递归加载被引用的文件
3. 检测循环引用（A includes B includes A）并跳过

### 2.3 注入时机

CLAUDE.md 在 `_build_engine()` 的 Step 1 中加载（`main.py:373`），然后传入 `_build_system()` 拼入 system prompt。这意味着：
- **每次启动时加载一次**（不是每轮对话都重新加载）
- 如果用户在对话中修改了 CLAUDE.md，需要重启才能生效
- 模型通过 Read 工具可以读取 CLAUDE.md 文件，但 system prompt 中的版本是启动时的快照

---

## 三、Memory 系统的双文件注入

Memory 系统提供跨会话持久化。它的核心设计是**两级文件结构**：

### 3.1 存储架构

```
~/.claude/projects/<sha256(cwd)[:12]>/memory/
  ├── MEMORY.md                    # 索引文件（轻量，加载到 system prompt）
  │   ├── - [Title](file.md) — one-line hook
  │   └── - [Another](other.md) — ...
  ├── user_role.md                 # 具体记忆条目（按需读取）
  ├── feedback_testing.md
  ├── project_deadlines.md
  └── reference_grafana.md
```

- **MEMORY.md**：索引文件，每行一个 markdown 链接。最多加载 200 行到 system prompt。
- ***.md 文件**：具体记忆内容，带 YAML frontmatter（name/description/type）。模型通过 Read 工具按需读取。

### 3.2 注入流程

```
build_system_prompt()
  └─> build_memory_prompt(memory_dir, memory_index_content)
        └─> 拼接 8 个子段落：
              1. 目录位置提示
              2. 四种记忆类型定义（user/feedback/project/reference）
              3. 不应保存的内容清单
              4. 保存两步流程（写文件 + 更新索引）
              5. 何时访问记忆
              6. 使用前的验证要求（trust-but-verify）
              7. 与其他持久化机制的区分
              8. MEMORY.md 索引当前内容
```

**关键设计决策**：
- 索引加载到 system prompt（每轮都带），内容文件按需读取（减少 token 消耗）
- 200 行截断：防止索引过大挤占对话上下文
- trust-but-verify：教导模型"记忆说 X 存在不等于 X 现在仍然存在"

### 3.3 提取与注入的时间差

Memory 提取发生在**每轮 REPL 结束后**（异步，不阻塞用户输入），通过 `ExtractionCoordinator` 的 coalescing 策略保证最终一致性。但**提取的记忆不会立即刷新当前 session 的 system prompt**——它们只在**下次启动时**通过 `load_memory_index()` 加载到 system prompt 中。

这意味着：
- Session A 中产生的记忆，Session B 启动时可见
- Session A 中产生的记忆，Session A 后续轮次不可见（除非模型主动用 Read 工具读取）

---

## 四、Transcript —— 模型如何"记住"对话历史

如果说 system prompt 是"规则手册"，transcript 就是"聊天记录"。它是模型感知"上一轮做了什么"的直接来源。

### 4.1 Transcript 的本质

Transcript 是一个 `list[Message]`，在 `query_loop()` 中通过参数 `messages` 传入。它包含四种消息类型：

| 类型 | 内容 | 发给 API？ |
|---|---|---|
| `UserMessage` | 用户输入文本 或 tool_result blocks | 是 |
| `AssistantMessage` | 模型输出（text + tool_use blocks） | 是 |
| `SystemMessage` | 系统通知（信息/警告/错误） | **否**（仅本地 UI） |
| `CompactBoundaryMessage` | 压缩边界标记 | 转为 user 消息发送 |

### 4.2 query_loop 中的 6 个 Transcript 写入点

```python
# 写入点 1: Auto-compact 后替换整个 messages
messages.clear()
messages.extend(compacted)

# 写入点 2: Reactive compact（413 错误后）
messages.clear()
messages.extend(compacted)

# 写入点 3: max_output_tokens 恢复（保存截断输出 + "请继续"）
messages.append(AssistantMessage(content=[TextBlock(text=accumulated_text)]))
messages.append(UserMessage(content="Please continue from where you left off."))

# 写入点 4: 正常 max_tokens 截断（同写入点 3）
messages.append(AssistantMessage(...))
messages.append(UserMessage("Please continue from where you left off."))

# 写入点 5: 模型本轮完整输出写入
messages.append(AssistantMessage(content=assistant_blocks, usage=usage, stop_reason=stop_reason))

# 写入点 6: 工具执行结果写入
messages.append(UserMessage(content=tool_result_blocks))
```

**核心机制**：每次循环的 Phase 2 调用 API 时，**当前完整的 `messages` 列表**作为 `messages` 参数传入。这意味着模型在每一轮都看到完整的对话历史——不是"记住"，而是"重新阅读"。

### 4.3 normalize_messages_for_api —— 发送前的三步修复

在 API 调用前，`normalize_messages_for_api()`（`cc/models/messages.py:184`）对 transcript 进行修复，确保符合 Anthropic API 的严格要求：

**修复 1：角色交替**
- 连续两个 user 消息 → 合并到前一条
- 连续两个 assistant 消息 → 中间插入占位 user 消息 `"Continue."`

**修复 2：以 user 消息开头**
- 如果第一条是 assistant → 前面插入 `"Begin."`

**修复 3：tool_use / tool_result 配对**
- Pass 1：收集所有 tool_use ID 和 tool_result ID
- Pass 2：移除孤儿 tool_result（没有对应 tool_use）
- Pass 3：为孤儿 tool_use 补充合成错误结果 `"[Tool result missing due to internal error]"`

### 4.4 为什么 SystemMessage 不发给 API？

`SystemMessage`（如"正在保存会话..."、"压缩已触发"等通知）仅在本地 UI 展示，不进入 API 消息流。这减少了不必要的 token 消耗，也避免系统通知干扰模型的对话理解。

---

## 五、Compact —— 长对话的上下文生存机制

当对话变长，transcript 会接近上下文窗口上限（默认 200K tokens）。Compact 机制在**不丢失关键信息**的前提下**释放 token 空间**。

### 5.1 双防御策略

| 防御层 | 时机 | 机制 |
|---|---|---|
| **Pre-hoc（预防性）** | 模型生成回复时 | `SUMMARIZE_TOOL_RESULTS` 提示："把工具结果中的关键信息写进你的回复，因为原始结果可能被清除" |
| **Post-hoc（修复性）** | Compact 触发时 | `COMPACT_SYSTEM_PROMPT` 指导摘要模型：保留精确文件路径、函数名、决策结果 |

### 5.2 Auto-Compact 流程

```
Phase 1 检查:
  estimate_messages_tokens(api_messages) >= 200_000 - 13_000
    └─> 触发 compact_messages()
          1. 拆分消息：保留最近 4 轮（POST_COMPACT_KEEP_TURNS * 2 条消息）
          2. 旧消息格式化为文本（tool_result 内容展开到 500 字符）
          3. 调用模型生成摘要（max_tokens=4096）
          4. 用 CompactBoundaryMessage(summary) + 保留的最近消息 替换原列表
```

**滑动窗口设计**：保留最近 4 轮对话不压缩。假设一个任务跨越 20 轮：
- 轮 1-16 → 压缩为摘要
- 轮 17-20 → 原样保留
- 模型知道"刚才在做什么"，也了解"之前的背景"

### 5.3 CompactBoundaryMessage 的转换

`CompactBoundaryMessage` 本身不直接发给 API。在 `normalize_messages_for_api()` 中，它被转换为：

```python
{"role": "user", "content": "[Previous conversation summary]\n{summary}"}
```

这意味着压缩后的历史以**用户消息**的形式出现在对话开头，模型将其视为"用户提供的背景信息"。

### 5.4 Reactive Compact（紧急压缩）

与 auto-compact 的" proactive 预防"不同，reactive compact 是" reactive 应急"：
- 触发条件：API 返回 413 / prompt_too_long（请求已被拒绝）
- 限制：每轮 query_loop 只尝试一次（`has_attempted_reactive_compact` 标志）
- 目的：在 API 已经拒绝后紧急缩减 token，使重试能通过

### 5.5 Token 估算的粗糙性

```python
# cc/api/token_estimation.py
BYTES_PER_TOKEN = 4        # 自然语言
JSON_BYTES_PER_TOKEN = 2   # 结构化数据（标点多，token 密度高）

def estimate_messages_tokens(messages):
    text = json.dumps(messages)
    return len(text.encode("utf-8")) // BYTES_PER_TOKEN
```

使用粗略估算而非精确 tokenization（需要加载 ~2MB BPE vocab），因为：
1. 阈值检测只需要近似精度
2. 估算复杂度 O(n)，几乎无开销
3. 13K 缓冲区补偿了估算误差

---

## 六、Session 恢复 —— 跨会话连续性

### 6.1 存储格式：JSONL

会话以 JSONL（每行一个 JSON 对象）格式存储在 `~/.claude/sessions/{session_id}.jsonl`：

```json
{"type":"user","content":"帮我看看 helper.py","uuid":"...","timestamp":"..."}
{"type":"assistant","content":[{"type":"text","text":"让我先查看文件内容"},{"type":"tool_use","id":"toolu_01","name":"Read","input":{"file_path":"src/utils/helper.py"}}],"uuid":"...","timestamp":"...","stop_reason":"tool_use"}
```

JSONL 的优势：
- 流式写入友好（每写完一条就 flush）
- 部分损坏可恢复（跳过损坏行）
- 便于追加（不需要读取整个文件）

### 6.2 恢复时的 Transcript 修复

`validate_transcript()`（`cc/session/recovery.py:29`）处理崩溃恢复后的对话完整性：

**修复 1：末尾截断**
- 场景：assistant 发出 tool_use 后进程崩溃，tool_result 从未被追加
- 修复：追加一条合成的 user 消息，包含 `is_error=True` 的 tool_result

**修复 2：中间孤立 tool_use**
- 场景：对话在工具执行中间崩溃，后续 user 消息缺少某些 tool_result
- 修复：在现有 user 消息中补入合成的 tool_result

### 6.3 恢复后的 System Prompt

Session 恢复时，**只有 transcript（messages）被恢复**，system prompt 是**重新组装**的：

```python
# 恢复流程
messages = load_session(session_id)       # 从 JSONL 加载历史消息
system = _build_system(cwd, model, claude_md)  # 重新组装 system prompt（会加载最新的 Memory/CLAUDE.md）
# 然后进入 query_loop(messages=messages, system_prompt=system, ...)
```

这意味着：
- 跨会话的连续性依赖 transcript 的完整保存
- 跨会话的"记忆升级"依赖 system prompt 的重新组装（加载最新的 Memory/CLAUDE.md）

---

## 七、完整数据流：从用户输入到 API 请求

以一个具体例子走完全流程：

### 场景：用户说"帮我看看 helper.py 里有没有 dead code"

**Step 1: Engine 构建时（启动时，只执行一次）**

```
_build_engine()
  ├─> load_claude_md(cwd)              # 搜索并加载 CLAUDE.md
  ├─> _build_system(cwd, model, claude_md)
  │     ├─> get_memory_dir(cwd)        # ~/.claude/projects/<sha>/memory/
  │     ├─> load_memory_index(cwd)     # 读取 MEMORY.md
  │     └─> build_system_prompt(...)   # 拼装 11 段 system prompt
  │           ├─> 静态段落（1-7）
  │           ├─> 动态段落（8-9）
  │           ├─> Memory 段落（10，如启用）
  │           └─> CLAUDE.md 段落（11，如存在）
  └─> system = "\n\n".join(parts)       # 最终 system prompt 字符串
```

**Step 2: REPL 启动，等待用户输入**

此时 `messages = []`（空列表），system prompt 已准备好。

**Step 3: 用户输入"

```
UserMessage(content="帮我看看 helper.py 里有没有 dead code")
  └─> messages.append(user_msg)        # messages 现在包含 1 条消息
```

**Step 4: query_loop 第一轮**

```
Phase 1:
  api_messages = normalize_messages_for_api(messages)
    └─> [{"role":"user","content":"帮我看看 helper.py 里有没有 dead code"}]
  estimate_messages_tokens(api_messages) = 15  # 远小于阈值，不触发 compact

Phase 2:
  call_model(
    messages=api_messages,              # 1 条 user 消息
    system=system_prompt,               # 完整的 system prompt（含 CLAUDE.md + Memory）
    tools=tool_schemas,                 # Read、Grep、Glob 等工具定义
  )
    └─> 模型返回流式输出：
        TextDelta("Let me read the file first.")
        ToolUseStart(name="Read", input={"file_path": "src/utils/helper.py"})
        TurnComplete(stop_reason="tool_use")

Phase 4:
  messages.append(AssistantMessage(     # 写入点 5
    content=[TextBlock("Let me read..."), ToolUseBlock("Read", {...})]
  ))
  messages.append(UserMessage(          # 写入点 6
    content=[ToolResultBlock("def helper():\n    pass", tool_use_id="toolu_01")]
  ))
  continue  # 有工具调用，继续下一轮
```

**Step 5: query_loop 第二轮**

```
Phase 1:
  api_messages = normalize_messages_for_api(messages)
    └─> [
      {"role":"user","content":"帮我看看 helper.py 里有没有 dead code"},
      {"role":"assistant","content":[{"type":"text","text":"Let me read..."},{type:"tool_use",...}]},
      {"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01","content":"def helper():\n    pass"}]}
    ]
  # 模型现在看到：用户请求 + 自己上一轮说要读文件 + 文件内容结果

Phase 2:
  call_model(messages=api_messages, system=system_prompt, tools=...)
    # 模型基于完整上下文分析代码，得出结论
```

### 关键观察

每轮 API 调用时，`messages` 参数包含**从对话开始到现在的所有消息**。模型不是"记住"了上一轮，而是**每轮都重新读取完整的聊天记录**。这就是连续性保持的本质。

---

## 八、与 Harness 的关联

理解 Claude Code Runtime 的上下文注入机制，对理解当前使用的 Harness 有重要参考价值：

| 机制 | Claude Code Runtime | Harness (当前会话) |
|---|---|---|
| **System Prompt** | 11 段拼装（静态+动态+条件） | 系统级指令 + 技能注入 + 项目记忆 |
| **Transcript** | `list[Message]` 显式管理 | 对话历史隐式管理 |
| **Memory** | 文件系统持久化（~/.claude/projects/.../memory/） | `MEMORY.md` 索引 + .md 文件 |
| **Compact** | Auto-compact + Reactive compact | 由底层平台处理 |
| **Session 恢复** | JSONL 存储 + validate_transcript 修复 | 由底层平台处理 |

**对日常开发的启示**：

1. **CLAUDE.md 的效力**：当前会话中的 CLAUDE.md 内容已被加载到 system prompt 中，这解释了为什么模型会遵循其中的规则（如"不用 emoji"、"先给结论"）。

2. **Memory 的时效性**：当前会话中创建的记忆（如用户偏好、项目决策）不会立即影响当前对话，但会在**下次启动时**加载到 system prompt。

3. **Transcript 的累积效应**：随着对话进行，transcript 不断增长。如果看到模型"忘记"了早期的重要信息，可能是因为：
   - 上下文窗口接近上限，早期信息已被 compact 压缩
   - 模型在长上下文中对早期信息的注意力衰减（这是 LLM 的固有局限，非 Runtime 问题）

4. **工具结果的生命周期**：工具结果作为 `ToolResultBlock` 存在于 transcript 中，但在 compact 时可能被清除。这就是为什么 `SUMMARIZE_TOOL_RESULTS` 提示模型"把关键信息写进回复"——一旦写进回复（TextBlock），就成为 transcript 的一部分，compact 时不会被清除。

---

## 九、待深入话题

以下话题在本次调研中有涉及但未完全展开：

- [ ] StreamingToolExecutor 的并发执行与 transcript 写入的时序关系
- [ ] Agent Teams 的 contextvars 身份隔离——子 agent 的 transcript 如何与父 agent 隔离
- [ ] MCP 协议的 Client-Server 工具发现流程——外部工具如何注入到 system prompt 的工具定义中
- [ ] Prompt Caching 的具体实现——API 层如何将 `list[str]` 映射到 cache_control 段
- [ ] Hook 系统（PreToolUse/PostToolUse）如何修改 transcript 的流向

---

## 参考文件

| 文件 | 作用 |
|---|---|
| `cc/prompts/builder.py` | System prompt 拼装 |
| `cc/prompts/sections.py` | System prompt 各段落的文本内容 |
| `cc/prompts/claudemd.py` | CLAUDE.md 多级加载 |
| `cc/models/messages.py` | 消息类型定义 + normalize_messages_for_api |
| `cc/core/query_loop.py` | Transcript 写入的 6 个点位 |
| `cc/compact/compact.py` | Auto-compact + reactive compact |
| `cc/session/storage.py` | 会话持久化（JSONL） |
| `cc/session/recovery.py` | Transcript 恢复修复 |
| `cc/memory/session_memory.py` | Memory 目录管理和索引加载 |
| `cc/memory/extractor.py` | Memory 提取的 coalescing 策略 |
