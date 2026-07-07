# Python 源码解析：上下文管理

## 模块概览

上下文管理的代码集中在一个文件里：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `mewcode/context/manager.py` | 703 | 两层上下文压缩：Layer 1 三趟裁剪 + Layer 2 LLM 全量摘要 + 压缩后恢复 + 熔断和手动入口 |

703 行里大约前 520 行是两层压缩的核心实现，后面约 180 行是「压缩后恢复」段，集中处理摘要替换之后怎么把工作记忆补回来。没有子模块，所有逻辑平铺在一个文件中。这种「小而全」的组织方式是刻意的选择，因为上下文管理的各个步骤（判断、裁剪、摘要、重建、恢复）紧密耦合，拆开反而增加理解成本。

## 核心类型

### 常量：六条控制线

```Python
SINGLE_RESULT_CHAR_LIMIT = 5_000
AGGREGATE_CHAR_LIMIT = 20_000
PREVIEW_CHARS = 2_000

KEEP_RECENT_TURNS = 10
OLD_RESULT_SNIP_CHARS = 2_000
SNIPPED_TAG = "<snipped>"
```

前三个常量控制 Layer 1 的溢写行为。 `SINGLE_RESULT_CHAR_LIMIT` 是单个工具结果的溢写阈值，超过 5000 字符就往磁盘上写。 `AGGREGATE_CHAR_LIMIT` 是单条消息里所有工具结果加起来的上限，20000 字符。 `PREVIEW_CHARS` 控制溢写后保留多少预览内容，2000 字符。

后三个控制过期裁剪。最近 10 轮对话不动，更老的消息里超过 2000 字符的工具结果直接裁掉。 `SNIPPED_TAG` 是裁剪标记，用来判断一个结果是不是已经被处理过了。

Layer 2 有单独的阈值常量：

```Python
SUMMARY_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_SAFETY_MARGIN = 13_000
MANUAL_COMPACT_SAFETY_MARGIN = 3_000
```

`SUMMARY_OUTPUT_RESERVE` 预留给 LLM 生成摘要的输出空间。 `AUTO_COMPACT_SAFETY_MARGIN` 是自动压缩的安全边距，要留出 13000 Token 的缓冲。 `MANUAL_COMPACT_SAFETY_MARGIN` 只留 3000，因为用户手动触发时更激进。

### CompactEvent：压缩事件

```Python
@dataclass
class CompactEvent:
    before_tokens: int
```

用 `@dataclass` 定义，只有一个字段：压缩前的 Token 数。Agent Loop 收到这个事件后可以计算出压缩效果（压缩前后的 Token 差值），展示给用户看。

### CompactCircuitBreaker：熔断器

```Python
@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = field(
        default=0, init=False)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures
```

经典的熔断器模式。连续失败 3 次就「跳闸」，不再尝试自动压缩。 `field(default=0, init=False)` 表示 `consecutive_failures` 不参与构造函数，初始值固定为 0。成功一次就归零，失败一次就累加。 `is_open()` 返回 `True` 时表示熔断器已跳闸。

为什么需要熔断？Layer 2 要调 LLM 做摘要，如果上下文已经大到 LLM 处理不了，每次尝试都会失败并浪费 API 调用。熔断器让系统在连续失败后自动停止尝试，降级到只做 Layer 1 的本地裁剪。

## 主流程走读

### 入口：apply\_tool\_result\_budget

这是 Layer 1 的入口函数，Agent Loop 每轮迭代都会调用。它对对话历史做三趟扫描：

```Python
def apply_tool_result_budget(
    conversation: ConversationManager,
    session_dir: Path,
) -> None:
    for msg in conversation.history:
        if not msg.tool_results:
            continue
```

外层循环遍历所有消息，只处理包含工具结果的消息。三趟扫描嵌套在这个循环里，这意味着对每条消息都会依次执行三种处理。

### 第一趟：溢写单个超大结果

```Python
        # Pass 1: persist individually oversized results
        for tr in msg.tool_results:
            if tr.content.startswith(PERSISTED_TAG):
                continue
            if len(tr.content) > SINGLE_RESULT_CHAR_LIMIT:
                fp = persist_tool_result(
                    tr.tool_use_id, tr.content, session_dir)
                tr.content = make_persisted_preview(
                    tr.content, fp)
```

任何单个工具结果超过 5000 字符就触发溢写。 `startswith(PERSISTED_TAG)` 检查这个结果是不是已经被溢写过了，防止重复处理。溢写后用预览替换原始内容。

`persist_tool_result` 的实现用了 `os.O_EXCL` 标志：

```Python
def persist_tool_result(
    tool_use_id: str, content: str, session_dir: Path
) -> Path:
    file_path = session_dir / f"{tool_use_id}.txt"
    try:
        fd = os.open(str(file_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        pass
    return file_path
```

`O_EXCL` 的含义是：如果文件已经存在就失败。配合 `except FileExistsError: pass` ，整个操作是幂等的。文件名用 `tool_use_id` ，天然唯一。第一次写入成功，后续调用直接跳过。用操作系统层面的原子性保证来实现幂等，比自己检查文件再写入更可靠，不存在检查和写入之间的竞态窗口。

预览内容的格式：

```Python
def make_persisted_preview(
    content: str, file_path: Path
) -> str:
    size_kb = len(content.encode("utf-8")) // 1024
    preview = content[:PREVIEW_CHARS]
    return (
        f"{PERSISTED_TAG}\n"
        f"输出太大（{size_kb}KB），完整内容已保存到：\n"
        f"{file_path}\n"
        f"\n"
        f"预览（前 2KB）：\n"
        f"{preview}\n"
        f"</persisted-output>"
    )
```

用 `encode("utf-8")` 计算字节大小而不是字符数，因为中文字符占 3 个字节，按字符数算会低估实际大小。预览保留前 2000 个字符，足够 LLM 理解输出的大致内容。整段内容用 `<persisted-output>` 标签包裹，方便后续检测。

### 第二趟：聚合预算控制

```Python
        # Pass 2: enforce aggregate limit per message
        total = sum(len(tr.content) for tr in msg.tool_results)
        if total <= AGGREGATE_CHAR_LIMIT:
            continue
        ranked = sorted(
            [tr for tr in msg.tool_results
             if not tr.content.startswith(PERSISTED_TAG)],
            key=lambda tr: len(tr.content), reverse=True)
```

先算总长度，没超过 20000 就跳过。超过了就按长度降序排列，先裁最大的：

```Python
        for tr in ranked:
            if total <= AGGREGATE_CHAR_LIMIT: break
            old_len = len(tr.content)
            fp = persist_tool_result(tr.tool_use_id, tr.content, session_dir)
            tr.content = make_persisted_preview(tr.content, fp)
            total -= old_len - len(tr.content)
```

先算这条消息里所有工具结果的总长度。没超过 20000 就跳过，超过了就开始裁。

裁剪策略很有意思：按长度降序排列，先裁最大的。每裁一个，总量就减少一些，减到阈值以下就停。已经被第一趟溢写过的结果（以 `PERSISTED_TAG` 开头）不参与排序，因为它们已经很短了。

`total -= old_len - len(tr.content)` 这行是核心。溢写后新内容（预览）比原内容短，差值就是节省的字符数。动态更新总量，一旦达标就 `break` 。

### 第三趟：裁剪过期结果

```Python
    total_turns = _count_turns(conversation.history)
    if total_turns <= KEEP_RECENT_TURNS:
        return
    turns_seen = 0
    old_boundary = total_turns - KEEP_RECENT_TURNS
    for msg in conversation.history:
        if msg.role == "assistant" and not msg.tool_uses:
            turns_seen += 1
        if turns_seen > old_boundary:
            break
```

和前两趟不同，这趟操作的范围不是单条消息，而是整个对话历史中的「老」消息。「一轮」的定义是一条没有工具调用的 assistant 消息。Python 版精确计数了对话轮次，不到 10 轮就直接返回。

超过 10 轮后，从头开始遍历，用 `turns_seen` 追踪当前位置。一旦过了 `old_boundary` ，就 `break` 停止。在 boundary 之前的消息做裁剪：

```Python
        for tr in msg.tool_results:
            if tr.content.startswith(SNIPPED_TAG):
                continue
            if len(tr.content) > OLD_RESULT_SNIP_CHARS:
                preview = tr.content[:200]
                tr.content = (
                    f"{SNIPPED_TAG}\n"
                    f"(旧结果已裁剪，原始长度 {len(tr.content)} 字符)\n"
                    f"{preview}\n... (snipped)")
```

工具结果超过 2000 字符就裁成一段简短摘要，只保留前 200 字符的预览。

注意裁剪和溢写的区别：溢写把完整内容存到磁盘，LLM 可以用 ReadFile 读回来；裁剪是真的丢弃了，只保留前 200 字符的预览。老旧的工具输出大概率不需要了，丢弃是合理的。

## 第二层：LLM 全量摘要

Layer 1 做完之后如果上下文还是太大，就进入 `auto_compact` 。这一层要调 LLM 把整个对话压缩成一段摘要。

### 阈值计算

```Python
def compute_compact_threshold(
    context_window: int, manual: bool = False
) -> int:
    effective = context_window - SUMMARY_OUTPUT_RESERVE
    margin = (MANUAL_COMPACT_SAFETY_MARGIN
              if manual
              else AUTO_COMPACT_SAFETY_MARGIN)
    return effective - margin
```

阈值计算考虑了两件事：摘要输出本身需要空间（20000 Token），还要留安全边距。自动模式留 13000，手动模式只留 3000。手动模式更激进是因为用户主动触发时，说明他已经意识到上下文快满了，应该尽量保留对话而不是过早压缩。

快捷判断函数：

```Python
def should_auto_compact(
    last_input_tokens: int, context_window: int
) -> bool:
    return last_input_tokens >= compute_compact_threshold(
        context_window)
```

`last_input_tokens` 来自上一轮 LLM 调用的实际用量反馈，不是本地估算。直接用 API 返回的 Token 计数做判断，比本地估算更精确，不会因为估算偏差导致压缩过早或过晚。

### 摘要提示词

```Python
SUMMARY_PROMPT = """\
你是一个对话摘要助手。你只能输出纯文本，不能调用任何工具。
请对下面的对话生成一份结构化摘要。
先在 <analysis> 标签中梳理对话中发生了什么（会被丢弃），
然后在 <summary> 标签中输出正式摘要。
<summary> 必须包含以下 9 个部分：
1. 主要请求和意图  2. 关键技术概念  3. 文件和代码段
4. 错误和修复  5. 问题解决过程  6. 所有用户消息（原文保留）
7. 待办任务  8. 当前工作（要最详细）  9. 可能的下一步"""
```

9 个部分的结构化要求确保摘要不会遗漏关键信息。特别是第 6 点「所有用户消息原文保留」，这是为了让压缩后的对话仍然能准确反映用户的意图。第 8 点「当前工作要最详细」是因为 Agent 大概率还在继续之前没做完的任务。

开头的「不能调用任何工具」反复强调了三次（提示词开头、结尾、最后的 user 消息里又说一次），因为 LLM 在看到对话中有工具调用的上下文时，可能会模仿着也去调工具。

### auto\_compact 编排

```Python
async def auto_compact(
    conversation, client, context_window,
    session_dir, manual=False, breaker=None,
) -> CompactEvent | str | None:
    threshold = compute_compact_threshold(context_window, manual=manual)
    if not manual and conversation.last_input_tokens < threshold:
        return None
    if not manual and breaker is not None and breaker.is_open():
        return "自动压缩已熔断（连续失败 3 次），请手动处理或使用 /compact"
```

返回值是三选一： `CompactEvent` 表示压缩成功， `str` 表示失败或熔断消息， `None` 表示不需要压缩。非手动模式下先检查阈值，没超过就返回 `None` 。然后检查熔断器，已跳闸就返回提示消息。

构建摘要对话时的重试逻辑：

```Python
    for attempt in range(3):
        try:
            collected_text = ""
            async for event in client.stream(summary_conv, system=SUMMARY_PROMPT):
                if isinstance(event, TextDelta):
                    collected_text += event.text
            llm_output = collected_text
            break
```

流式收集 LLM 输出，遇到 `TextDelta` 就拼接文本。成功就 `break` 跳出重试循环。如果因为上下文太长而失败，走降级路径：

```Python
        except Exception as e:
            err_msg = str(e).lower()
            if "prompt" in err_msg and "long" in err_msg:
                groups = _group_messages_by_turn(summary_conv.history[1:-1])
                drop_count = max(1, len(groups) // 5)
                remaining = groups[drop_count:]
                summary_conv.history = (
                    [summary_conv.history[0]]
                    + [m for g in remaining for m in g]
                    + [summary_conv.history[-1]])
                continue
```

通过错误消息里的关键词判断是否「太长」。如果是，砍掉对话的前 20%，重试。最多重试 3 次。 `_group_messages_by_turn` 把消息按对话轮次分组，每次丢掉最老的 1/5 组。

`_group_messages_by_turn` 的分组逻辑：

```Python
def _group_messages_by_turn(
    messages: list[Message]
) -> list[list[Message]]:
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        current.append(msg)
        if msg.role == "assistant" and not msg.tool_uses:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups
```

一轮的结束标志是「没有工具调用的 assistant 消息」。从 user 提问开始，经过可能的多次工具调用，到 assistant 最终回复，构成一个完整的对话轮次。

### 摘要提取与对话重建

```Python
def extract_summary(llm_output: str) -> str:
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start == -1 or end == -1:
        return llm_output
    return llm_output[
        start + len("<summary>"):end].strip()
```

从 LLM 输出里提取 `<summary>` 标签之间的内容。如果 LLM 没按格式输出（找不到标签），就把整个输出当摘要用，不会丢失内容。 `<analysis>` 标签的内容自然被丢弃了，因为它在 `<summary>` 之前。

```Python
COMPACT_BOUNDARY_MESSAGE = (
    "上面是之前对话的摘要。如果你需要文件的具体内容，"
    "请用 ReadFile 重新读取，不要根据摘要猜测代码细节。"
)

def build_compact_messages(
    summary: str
) -> list[Message]:
    return [
        Message(role="user",
                content=f"[摘要]\n{summary}"),
        Message(role="assistant",
                content=COMPACT_BOUNDARY_MESSAGE),
    ]
```

用摘要创建两条消息：一条 user 消息放摘要，一条 assistant 消息提醒自己「不要根据摘要猜代码」。这个提醒很重要，因为摘要里的代码片段可能是不完整的，LLM 如果依赖它来做修改会出错。

最后替换对话并清理磁盘：

```Python
    summary = extract_summary(llm_output)
    new_messages = build_compact_messages(summary)

    conversation.replace_history(new_messages)
    cleanup_tool_results(session_dir)

    if breaker is not None:
        breaker.record_success()

    return CompactEvent(before_tokens=before_tokens)
```

`replace_history` 把整个对话历史替换成摘要的两条消息。 `cleanup_tool_results` 清理之前溢写到磁盘的工具结果文件，因为压缩后那些文件引用已经失效了。熔断器记录成功，归零计数。返回 `CompactEvent` 让上层知道压缩完成了。

`cleanup_tool_results` 的实现：

```Python
def cleanup_tool_results(session_dir: Path) -> None:
    if session_dir.exists():
        shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
```

先删除整个目录，再重新创建空目录。用 `shutil.rmtree` 而不是逐个删文件，因为不关心里面具体有什么，全部清掉就对了。

## 第三层：压缩后恢复

Layer 2 把整段对话换成了一段摘要 + 一句边界提醒，上下文是腾出来了，但模型也瞬间「失忆」。它刚刚还能看到完整代码、工具输出、用户原话，下一秒只剩三句话的摘要。如果接下来用户问「刚才那个文件里 handle\_error 是怎么实现的」，模型会基于摘要里的几个名词猜测，错的概率不低。 `mewcode/context/manager.py` 在「Post-compact recovery state」段解决这个问题：把摘要前最近读过的文件、最近调用过的技能、当前的工具表，全部拼到摘要消息后面。

### RecoveryState：跨轮快照仓库

```Python
class RecoveryState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(
                path=path, content=content, timestamp=time.time()
            )
```

`threading.Lock` 是必要的。 `StreamingExecutor` 用 `asyncio.gather` 并发跑 ReadFile，多个回写可能交错。空 path 直接 return，给测试和一次性脚本留个安全网，不需要刻意构造对象也不会崩。

`Agent.__init__` 默认就初始化一个：

```Python
self.recovery_state: RecoveryState = RecoveryState()
```

不需要可选注入，因为这是一个无副作用的本地容器，每个 Agent 都该有。

### 何时记录文件

ReadFile 工具的回写挂在 `Agent` 里一个小方法上：

```Python
def _snapshot_for_recovery(
    self, tc: ToolCallComplete, result: ToolResult
) -> None:
    if result.is_error or tc.tool_name != "ReadFile":
        return
    path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return
    self.recovery_state.record_file_read(path, content)
```

`_execute_single_tool_direct` 和 `_execute_tool` 两条工具执行路径末尾都加了一行 `self._snapshot_for_recovery(tc, result)` 。两个路径都要加是因为前者走批量并发、后者走交互式权限确认，两条都可能触发 ReadFile。

`encoding="utf-8", errors="replace"` 是关键。文件可能不是合法 UTF-8（比如二进制图片，或者声明了 GBK 编码的 Python 源码）， `errors="replace"` 让它把无法解码的字节换成 `\ufffd` 占位符而不是抛异常。 `OSError` 静默吞掉，文件读不到就跳过这次记录，绝不让 Agent 主流程崩。

### 何时记录技能

技能的记录在 `SkillExecutor` 里：

```Python
def execute_inline(self, skill: SkillDef, args: str) -> None:
    prompt = substitute_arguments(skill.prompt_body, args)
    self.agent.activate_skill(skill.name, prompt)
    if getattr(self.agent, "recovery_state", None) is not None:
        self.agent.recovery_state.record_skill_invocation(skill.name, prompt)
```

inline 模式记的是渲染后的 prompt（含 `$ARGUMENTS` 替换结果），fork 模式记的是原始 `skill.prompt_body` ：

```Python
async def execute_fork(self, skill: SkillDef, args: str) -> str:
    prompt = substitute_arguments(skill.prompt_body, args)
    if getattr(self.agent, "recovery_state", None) is not None:
        self.agent.recovery_state.record_skill_invocation(
            skill.name, skill.prompt_body
        )
```

`getattr(..., None) is not None` 这个判断是给老版本 Agent 留的兼容窗，万一某个测试桩没有 `recovery_state` 字段也能正常跑。

### 限额：四个硬上限

```Python
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
_RECOVERY_CHARS_PER_TOKEN = 3.5
```

最多 5 个文件、每个最多 5000 token；技能总预算 25000 token、单技能 5000 token 上限。这些是硬上限，超出就丢，不报错。这意味着恢复块的体积可以预测，最坏情况大约 60K token，远低于 `compute_compact_threshold` 给出的触发线。换句话说，恢复块自己不会反过来把刚腾出来的空间又顶爆。

`_truncate_by_tokens` 按 `len(s) / 3.5` 折算 byte 上限，超额时切尾追加 `\n… (内容已截断)` 标记。这个标记是给模型的提示，让它知道这里不是完整内容，需要的话要重读。

### build\_recovery\_attachment：四段渲染

```Python
def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: list[Mapping[str, Any]] | None,
) -> str:
    sections: list[str] = []

    if state is not None:
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            buf = ["## 最近读过的文件\n",
                   "以下快照是文件读取工具上次返回的内容。如需当前字节请重新读取。\n"]
            ...
            sections.append("".join(buf))
        ...
```

输出顺序固定：最近读过的文件 → 已激活的技能 → 当前可用工具 → 收尾提示。每段都可能为空，全空时返回 `""` ，调用方就当压缩没附带恢复块处理。文件段按 timestamp 倒序排，最近读的在最上面。

技能段在循环里实时算预算：

```Python
used = 0
emitted = False
for sk in skills:
    body = _truncate_by_tokens(sk.body, RECOVERY_TOKENS_PER_SKILL)
    tokens = _approx_tokens(body) + _approx_tokens(sk.name) + 8
    if used + tokens > RECOVERY_SKILLS_BUDGET:
        break
    used += tokens
    buf.append(f"### {sk.name}\n\n{body}\n")
    emitted = True
```

`+ 8` 是给 `### \n\n` 这种 markdown 开销留的小余量，估算用，不需要精确。算到超预算就 break，保证总长可控。

工具段列出当前 schema 表里的所有工具名和描述首行：

```Python
if tool_schemas:
    buf = ["## 可用工具\n",
           "你仍然可以调用以下工具，需要时直接发起调用即可：\n"]
    for t in tool_schemas:
        name = t.get("name") if isinstance(t, Mapping) else None
        if not name:
            continue
        desc = t.get("description", "") if isinstance(t, Mapping) else ""
        desc = _first_line(desc or "")
        if desc:
            buf.append(f"- {name} — {desc}\n")
        else:
            buf.append(f"- {name}\n")
```

这段是关键。摘要替换之后，模型如果不知道自己还有什么工具可用，行为会变保守，宁可问用户也不肯发起调用。把工具表显式写一遍，给模型一个清晰的「你还能这样做」的提醒。API 层每次请求本来就会带 `tools` 参数，所以这是双重保险。

收尾段是固定的一小段提示：

> 以上恢复的上下文是重建的。若需要原文代码、错误信息或用户原话，请用文件读取工具重新读取，不要根据摘要猜测细节。

修正的是模型一个常见错误倾向：看到摘要里说「修改了 foo.py 的 handle\_error」，可能就直接基于摘要那几句话改代码，结果改错。这段明确告诉它要原文请去重读。

### 怎么拼到摘要后面

`auto_compact` 新增两个 kwargs，把 recovery 状态和当前工具表透传进来：

```Python
async def auto_compact(
    conversation: ConversationManager,
    client: Any,
    context_window: int,
    session_dir: Path,
    protocol: str = "anthropic",
    manual: bool = False,
    breaker: CompactCircuitBreaker | None = None,
    recovery: RecoveryState | None = None,
    tool_schemas: list[Mapping[str, Any]] | None = None,
) -> CompactEvent | str | None:
```

生成 summary 之后调一次 `build_recovery_attachment` 拿到 attachment 字符串：

```Python
summary = extract_summary(llm_output)
attachment = build_recovery_attachment(recovery, tool_schemas)
new_messages = build_compact_messages(summary, attachment=attachment)
conversation.replace_history(new_messages)
```

`build_compact_messages` 多了一个 `attachment` 参数，把它用 `\n\n---\n\n` 拼到 `[摘要]\n{summary}` 之后：

```Python
def build_compact_messages(summary: str, attachment: str = "") -> list[Message]:
    user_content = f"[摘要]\n{summary}"
    if attachment:
        user_content += "\n\n---\n\n" + attachment
    return [
        Message(role="user", content=user_content),
        Message(role="assistant", content=COMPACT_BOUNDARY_MESSAGE),
    ]
```

`COMPACT_BOUNDARY_MESSAGE` 就是那条 assistant 「上面是之前对话的摘要……」的边界提醒，原本就有。整段会话史现在只剩两条消息：一条带摘要+恢复块的 user，一条边界提醒的 assistant。

### 三个调用点都要传

`Agent.run` 主循环、 `manual_compact` 、 `run_to_completion` 三个地方都调 `auto_compact` ，三个都得传 `recovery` 和 `tool_schemas` ：

```Python
compact_result = await auto_compact(
    conversation,
    self.client,
    self.context_window,
    self.session_dir,
    protocol=self.protocol,
    breaker=self.compact_breaker,
    recovery=self.recovery_state,
    tool_schemas=self.registry.get_all_schemas(self.protocol),
)
```

`self.registry.get_all_schemas(self.protocol)` 每次都现算，因为工具表可能在 skill 触发后被 `ToolNameFilter` 改变。每次现算就保证恢复块里列出的工具和下一次 `client.stream` 实际看到的工具集完全一致，模型不会被误导。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 两层架构 | Layer 1（ `apply_tool_result_budget` ）每轮无条件跑，Layer 2（ `auto_compact` ）超阈值才触发 |
| 三趟扫描 | 单结果溢写 → 聚合预算 → 过期裁剪，每趟解决不同维度的问题 |
| 溢写幂等 | 文件名用 `tool_use_id` ， `O_EXCL` 保证原子性 |
| 阈值区分 | 自动模式留 13000 Token 边距，手动模式只留 3000 |
| 摘要质量 | `<analysis>` + `<summary>` 两阶段提示词，只保留后者 |
| 重试降级 | 上下文太长时砍掉前 20% 的对话轮次，最多重试 3 次 |
| 熔断保护 | `CompactCircuitBreaker` 连续 3 次失败后停止自动压缩 |
| 对话重建 | 摘要作为 user 消息 + assistant 提醒，替换原始历史 |
| 磁盘清理 | 压缩后 `shutil.rmtree` 清空溢写目录 |
| 跨轮快照 | `RecoveryState` 用 `threading.Lock` 守护两张 dict，ReadFile 后重读 utf-8 字节落帐 |
| 恢复块限额 | 5 文件 × 5K token / 25K token 技能预算 / 单技能 5K，总长稳定在 60K 内 |
| 工具表对齐 | 三个 `auto_compact` 调用点都现算 `get_all_schemas` ，与下一次 `client.stream` 看到的工具集保持一致 |