# Python 源码解析：Agent Teams

## 模块概览

Agent Teams 的代码集中在 `mewcode/teams/` 目录下：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `manager.py` | 253 | 核心。TeamManager 类，团队和成员的全部生命周期管理 |
| `coordinator.py` | 243 | 协调者模式：系统提示词、工具白名单、模式匹配 |
| `models.py` | 123 | 数据类型定义：BackendType、TeammateInfo、AgentTeam |
| `registry.py` | 40  | AgentNameRegistry 单例，名字到 agent\_id 的映射 |
| `mailbox.py` | 122 | 基于文件的收件箱，读/写/消费/广播 |
| `shared_task.py` | 126 | 共享任务存储：CRUD + 依赖关系（blocks/blocked\_by） |
| `transcript.py` | 92  | 对话记录的序列化/反序列化，按 agent\_id 存储 |
| `backend_detect.py` | 51  | 后端自动检测：tmux → iTerm2 → in-process |
| `spawn_inprocess.py` | 56  | In-process 后端：asyncio.Task 包装 |
| `spawn_tmux.py` | 122 | Tmux 后端：split-window 启动，CLI 命令构建 |
| `spawn_iterm2.py` | 58  | iTerm2 后端：it2 CLI 分屏 |

这些文件可以分成三层来理解。第一层是数据和存储： `models.py` 、 `registry.py` 、 `mailbox.py` 、 `shared_task.py` 、 `transcript.py` 。第二层是执行后端： `backend_detect.py` 、 `spawn_inprocess.py` 、 `spawn_tmux.py` 、 `spawn_iterm2.py` 。第三层是编排逻辑： `manager.py` 、 `coordinator.py` 。Manager 调度后端启动队友，Coordinator 给 Lead 注入协调者身份。

## 核心类型

### BackendType 和 TeammateInfo

```Python
class BackendType(str, Enum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"
```

三种后端类型。继承 `str` 是为了 JSON 序列化时直接输出字符串值，不需要额外的 `.value` 调用。

```Python
@dataclass
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str
    model: str
    worktree_path: str
    backend_type: str
    is_active: bool | None = None
```

`TeammateInfo` 记录一个队友的完整信息。 `is_active` 用三态表示： `True` 正在工作， `False` 已空闲， `None` 刚注册还没开始。注意 `backend_type` 是 `str` 而不是 `BackendType` 枚举，这是为了序列化方便，从 JSON 反序列化时不需要额外转换。

### AgentTeam

```Python
@dataclass
class AgentTeam:
    name: str
    lead_agent_id: str
    members: list[TeammateInfo] = field(default_factory=list)
    config_path: str = ""
    description: str = ""
```

`AgentTeam` 代表一个团队。 `lead_agent_id` 是领导者（通常是主 Agent）， `members` 是队友列表。整个数据结构可以序列化为 JSON 存到磁盘上：

```Python
def save(self) -> None:
    path = Path(self.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

@classmethod
def load(cls, config_path: str) -> AgentTeam:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    team = cls.from_dict(data)
    team.config_path = config_path
    return team
```

`save` 和 `load` 成对出现，让 Team 的状态可以跨进程共享。Tmux 后端启动的队友进程可以通过 `load` 读取 Team 配置，知道自己属于哪个团队、Lead 是谁。

团队成员管理有个有意思的设计：

```Python
def all_idle(self) -> bool:
    return all(m.is_active is False for m in self.members)

def active_members(self) -> list[TeammateInfo]:
    return [m for m in self.members if m.is_active is not False]
```

`all_idle` 检查是否所有成员都明确标记为不活跃。 `active_members` 返回所有没有明确标记为不活跃的成员，包括 `is_active` 为 `None` 的。这两个方法配合使用，让 Lead 能判断是否可以收工。

### 路径和命名

```Python
def resolve_team_dir(team_name: str) -> Path:
    slug = _sanitize_name(team_name)
    return Path.home() / ".mewcode" / "teams" / slug

def unique_team_name(team_name: str) -> str:
    slug = _sanitize_name(team_name)
    base_dir = Path.home() / ".mewcode" / "teams"
    if not (base_dir / slug).exists():
        return slug
    counter = 2
    while (base_dir / f"{slug}-{counter}").exists():
        counter += 1
    return f"{slug}-{counter}"
```

团队目录放在用户主目录下（ `~/.mewcode/teams/` ），而不是项目目录下。这是因为一个团队可能跨多个项目工作。 `unique_team_name` 自动处理名称冲突，加数字后缀（ `my-team` 、 `my-team-2` 、 `my-team-3` ）。

## 主流程走读

### 创建团队

```Python
def create_team(
    self,
    name: str,
    lead_agent_id: str,
    description: str = "",
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> AgentTeam:
    backend = self.detect_backend(teammate_mode, is_interactive)
    slug = unique_team_name(name)
    team_dir = resolve_team_dir(slug)
    team_dir.mkdir(parents=True, exist_ok=True)
```

创建团队时先检测后端，再生成唯一名称，然后创建目录。接下来初始化三个子系统：

```Python
    team = AgentTeam(
        name=slug,
        lead_agent_id=lead_agent_id,
        config_path=str(team_dir / "config.json"),
        description=description,
    )
    team.save()

    task_store = SharedTaskStore(team_dir / "tasks.json")
    task_store.init_empty()

    mailbox_dir = team_dir / "mailbox"
    mailbox_dir.mkdir(parents=True, exist_ok=True)
    mailbox = Mailbox(mailbox_dir)
```

Team 配置、任务存储、邮箱三个组件分别初始化。每个都落盘到团队目录下，形成一个自包含的工作区： `config.json` 是团队元数据， `tasks.json` 是共享任务板， `mailbox/` 是通信目录。

### 注册成员

```Python
def register_member(
    self,
    team_name: str,
    member: TeammateInfo,
) -> None:
    team = self.get_team(team_name)
    if team is None:
        raise TeamError(f"Team '{team_name}' not found")
    team.add_member(member)
    team.save()

    AgentNameRegistry.instance().register(member.name, member.agent_id)
    self._teammate_team_map[member.agent_id] = team_name
```

注册成员做三件事：把成员加入 Team 的成员列表并持久化，在全局名称注册表里登记名字到 agent\_id 的映射，在内存里记录 agent\_id 到 team\_name 的反向映射。

`AgentNameRegistry` 是个单例，用双重检查锁实现线程安全：

```Python
class AgentNameRegistry:
    _instance: AgentNameRegistry | None = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> AgentNameRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None: cls._instance = cls()
        return cls._instance

    def resolve(self, name_or_id: str) -> str | None:
        if name_or_id in self._names: return self._names[name_or_id]
        if name_or_id in self._names.values(): return name_or_id
        return None
```

`resolve` 方法接受名字或 agent\_id 两种输入，让 SendMessage 工具不需要关心用户传的是哪种标识符。如果传的是名字就查映射表，如果本身就是 agent\_id（能在 values 里找到）就直接返回。

### 成员空闲通知

```Python
def set_member_idle(self, team_name: str, member_name: str) -> None:
    team = self.get_team(team_name)
    if team is None:
        return
    team.set_member_active(member_name, False)
    team.save()
    mailbox = self.get_mailbox(team_name)
    if mailbox:
        msg = create_message(
            from_agent=member_name, to_agent=team.lead_agent_id,
            content=f"Teammate '{member_name}' is now idle ...",
            summary=f"{member_name} idle",
        )
        mailbox.write(team.lead_agent_id, msg)
```

队友完成任务后，Manager 做两件事：把成员状态改为不活跃，往 Lead 的邮箱里写一条空闲通知。Lead 的 Agent 在下一轮迭代开头会读到这条消息，然后决定是派新任务还是收工。

这个通知机制是推模型。队友主动推送状态给 Lead，而不是 Lead 轮询每个队友的状态。推模型的好处是 Lead 不需要浪费 LLM 调用去检查状态，坏处是如果队友进程崩溃了，Lead 永远收不到空闲通知。这种情况需要靠超时机制兜底。

### 删除团队

```Python
def delete_team(self, team_name: str) -> None:
    active = [m for m in team.members if m.is_active is not False]
    if active:
        raise TeamError(f"Cannot delete team: active members: {names}")

    for member in list(team.members):
        AgentNameRegistry.instance().unregister(member.name)
        handle = self._inprocess_handles.pop(member.agent_id, None)
        if handle and not handle.done: handle.cancel()
        pane_id = self._pane_ids.pop(member.agent_id, None)
        if pane_id: self._kill_pane(pane_id, member.backend_type)
        if member.worktree_path:
            self._cleanup_worktree(member.worktree_path)
```

删除团队之前先检查有没有活跃成员，有就拒绝。然后逐个清理每个成员：从名称注册表注销，取消 in-process 的 asyncio task，关闭 tmux 的 pane，清理 worktree。最后删除邮箱和团队目录。

注意 `list(team.members)` 做了拷贝，因为循环体里可能修改 `team.members` 。这是 Python 里遍历时修改列表的标准防御写法。

## 三种执行后端

### detect\_backend：自动选择

```Python
def detect_backend(teammate_mode: str = "",
                   is_interactive: bool = True) -> BackendType:
    if teammate_mode == "in-process" or not is_interactive:
        return BackendType.IN_PROCESS
    if _in_tmux_session():
        return BackendType.TMUX
    if _in_iterm2() and _it2_available():
        return BackendType.ITERM2
    if _tmux_installed():
        return BackendType.TMUX
    raise BackendDetectionError(
        "No suitable terminal backend found ..."
    )
```

检测逻辑有明确的优先级。如果用户指定了 in-process 或者不是交互模式（比如 CI 环境），直接用 in-process。否则按环境变量检测：在 tmux 里就用 tmux，在 iTerm2 里就用 iTerm2（还要检查 `it2` CLI 工具是否可用），tmux 装了但不在里面也用 tmux。

Python 版在所有外部后端都不可用时会抛异常，不会静默退化。这个选择更严格，强制要求用户明确选择 in-process 模式，避免意外行为。

检测函数都很短，各自检查一个环境条件：

```Python
def _in_tmux_session() -> bool:
    return bool(os.environ.get("TMUX"))

def _in_iterm2() -> bool:
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"

def _tmux_installed() -> bool:
    return shutil.which("tmux") is not None
```

`shutil.which` 等价于 shell 的 `which` 命令，检查可执行文件是否在 PATH 里。

### In-process：asyncio.Task 方案

```Python
class InProcessTeammateHandle:
    def __init__(self, agent: Agent,
                 task: asyncio.Task[str], name: str) -> None:
        self.agent = agent
        self.task = task
        self.name = name

    @property
    def done(self) -> bool:
        return self.task.done()

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()
```

In-process 后端把队友的执行包装成一个 `asyncio.Task` 。 `InProcessTeammateHandle` 提供三个能力：检查是否完成（ `done` ）、获取结果（ `result` ）、取消执行（ `cancel` ）。

启动函数很简洁：

```Python
def spawn_inprocess_teammate(
    agent: Agent,
    prompt: str,
    name: str,
    conversation: ConversationManager | None = None,
) -> InProcessTeammateHandle:
    async def _run() -> str:
        if conversation is not None:
            return await agent.run_to_completion("", conversation)
        return await agent.run_to_completion(prompt)

    task = asyncio.create_task(_run(), name=f"teammate-{name}")
    return InProcessTeammateHandle(agent=agent, task=task, name=name)
```

如果传了 `conversation` ，就用已有的对话历史继续执行（用于 SendMessage 继续已有队友）。否则用 prompt 从头开始。 `asyncio.create_task` 创建一个并发任务，name 参数方便调试时在 task 列表里找到它。

asyncio.Task 在取消时有很好的语义支持。 `task.cancel()` 会在下一个 `await` 点抛出 `CancelledError` ，Agent 的主循环可以捕获它并清理资源。取消信号自动沿着 await 链传播，不需要在每个可能阻塞的地方主动检查。

### Tmux：split-window 方案

```Python
def spawn_tmux_teammate(team_name, teammate_name, worktree_path,
                        prompt, ...) -> TmuxPaneInfo:
    window_name = f"{team_name}-{teammate_name}"
    try:
        pane_id = _run_tmux("split-window", "-h", "-P",
                            "-F", "#{pane_id}", "-t", team_name)
    except TmuxSpawnError:
        try:  # 降级：新建 window 再 split
            _run_tmux("new-window", "-t", team_name, "-n", window_name, ...)
            pane_id = _run_tmux("split-window", "-h", "-P", "-F",
                                "#{pane_id}", "-t", f"{team_name}:{window_name}")
        except TmuxSpawnError:  # 再降级：新建整个 session
            _run_tmux("new-session", "-d", "-s", team_name, "-n", window_name)
            pane_id = _run_tmux("list-panes", "-t",
                                f"{team_name}:{window_name}", ...).split("\n")[0]
```

这段代码用了三级降级策略。先尝试在已有的 tmux session 里 split window，失败了就新建 window 再 split，再失败就新建整个 session。三级降级保证了无论当前 tmux 状态如何，都能找到地方放新队友。

`-P -F "#{pane_id}"` 让 tmux 创建窗格后打印其 ID，这个 ID 后续用来发送按键和关闭窗格。

CLI 命令构建：

```Python
def build_cli_command(team_name, teammate_name,
                      worktree_path, prompt, ...) -> str:
    parts = ["mewcode", "-p", "--work-dir", worktree_path]
    if agent_type: parts.extend(["--agent-type", agent_type])
    if model: parts.extend(["--model", model])
    env_parts = [
        f"MEWCODE_TEAM_NAME={team_name}",
        f"MEWCODE_TEAMMATE_NAME={teammate_name}",
    ]
    full_prompt = prompt.replace("'", "'\\''")
    return f"{' '.join(env_parts)} {' '.join(parts)} '{full_prompt}'"
```

构建的命令形如 `MEWCODE_TEAM_NAME=xxx MEWCODE_TEAMMATE_NAME=yyy mewcode -p --work-dir /path 'do something'` 。通过环境变量传递团队信息，通过 CLI 参数传递工作目录和提示词。 `prompt.replace("'", "'\\''")` 处理单引号转义，防止 prompt 里的单引号破坏 shell 解析。

构建完命令后用 send-keys 发送到 tmux 窗格：

```Python
    _run_tmux("send-keys", "-t", pane_id, cli_cmd, "Enter")
```

### iTerm2：it2 CLI 方案

```Python
def spawn_iterm2_teammate(team_name, teammate_name,
                          worktree_path, prompt, ...) -> ITermPaneInfo:
    from mewcode.teams.spawn_tmux import build_cli_command

    cli_cmd = build_cli_command(
        team_name=team_name, teammate_name=teammate_name,
        worktree_path=worktree_path, prompt=prompt, ...)

    session_id = _run_it2(
        "split-pane", "--command", f"/bin/zsh -c '{cli_cmd}'"
    )
```

iTerm2 后端复用了 tmux 后端的 `build_cli_command` 函数来构建命令，然后用 `it2 split-pane` 在 iTerm2 里创建新窗格。用 `it2` CLI 工具而不是 AppleScript 来操控 iTerm2，代码更简洁，跨平台兼容性也更好。

## 通信协调

### Mailbox：基于文件的消息传递

```Python
@dataclass
class MailboxMessage:
    id: str
    from_agent: str
    to_agent: str
    content: str
    summary: str = ""
    message_type: str = "text"
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
```

每条消息有唯一 ID（UUID 前 12 位）、发件人、收件人、内容、时间戳。 `message_type` 区分普通文本和系统消息（如 `shutdown_request` 、 `shutdown_response` ）。

```Python
class Mailbox:
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def write(self, agent_id: str, message: MailboxMessage) -> None:
        d = self._agent_dir(agent_id)
        d.mkdir(parents=True, exist_ok=True)
        filename = f"{message.timestamp:.6f}_{message.id}.json"
        (d / filename).write_text(
            json.dumps(message.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
```

Python 版的 Mailbox 采用「每条消息一个独立 JSON 文件」的设计，而不是追加到一个大 JSON 数组里。文件名用时间戳开头，这样 `sorted(d.iterdir())` 就是按时间排序的。

这个设计消除了并发写入的锁需求。两个进程同时写不同的文件，不会冲突。不需要文件锁来保护，操作系统层面的文件创建本身就是原子的。

`consume` 方法读取后删除，是「消费」语义：

```Python
def consume(self, agent_id: str) -> list[MailboxMessage]:
    d = self._agent_dir(agent_id)
    if not d.exists():
        return []
    messages: list[MailboxMessage] = []
    for f in sorted(d.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            messages.append(MailboxMessage.from_dict(data))
            f.unlink()
        except (json.JSONDecodeError, KeyError):
            continue
    return messages
```

逐个读取 JSON 文件，解析成功的立刻删除（ `f.unlink()` ）。解析失败的跳过不删，避免数据丢失。

`broadcast` 方法给所有成员发消息，可以排除发件人自己：

```Python
def broadcast(
    self,
    team_members: list[str],
    message: MailboxMessage,
    exclude: str = "",
) -> None:
    for agent_id in team_members:
        if agent_id == exclude:
            continue
        self.write(agent_id, message)
```

### SharedTaskStore：共享任务板

```Python
@dataclass
class SharedTask:
    id: str
    title: str
    description: str = ""
    status: str = "pending"
    assignee: str = ""
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    created_by: str = ""
```

共享任务板让团队成员之间的工作有了结构化的协调方式。 `blocks` 和 `blocked_by` 表达任务之间的依赖关系，一个任务可以阻塞另一个任务。

任务的更新支持增量操作：

```Python
def update(self, task_id: str, status: str | None = None,
           assignee: str | None = None,
           add_blocks: list[str] | None = None, ...) -> SharedTask | None:
    task = self._tasks.get(task_id)
    if task is None: return None
    if status is not None: task.status = status
    if assignee is not None: task.assignee = assignee
    if add_blocks:
        for bid in add_blocks:
            if bid not in task.blocks: task.blocks.append(bid)
    self._save()
    return task
```

每个字段都是 `None` 就跳过、有值才更新的模式，让调用方可以只更新需要改的字段。 `add_blocks` 和 `add_blocked_by` 是追加语义而不是覆盖，新的依赖加到已有列表里，重复的不会重复加。

### Transcript：对话记录持久化

```Python
def save_transcript(
    team_name: str,
    agent_id: str,
    conversation: ConversationManager,
) -> Path:
    transcript_dir = resolve_team_dir(team_name) / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{agent_id}.json"
    data = _serialize_conversation(conversation)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
```

每个队友的对话记录按 agent\_id 存储在团队目录下的 `transcripts/` 子目录。序列化时保留完整的对话结构，包括 tool\_uses 和 tool\_results：

```Python
def _serialize_conversation(conv: ConversationManager) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for msg in conv.history:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_uses:
            entry["tool_uses"] = [
                {
                    "tool_use_id": tu.tool_use_id,
                    "tool_name": tu.tool_name,
                    "arguments": tu.arguments,
                }
                for tu in msg.tool_uses
            ]
        messages.append(entry)
    return messages
```

反序列化时重建完整的 ConversationManager 对象，并标记 `env_injected` 和 `ltm_injected` 为 True，避免重复注入系统环境和长期记忆。

## 协调者模式

`coordinator.py` 是整个模块最长的文件（243 行），大部分是系统提示词。

### 模式切换

```Python
def is_coordinator_mode(enable_flag: bool = False) -> bool:
    if not enable_flag: return False
    val = os.environ.get("MEWCODE_COORDINATOR_MODE", "").lower()
    return val in ("1", "true", "yes")

def match_session_mode(session_mode: str | None,
                       enable_flag: bool = False) -> str | None:
    current = is_coordinator_mode(enable_flag)
    session_is_coordinator = session_mode == "coordinator"
    if current == session_is_coordinator: return None
    if session_is_coordinator:
        os.environ["MEWCODE_COORDINATOR_MODE"] = "1"
    else:
        os.environ.pop("MEWCODE_COORDINATOR_MODE", None)
```

协调者模式通过环境变量 `MEWCODE_COORDINATOR_MODE` 控制。 `match_session_mode` 在恢复会话时调整当前模式以匹配之前的状态，确保重启后行为一致。

### 系统提示词

协调者的系统提示词定义了 Lead 的完整行为规范。几个关键点：

Lead 的四阶段工作流（Research → Synthesis → Implementation → Verification）直接写在提示词里。其中最重要的一条规则是验证必须由独立的 worker 执行：

提示词里还包含了反模式示例，教 Lead 如何写好 worker 的 prompt。「lazy delegation」是头号反模式，Lead 必须把研究结果消化后再给出具体的、包含文件路径和行号的指令。

工具白名单通过 `get_coordinator_user_context` 注入：

```Python
def get_coordinator_user_context(
    worker_tools: list[str] | None = None,
) -> dict[str, str]:
    if worker_tools is None:
        from mewcode.agents.tool_filter import IN_PROCESS_TEAMMATE_ALLOWED_TOOLS
        tools_str = ", ".join(sorted(IN_PROCESS_TEAMMATE_ALLOWED_TOOLS))
    else:
        tools_str = ", ".join(sorted(worker_tools))

    return {
        "workerToolsContext": f"Workers spawned via the Agent tool have access to these tools: {tools_str}",
    }
```

告诉 Lead 每个 worker 能用哪些工具，这样 Lead 就不会给 worker 分配超出能力范围的任务。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 后端自动选择 | `detect_backend` 按环境变量和 PATH 优先级选择，不可用时抛异常 |
| In-process 执行 | `asyncio.create_task` 包装 Agent 执行 |
| 外部进程执行 | Tmux `split-window` 三级降级 / iTerm2 `it2 split-pane` |
| 跨进程通信 | Mailbox 每消息一文件，无需文件锁 |
| 成员注册 | `AgentNameRegistry` 单例，支持按名字或 agent\_id 查找 |
| 空闲通知 | 推模型：队友完成后主动写 mailbox 通知 Lead |
| 任务协调 | `SharedTaskStore` 带依赖关系的共享任务板 |
| 对话持久化 | `transcript.py` 按 agent\_id 序列化完整对话历史 |
| 协调者模式 | 环境变量驱动 + 系统提示词注入 + 工具白名单 |
| 团队清理 | 按后端分别处理：cancel task / kill pane / 清理 worktree |
| 命名冲突 | `unique_team_name` 自动加数字后缀 |