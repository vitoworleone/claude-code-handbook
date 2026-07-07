# Python 源码解析：记忆系统

## 模块概览

记忆系统的代码集中在 `mewcode/memory/` 目录下，三个文件各管一层：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `instructions.py` | 65  | 自定义指令：加载 `MEWCODE.md` ，支持 `@include` 嵌套引用 |
| `session.py` | 480 | 会话持久化：JSONL 格式存储、恢复、校验、过期清理 |
| `auto_memory.py` | 213 | 自动记忆：LLM 提取对话中的长期知识，分用户/项目两级存储 |

三个文件加起来大约 760 行。量不小，但每个文件的职责边界非常清晰。下面按「从简单到复杂」的顺序走读。

## 核心类型

### RecordType：JSONL 里每行是什么

会话持久化的基本单位是 `SessionRecord` ，每条记录都带一个类型标签：

```Python
class RecordType(str, Enum):
    SYSTEM_PROMPT = "system_prompt"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    COMPRESSION = "compression"
```

五种类型里有一个很容易忽略的： `COMPRESSION` 。当上下文压缩后，压缩结果会作为一条 `COMPRESSION` 记录写入 JSONL 文件。恢复会话时，这条记录会被转成 `[摘要]` 前缀的消息注入对话，让 LLM 知道之前发生了什么。

### SessionRecord：一条记录的完整结构

```Python
@dataclass
class SessionRecord:
    type: RecordType
    content: Any
    timestamp: datetime
    tool_use_id: str | None = None
    is_error: bool = False
```

`content` 的类型是 `Any` ，这不是偷懒。对于纯文本消息（用户、助手）， `content` 是字符串；对于带工具调用的助手回复， `content` 是一个列表，里面混合了 `text` 块和 `tool_use` 块。这种灵活性让同一个序列化格式能覆盖所有消息类型。

### SessionMeta：会话的元数据

```Python
@dataclass
class SessionMeta:
    id: str
    title: str = ""
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(...)
    last_active: datetime = field(...)
```

每个会话除了 JSONL 文件，还有一个 `.meta` 文件存元数据。 `title` 取自用户第一条消息的前 50 个字符， `summary` 可以由 LLM 生成。元数据和对话内容分开存储，列出所有会话时不用解析巨大的 JSONL 文件，只读小小的 `.meta` 就够了。

## 第一层记忆：自定义指令

自定义指令是最简单的一层。 `instructions.py` 只有 65 行，做两件事：加载 `MEWCODE.md` 文件，处理 `@include` 指令。

### 加载路径

```Python
def load_instructions(project_root: str) -> str:
    paths = [
        root / "MEWCODE.md",
        root / ".mewcode" / "MEWCODE.md",
        home / ".mewcode" / "MEWCODE.md",
    ]
    sections: list[str] = []
    for path in paths:
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8")
            processed = process_includes(content, path.parent, root)
            sections.append(processed)
    return "\n---\n".join(sections)
```

三个候选路径，按优先级从高到低：项目根目录、项目的 `.mewcode` 子目录、用户主目录。注意这里不是「找到一个就停」，而是把所有找到的文件内容用 `---` 分隔符拼起来。项目级指令和用户级指令可以共存。

### @include 处理

```Python
def process_includes(content, base_dir, project_root, depth=0):
    if depth >= MAX_INCLUDE_DEPTH:
        return content
    # ...
    for line in lines:
        if not stripped.startswith(INCLUDE_PREFIX):
            result.append(line)
            continue
        abs_path = (base_dir / rel_path).resolve()
        try:
            abs_path.relative_to(resolved_root)  # 越界检查
        except ValueError:
            result.append("<!-- @include blocked -->")
            continue
```

`@include` 让你可以把大指令拆成多个文件。但这里有两道安全防线：第一，递归深度限制为 5 层，防止 `A includes B includes A` 这种循环引用；第二，路径必须在项目根目录内，防止通过 `@include ../../etc/passwd` 读取系统文件。

## 第二层记忆：会话持久化

会话持久化是记忆系统最复杂的一层，占了 480 行。核心思路是：每条消息实时追加到 JSONL 文件，恢复会话时逐行读回来。

### JSONL 格式的选择

为什么用 JSONL 而不是 JSON？看序列化方法就明白了：

```Python
def to_jsonl(self) -> str:
    data: dict[str, Any] = {
        "type": self.type.value,
        "content": self.content,
        "timestamp": self.timestamp.isoformat(),
    }
    if self.tool_use_id is not None:
        data["tool_use_id"] = self.tool_use_id
    if self.type == RecordType.TOOL_RESULT:
        data["is_error"] = self.is_error
    return json.dumps(data, ensure_ascii=False)
```

每条记录独立成一行 JSON。追加写入只需要 `file.write(line + "\n")` ，不需要读取整个文件再反序列化。对于可能几百轮的长对话，JSONL 的追加性能远优于反复重写一个巨大的 JSON 数组。

还有一个微妙的细节： `is_error` 字段只在 `TOOL_RESULT` 类型时才写入。这不是偷懒，是刻意减少每行的冗余字段，保持 JSONL 文件尽可能紧凑。

### Message 到 Record 的转换

一条 `Message` 可能变成多条 `SessionRecord` 。最典型的情况是工具调用结果：

```Python
@classmethod
def from_message(cls, message: Message):
    if message.tool_results:
        for tr in message.tool_results:
            records.append(cls(type=RecordType.TOOL_RESULT,
                content=tr.content, timestamp=now,
                tool_use_id=tr.tool_use_id, is_error=tr.is_error))
    elif message.role == "assistant" and message.tool_uses:
        content_blocks = []
        # 文本和工具调用打包在一起
        for tu in message.tool_uses:
            content_blocks.append({"type": "tool_use", ...})
        records.append(cls(type=RecordType.ASSISTANT, content=content_blocks, ...))
```

一条用户消息对应一条记录，但一条带工具调用的助手回复会把文本和工具调用打包成一个 content\_blocks 列表。而工具结果那边，每个工具结果单独一条记录。这种不对称的设计有原因：Anthropic API 要求同一个 assistant 消息里的文本和工具调用必须打包在一起，但工具结果可以分开。

### Record 到 Message 的反序列化

恢复会话时要走反方向。这个函数比序列化复杂得多，因为它要处理「多条工具结果合并成一条消息」的逻辑：

```Python
def records_to_messages(records):
    messages = []
    pending_tool_results = []

    for record in records:
        if record.type == RecordType.TOOL_RESULT:
            pending_tool_results.append(ToolResultBlock(...))
            continue
        if pending_tool_results:
            messages.append(Message(
                role="user", content="",
                tool_results=pending_tool_results,
            ))
            pending_tool_results = []
```

关键在 `pending_tool_results` 这个缓冲区。工具结果在 JSONL 里是一条一条的，但恢复时要攒起来，等遇到下一条非工具结果记录时才一起打包成一条消息。循环结束后还要检查缓冲区里是否有残留的工具结果。

`COMPRESSION` 类型记录的处理也值得注意：它被转成一条 `[摘要]` 前缀的用户消息，而不是系统消息。这样 LLM 会把压缩摘要当作对话历史的一部分来理解，而不是指令。

### 消息链校验

JSONL 文件可能因为进程崩溃而中途截断。如果 assistant 发出了工具调用但还没收到结果，直接把这段不完整的对话喂给 LLM 会出问题。所以需要一个校验器：

```Python
def validate_message_chain(records) -> int:
    last_valid = 0
    pending_tool_uses: set[str] = set()

    for i, record in enumerate(records):
        if record.type == RecordType.ASSISTANT:
            # 从 content_blocks 里提取所有 tool_use id
            for block in record.content:
                if block.get("type") == "tool_use":
                    pending_tool_uses.add(block["id"])
        if record.type == RecordType.TOOL_RESULT:
            pending_tool_uses.discard(record.tool_use_id)
        if not pending_tool_uses:
            last_valid = i + 1
    return last_valid
```

思路很巧妙：维护一个「还没收到结果的工具调用」集合。遍历所有记录，遇到工具调用就加入集合，遇到工具结果就从集合移除。每当集合为空，说明当前位置之前的消息链是完整的。最终返回最后一个完整位置的索引，恢复时只取这个索引之前的记录。

### Session：活跃会话句柄

`Session` 持有一个打开的文件句柄，每次 `append()` 直接写入：

```Python
class Session:
    def append(self, message: Message) -> None:
        records = SessionRecord.from_message(message)
        for record in records:
            self._file.write(record.to_jsonl() + "\n")
        self._file.flush()

        self.meta.message_count += 1
        self.meta.last_active = datetime.now(timezone.utc)
        if not self.meta.title and message.role == "user":
            self.meta.title = message.content[:TITLE_MAX_LENGTH]
        self.meta.save(self._sessions_dir / f"{self.session_id}.meta")
```

每次写入后立即 `flush()` ，确保即使进程崩溃也不会丢数据。标题的自动生成也在这里：如果还没有标题，就用用户第一条消息的前 50 个字符。简单粗暴，但实际效果不错，因为用户的第一句话通常就是任务描述。

### SessionManager：会话的增删查

```Python
class SessionManager:
    def __init__(self, work_dir: str) -> None:
        self._sessions_dir = Path(work_dir) / SESSIONS_DIR
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> Session:
        session_id = _generate_session_id()
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta = SessionMeta(id=session_id)
        meta.save(self._sessions_dir / f"{session_id}.meta")
        file = open(jsonl_path, "a", encoding="utf-8")
        return Session(session_id=session_id, file=file,
                       meta=meta, sessions_dir=self._sessions_dir)
```

会话 ID 的格式是 `session_YYYYMMDD_HHMMSS_xxxx` ，后缀是 4 位随机字符。这既保证了按时间排序的能力，又避免了同一秒内创建多个会话时的冲突。

恢复会话时，先读 JSONL 文件，校验消息链完整性，再转成 `Message` 列表：

```Python
def resume(self, session_id: str) -> ResumeResult | None:
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = SessionRecord.from_jsonl(line)
            if record is not None:
                records.append(record)
    valid_count = validate_message_chain(records)
    records = records[:valid_count]
    messages = records_to_messages(records)
```

注意 `validate_message_chain` 的结果直接截断了 records 列表。如果 JSONL 文件末尾有不完整的工具调用，这些记录会被静默丢弃，不会影响恢复。

### 时间间隔提示

恢复会话时，如果距离上次活跃超过 24 小时，会插入一条提示消息：

```Python
def build_time_gap_message(last_active):
    gap = datetime.now(timezone.utc) - last_active
    if gap < TIME_GAP_THRESHOLD:
        return None
    hours = int(gap.total_seconds() // 3600)
    gap_text = f"{hours // 24} 天" if hours >= 48 else f"{hours} 小时"
    return Message(
        role="user",
        content=f"[系统提示] 距离上次会话已过去 {gap_text}。"
                "期间代码可能有变更，建议在操作前重新读取相关文件。",
    )
```

这条消息提醒 LLM 代码可能已经变了，不要依赖旧的记忆直接修改文件。24 小时的阈值和「重新读取相关文件」的建议，都是从实际使用中得出的经验。

### 过期清理

```Python
def cleanup(self, max_age_days=DEFAULT_MAX_AGE_DAYS):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    removed = 0
    for meta_path in self._sessions_dir.glob("*.meta"):
        meta = SessionMeta.load(meta_path)
        if meta and meta.last_active < cutoff:
            self.delete(meta.id)
            removed += 1
    return removed
```

默认清理 30 天前的会话。遍历所有 `.meta` 文件而不是 JSONL 文件，因为 meta 文件小，解析快，里面就有 `last_active` 时间戳。

## 第三层记忆：自动记忆提取

自动记忆是最有意思的一层。它让 LLM 从对话中提取值得长期保留的信息，写入 `memories.md` 文件。

### MemoryManager 的构造

```Python
class MemoryManager:
    def __init__(self, project_root: str) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = (
            Path(project_root) / PROJECT_MEMORIES_RELPATH
        )
        self._last_extraction_msg_count = 0
```

两个路径，两级记忆。用户级存在 `~/.mewcode/memories.md` ，项目级存在 `<project>/.mewcode/memories.md` 。 `_last_extraction_msg_count` 记录上次提取到了第几条消息，下次提取时只处理新消息。

### 加载记忆

```Python
def load(self) -> str:
    sections = []
    if self._user_path.exists():
        content = self._user_path.read_text(encoding="utf-8").strip()
        if content:
            sections.append(content)
    if self._project_path.exists():
        content = self._project_path.read_text(encoding="utf-8").strip()
        if content:
            sections.append(content)
    return "\n\n".join(sections)
```

加载时把两个文件拼在一起。用户级和项目级的记忆在 LLM 看来就是一整段文本，不需要区分来源。

### LLM 驱动的提取

提取过程是整个模块的核心。每隔一定轮次，把最近的对话交给 LLM，让它判断哪些信息值得记住：

```Python
async def extract(self, client, conversation, protocol):
    current_memories = self.load()
    recent = conversation.history[self._last_extraction_msg_count:]
    if not recent:
        return
    conv_lines = []
    for msg in recent:
        if msg.role == "user" and msg.content:
            conv_lines.append(f"用户: {msg.content}")
        elif msg.role == "assistant" and msg.content:
            conv_lines.append(f"助手: {msg.content}")
```

首先读取当前已有的记忆，然后只取上次提取之后的新消息。工具调用和工具结果被过滤掉了，只保留用户和助手的纯文本对话。这不只是为了节省 token，更因为工具调用的细节对长期记忆没什么价值，重要的是对话中暴露出的偏好和决策。

提取用的 prompt 非常具体，规定了四个分类和严格的输出格式：

```Python
MEMORY_EXTRACTION_PROMPT = """\
你是一个记忆提取助手。分析下面的对话，提取值得长期记忆的信息。
分类规则：
- **用户偏好**：编码习惯和风格要求
- **纠正反馈**：用户明确指出的错误和正确做法
- **项目知识**：技术栈、目录结构、部署方式
- **参考资料**：外部链接和文档地址
规则：已有条目不重复添加，每条 `- ` 开头，输出完整内容。
"""
```

四个分类的设计暗含了一个存储决策：「用户偏好」和「纠正反馈」是跟人走的，存用户级；「项目知识」和「参考资料」是跟项目走的，存项目级。

### 分级写入

LLM 返回的内容需要按分类拆分到两个文件：

```Python
def _write_memories(self, content: str) -> None:
    user_sections, project_sections = [], []
    current_header, current_lines = "", []
    for line in content.split("\n"):
        if line.startswith("### "):
            if current_header:
                self._assign_section(
                    current_header, current_lines,
                    user_sections, project_sections)
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)
```

逐行解析 LLM 的输出，遇到 `###` 标题就切换当前分类。然后用 `_assign_section` 根据标题关键词决定写入哪个文件：

```Python
_USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
_PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}

@staticmethod
def _assign_section(header, lines, user_sections, project_sections):
    real_lines = [l for l in lines
                  if l.strip().startswith("- ")
                  and not MemoryManager._is_placeholder(l)]
    if not real_lines:
        return
    section_text = header + "\n" + "\n".join(real_lines)
    for keyword in _USER_LEVEL_HEADERS:
        if keyword in header:
            user_sections.append(section_text)
            return
```

这里有个防御性过滤： `_is_placeholder` 会过滤掉 `...` 、 `暂无` 、 `N/A` 这类占位符。LLM 有时候会在空分类下写这些占位文字，必须过滤掉，否则 `memories.md` 里会堆满无意义的内容。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 自定义指令 | 三级路径扫描 + `@include` 递归展开（深度限制 5） |
| 会话格式 | JSONL 逐行追加，每次写入 `flush()` |
| 消息链完整性 | 工具调用 ID 配对校验，截断不完整尾部 |
| 元数据分离 | `.jsonl` + `.meta` 双文件，列表页只读 meta |
| 时间间隔感知 | 恢复时注入 `[系统提示]` 消息，提醒 LLM 代码可能已变 |
| 自动记忆存储 | 用户级/项目级 `memories.md` ，按标题关键词分拣 |
| 提取频率 | 只处理上次提取后的新消息，避免重复提取 |
| 占位符过滤 | `_is_placeholder` 过滤 `...` 、 `暂无` 等 LLM 幻觉 |