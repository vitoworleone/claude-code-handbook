# Python 源码解析：SubAgent 与任务分发

## 模块概览

SubAgent 系统的代码分散在两个目录下：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `agents/parser.py` | 119 | Agent 定义文件解析。YAML frontmatter + Markdown body |
| `agents/loader.py` | 137 | Agent 定义加载。三级优先级扫描 + 热重载 |
| `agents/task_manager.py` | 166 | 后台任务管理。启动、取消、轮询、通知 |
| `agents/tool_filter.py` | 170 | 工具过滤。四层过滤规则 + 团队模式工具构建 |
| `agents/fork.py` | 80  | Fork 会话构建。深拷贝对话历史 + 防嵌套 |
| `tools/agent_tool.py` | 637 | 入口。AgentTool 的 execute 方法，三种执行路径的分发枢纽 |

模块之间的调用关系很清晰： `agent_tool.py` 是入口，调用 `loader` 获取定义、调用 `fork` 构建会话、调用 `tool_filter` 过滤工具、调用 `task_manager` 管理后台任务。 `parser` 被 `loader` 调用，负责最底层的文件解析。

## 核心类型

### AgentDef：Agent 的蓝图

```Python
@dataclass
class AgentDef:
    agent_type: str
    when_to_use: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 50
    permission_mode: str = "default"
    background: bool = False
    isolation: str = ""
    file_path: Path | None = None
    source: str = "builtin"
```

AgentDef 是一个 Agent 的完整「蓝图」，定义了它能做什么、不能做什么、用什么模型、最多跑多少轮。

`tools` 和 `disallowed_tools` 是白名单和黑名单，两者可以同时存在，黑名单优先级更高。 `model` 默认是 `inherit` ，表示继承父 Agent 的模型。 `permission_mode` 控制权限行为： `default` 走正常的权限检查， `acceptEdits` 自动接受编辑操作， `dontAsk` 跳过所有权限询问（Fork 模式默认用这个）。

`source` 字段标记了定义来源： `builtin` 、 `user` 、 `project` 、 `plugin` 。这个信息在工具过滤时会用到，来自用户和项目的自定义 Agent 会受到额外的限制（不能进入 Plan Mode）。

### BackgroundTask：后台任务的状态容器

```Python
@dataclass
class BackgroundTask:
    id: str
    name: str
    agent: Agent
    task: str
    status: str = "running"
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)
```

每个后台任务封装了执行它的 Agent 实例、任务描述、状态和结果。 `cancel` 字段存的是 `asyncio.Task.cancel` 方法的引用，调用它就能取消整个 Agent 的执行。

`start_time` 用 `time.monotonic` （单调时钟）而不是 `time.time` （墙上时钟），因为单调时钟不受系统时间调整的影响，用来计算经过时间更准确。

## 两种创建模式

### 定义式创建

定义式创建从 AgentDef 出发，创建一个全新的 Agent 实例。 `AgentTool.execute` 里的核心路径：

```Python
if p.subagent_type:
    definition = self._agent_loader.get(p.subagent_type)
    if definition is None:
        return ToolResult(
            output=f"Unknown agent type: '{p.subagent_type}'. "
            f"Available types: {', '.join(
                t for t, _ in self._agent_loader.list_agents()
            )}",
            is_error=True,
        )
    conversation = ConversationManager()
```

指定了 `subagent_type` 就走定义式。从 loader 获取定义，创建一个空白的 ConversationManager。如果找不到指定类型，错误信息里会列出所有可用的类型，帮助 LLM 自我纠正。

### Fork 式创建

Fork 不需要 AgentDef，它从父 Agent 的对话历史「分叉」出去：

```Python
else:
    if not self._enable_fork:
        return ToolResult(output="Fork mode is not enabled.",
                          is_error=True)
    parent_conv = getattr(self._parent_agent,
                          '_current_conversation', None)
    if parent_conv is None:
        return ToolResult(output="Cannot fork: no active conversation.",
                          is_error=True)
    try:
        conversation = build_forked_messages(parent_conv, p.prompt)
    except ForkError as e:
        return ToolResult(output=str(e), is_error=True)
```

Fork 有两个前置检查：全局开关 `_enable_fork` 是否启用，父 Agent 是否有活跃的对话。都通过后才调用 `build_forked_messages` 构建分叉的对话。

Fork 时会临时构造一个 AgentDef，把权限模式设为 `dontAsk` ：

```Python
definition = AgentDef(
    agent_type="fork",
    when_to_use="Forked from parent agent",
    system_prompt="",
    disallowed_tools=[],
    model="inherit",
    max_turns=self._parent_agent.max_iterations,
    permission_mode="dontAsk",
    source="builtin",
)
```

`dontAsk` 是因为 Fork 出来的 Agent 通常跑在后台，没有 UI 可以弹权限确认框。

### Fork 会话的构建细节

`fork.py` 里的 `build_forked_messages` 做了几件精细的事情：

```Python
FORK_BOILERPLATE = f"""{FORK_BOILERPLATE_TAG}
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告控制在 500 字以内"""
```

先注入一段「你是 Fork 进程」的声明，用硬规则约束它的行为。不能再 Fork、不能提问、报告要简短。这些限制是从实践中总结出来的：不加限制的话，Fork 出来的 Agent 会尝试再 Fork（无限套娃），或者向不存在的用户提问（它跑在后台没有 UI）。

防嵌套的检测：

```Python
def build_forked_messages(
    conversation: ConversationManager, task: str
) -> ConversationManager:
    for msg in conversation.history:
        if FORK_BOILERPLATE_TAG in msg.content:
            raise ForkError(
                "Cannot fork from a forked agent. "
                "Fork nesting is not allowed."
            )
```

遍历整个对话历史，如果发现 `<fork_boilerplate>` 标签就拒绝 Fork。这个标签既是给 LLM 看的行为约束，也是给代码用的嵌套检测标记，一石二鸟。

处理未完成的工具调用：

```Python
if fork_conv.history:
    last = fork_conv.history[-1]
    if last.role == "assistant" and last.tool_uses:
        pending = [tu for tu in last.tool_uses
                    if tu.tool_use_id not in existing_result_ids]
        if pending:
            placeholders = [
                ToolResultBlock(tool_use_id=tu.tool_use_id,
                                content="interrupted", is_error=False)
                for tu in pending
            ]
            fork_conv.history.append(
                Message(role="user", content="", tool_results=placeholders))
```

Fork 发生的时候，父 Agent 可能正在执行工具调用。对话历史里最后一条消息是 assistant 发的工具调用请求，但还没有对应的结果。这种「半完成」的状态如果直接拷贝到 Fork 的对话里，LLM API 会报错（因为工具调用必须有对应的结果）。所以这里用 `"interrupted"` 占位符补全缺失的结果。

## Agent 定义的加载

### 三级优先级

AgentLoader 按优先级从高到低扫描三个位置：

```Python
def load_all(self) -> dict[str, AgentDef]:
    seen: dict[str, AgentDef] = {}
    # Priority 1: project-level (highest)
    for ad in self._scan_directory(project_path, "project"):
        if ad.agent_type not in seen: seen[ad.agent_type] = ad
    # Priority 2: user-level
    for ad in self._scan_directory(user_path, "user"):
        if ad.agent_type not in seen: seen[ad.agent_type] = ad
    # Priority 3: built-in
    for ad in self._load_builtins():
        if ad.agent_type not in seen: seen[ad.agent_type] = ad
```

项目级最优先。用户可以在 `.mewcode/agents/` 下放自己的 Agent 定义文件，覆盖内置的同名定义。 `if agent_def.agent_type not in seen` 这个判断实现了「先到先得」的覆盖策略。

### 热重载

```Python
def get(self, agent_type: str) -> AgentDef | None:
    cached = self._agents.get(agent_type)
    if cached is None:
        return None
    if cached.file_path is not None and cached.file_path.exists():
        try:
            reloaded = parse_agent_file(cached.file_path)
            reloaded.source = cached.source
            self._agents[agent_type] = reloaded
            return reloaded
        except AgentParseError:
            pass  # 回退到缓存版本
    return cached
```

每次 `get` 都会尝试从文件重新加载。如果文件被修改了，下次创建 SubAgent 就会用新的定义，不需要重启。如果重新加载失败（比如文件被改坏了），回退到缓存版本并打 warning。

这个热重载的粒度是「每次创建 SubAgent 时」，不是文件 watcher 那种实时监听。开发阶段够用了：改完定义文件，下次让 LLM 调 Agent 工具就生效。

### 定义文件的解析

Agent 定义用 YAML frontmatter + Markdown body 的格式，和 Jekyll 博客文章一样：

```Python
def parse_frontmatter(raw: str) -> tuple[dict, str]:
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise AgentParseError("Missing YAML frontmatter")
    end = stripped.find("---", 3)
    if end == -1:
        raise AgentParseError("Unclosed YAML frontmatter")
    yaml_block = stripped[3:end]
    body = stripped[end + 3:].lstrip("\n")
    meta = yaml.safe_load(yaml_block)
    if not isinstance(meta, dict):
        raise AgentParseError("Frontmatter must be a YAML mapping")
    return meta, body
```

frontmatter 里放结构化的元数据（name、description、tools、model 等），body 部分就是 system prompt。这样一个文件同时承载了配置和提示词。

校验逻辑检查模型名、权限模式、maxTurns 的合法性：

```Python
VALID_MODELS = {"inherit", "sonnet", "opus", "haiku", ""}
VALID_PERMISSION_MODES = {"default", "acceptEdits", "dontAsk", ""}

def _validate_agent_meta(meta: dict, source: str = ""):
    if "name" not in meta:
        raise AgentParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise AgentParseError(f"Missing required field 'description'{ctx}")

    model = str(meta.get("model", "inherit"))
    if model not in VALID_MODELS:
        raise AgentParseError(f"Invalid model '{model}'{ctx}")
```

## 工具过滤：五层模型

工具过滤是 SubAgent 安全性的核心。 `tool_filter.py` 实现了五层递进的过滤规则，MCP 工具在过滤之前先被分离出来，最后直接加回结果中。

### Layer 0：MCP 工具直通

```Python
mcp_tools = {name: tool for name, tool in all_tools.items()
              if _is_mcp_tool(name)}
all_tools = {name: tool for name, tool in all_tools.items()
              if not _is_mcp_tool(name)}
```

以 `mcp__` 开头的工具先被分离出来，不参与后续任何层的过滤，最后直接加回结果 registry。MCP 工具通常有自己的权限控制，SubAgent 层面不需要重复过滤。

### 第一层：全局黑名单

```Python
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "TaskOutput",
    "ExitPlanMode",
    "EnterPlanMode",
    "Agent",
    "AskUserQuestion",
    "TaskStop",
    "Workflow",
})
```

这 7 个工具对任何 SubAgent 都不可用。 `Agent` 被禁是为了防止无限嵌套（SubAgent 再创建 SubAgent）。 `AskUserQuestion` 被禁是因为 SubAgent 没有直接面向用户的 UI。 `ExitPlanMode` 、 `EnterPlanMode` 防止子 Agent 切换模式影响全局状态。 `TaskOutput` 、 `TaskStop` 、 `Workflow` 是协调类工具，只在团队模式下按需开放。

### 第二层：自定义 Agent 额外限制

```Python
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "TaskOutput",
    "ExitPlanMode",
    "EnterPlanMode",
    "Agent",
    "AskUserQuestion",
    "TaskStop",
    "Workflow",
})

# 在 resolve_agent_tools 中
if definition.source in ("project", "user", "plugin"):
    for name in CUSTOM_AGENT_DISALLOWED_TOOLS:
        all_tools.pop(name, None)
```

`CUSTOM_AGENT_DISALLOWED_TOOLS` 和 `ALL_AGENT_DISALLOWED_TOOLS` 内容相同（7 个工具），作为独立的集合是为了将来可以给自定义 Agent 加额外限制而不影响全局列表。来自用户自定义（项目级、用户级、插件）的 Agent 受到和内置 Agent 相同的限制。

### 第三层：后台模式白名单

```Python
ASYNC_AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "ReadFile", "WebSearch", "TodoWrite", "Grep",
    "WebFetch", "Glob", "Bash", "EditFile",
    "WriteFile", "NotebookEdit", "Skill", "LoadSkill",
    "SyntheticOutput", "ToolSearch",
    "EnterWorktree", "ExitWorktree",
})

if is_background:
    all_tools = {name: tool for name, tool in all_tools.items()
                  if name in ASYNC_AGENT_ALLOWED_TOOLS}
```

后台 Agent 只能用这 16 个工具。从白名单里可以看出哪些操作被认为是「后台安全」的：文件读写、搜索、Bash 命令、Skill 执行、Worktree 操作。而权限交互类（需要用户确认）、模式切换类（影响全局状态）的工具都被排除了。

### 第四层：定义级限制

```Python
if definition.disallowed_tools:
    for name in definition.disallowed_tools:
        all_tools.pop(name, None)

if definition.tools:
    allowed_set = set(definition.tools)
    all_tools = {
        name: tool
        for name, tool in all_tools.items()
        if name in allowed_set
    }
```

最后一层是 AgentDef 自己声明的限制。 `disallowed_tools` 黑名单先移除，然后 `tools` 白名单做交集过滤。如果定义里同时有黑名单和白名单，黑名单先生效，白名单再收窄。

整个过滤流程是逐层递进的：MCP 直通 → 全局黑名单(7) → 自定义额外限制(7) → 后台白名单(16) → 定义级限制。每一层只能进一步缩小工具集，不能扩大。MCP 工具在过滤结束后被加回结果中，保证了它们始终可用。这保证了 SubAgent 的权限永远不会超过父 Agent。

### 团队模式的工具构建

团队模式有一套独立的工具构建逻辑：

```Python
TEAMMATE_COORDINATION_TOOLS = frozenset({
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "SendMessage",
})

def build_teammate_tools(parent_registry, ..., backend_type, ...):
    if backend_type == BackendType.IN_PROCESS.value:
        all_tools = {t.name: t for t in parent_registry.list_tools()}
        filtered = {n: t for n, t in all_tools.items()
                     if n in IN_PROCESS_TEAMMATE_ALLOWED_TOOLS}
    else:
        filtered = {t.name: t for t in parent_registry.list_tools()}
        filtered.pop("TeamCreate", None)
        filtered.pop("TeamDelete", None)
```

In-process 模式的 teammate 用严格的白名单，pane 模式（tmux/iterm2）的 teammate 几乎拿到全部工具（只去掉 TeamCreate 和 TeamDelete）。差别在于 pane 模式的 teammate 是独立进程，有自己的文件系统隔离（worktree），风险更可控。

团队成员还会额外获得协调工具（TaskCreate、TaskGet 等），这些工具在普通 SubAgent 里是被全局黑名单禁止的，但团队模式下需要它们来协调工作。

## 执行路径：AgentTool.execute 的分发

`AgentTool.execute` 是整个 SubAgent 系统的入口。它根据参数决定走哪条执行路径：

```Python
async def execute(self, params: BaseModel) -> ToolResult:
    p: AgentToolParams = params
    if p.team_name:
        return await self._execute_as_teammate(p)
    # 检查是否需要 worktree 隔离
    if p.subagent_type:
        defn = self._agent_loader.get(p.subagent_type)
        if defn and defn.isolation == "worktree":
            return await self._execute_with_worktree(p)
    # ... 标准执行路径
```

三个分支，优先级从高到低：team\_name 指定了走团队模式，isolation 是 worktree 走隔离模式，否则走标准路径。

### 标准路径的 Agent 构建

无论走哪条路径，构建 SubAgent 的过程都是相似的：

```Python
# 1. 选择 LLM 客户端
client = self._select_llm(p, definition)
# 2. 过滤工具（优先用未缩减的完整 registry）
_base_registry = getattr(self._parent_agent, '_full_registry', None) \
                  or self._parent_agent.registry
filtered_registry = resolve_agent_tools(_base_registry, definition, is_background)
# 3. 创建权限检查器
checker = PermissionChecker(
    detector=DangerousCommandDetector(),
    sandbox=PathSandbox(self._parent_agent.work_dir),
    rule_engine=RuleEngine(), mode=pm_enum,
)
```

权限检查器的 sandbox 用父 Agent 的工作目录，确保 SubAgent 不能操作目录外的文件。然后把所有组件组装成 Agent 实例：

```Python
# 4. 创建 Agent 实例
sub_agent = AgentClass(
    client=client, registry=filtered_registry,
    protocol=self._parent_agent.protocol,
    work_dir=self._parent_agent.work_dir,
    max_iterations=definition.max_turns,
    permission_checker=checker,
    instructions_content=definition.system_prompt,
    hook_engine=self._parent_agent.hook_engine,
)
```

注意第 2 步获取基础 registry 的方式：优先用 `_full_registry` ，回退到 `registry` 。这是因为如果父 Agent 是协调器模式（Coordinator），它自己的 `registry` 可能已经被缩减过了（只保留了 Agent、TeamCreate 等少数工具）。SubAgent 应该从未缩减的完整工具集开始过滤，所以用 `_full_registry` 。

### 模型选择

```Python
def _select_llm(self, params, definition) -> LLMClient:
    model_override = params.model or (
        definition.model
        if definition.model != "inherit"
        else None
    )
    if model_override and model_override != "inherit":
        client = self._create_client_for_model(model_override)
        if client is not None:
            return client
    return self._parent_agent.client
```

参数级的模型覆盖优先于定义级的。如果都没指定或者是 `inherit` ，就用父 Agent 的 client。 `_create_client_for_model` 会根据别名查表：

```Python
model_map = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6-20250514",
    "opus": "claude-opus-4-6-20250514",
}
```

### 前台 vs 后台执行

```Python
is_background = p.run_in_background or definition.background
if self._enable_fork:
    is_background = True
```

后台模式有三个来源：用户显式指定、定义里标记、Fork 模式（Fork 总是后台的）。

后台执行：

```Python
if is_background:
    task_id = self._task_manager.launch(
        agent=sub_agent,
        task="" if is_fork else p.prompt,
        name=agent_name,
        fork_conversation=conversation if is_fork else None,
    )
    return ToolResult(
        output=f"Sub-agent launched in background.\n"
        f"Task ID: {task_id}\n"
        f"Do NOT wait, sleep, or poll.",
    )
```

后台执行立即返回 Task ID。返回消息里特意强调「Do NOT wait, sleep, or poll」，因为 LLM 有时候会试图写一个轮询循环来等后台任务完成，这会浪费上下文和 Token。

前台执行：

```Python
try:
    if is_fork:
        result_text = await sub_agent.run_to_completion(
            "", conversation
        )
    else:
        result_text = await sub_agent.run_to_completion(p.prompt)
except Exception as e:
    self._trace_manager.complete(trace_node.agent_id, "failed")
    return ToolResult(output=f"Sub-agent failed: {e}", is_error=True)
```

前台执行会阻塞父 Agent 直到 SubAgent 跑完。 `run_to_completion` 内部就是一个完整的 Agent Loop。

## TaskManager：后台任务的生命周期

### 启动

```Python
def launch(self, agent, task, name="", fork_conversation=None):
    task_id = uuid.uuid4().hex[:8]
    bg = BackgroundTask(
        id=task_id, name=name or task_id,
        agent=agent, task=task,
    )
    self._tasks[task_id] = bg

    async_task = asyncio.create_task(
        self._run_background(task_id, fork_conversation)
    )
    self._async_tasks[task_id] = async_task
    bg.cancel = async_task.cancel
    return task_id
```

用 `asyncio.create_task` 创建后台协程。Task ID 是 UUID 的前 8 个字符，足够在一个会话内唯一。把 `async_task.cancel` 存到 BackgroundTask 上，后续取消时直接调用。

### 运行和通知

```Python
async def _run_background(self, task_id, fork_conversation=None):
    bg = self._tasks.get(task_id)
    try:
        if fork_conversation is not None:
            result = await bg.agent.run_to_completion("", fork_conversation)
        else:
            result = await bg.agent.run_to_completion(bg.task)
        bg.result = result
        bg.status = "completed"
    except asyncio.CancelledError:
        bg.status = "cancelled"
    except Exception as e:
        bg.status = "failed"
        bg.result = f"Error: {e}"
```

三种终态：completed、cancelled、failed。无论哪种情况， `finally` 块都会收集 Token 用量并发出通知：

```Python
    finally:
        bg.end_time = time.monotonic()
        bg.progress.input_tokens = bg.agent.total_input_tokens
        bg.progress.output_tokens = bg.agent.total_output_tokens
        await self._notify_queue.put(task_id)
```

任务完成后把 task\_id 放入通知队列 `_notify_queue` 。Agent Loop 的每轮迭代会调用 `poll_completed` 检查是否有任务完成：

```Python
def poll_completed(self) -> list[BackgroundTask]:
    completed: list[BackgroundTask] = []
    while not self._notify_queue.empty():
        try:
            task_id = self._notify_queue.get_nowait()
            bg = self._tasks.get(task_id)
            if bg is not None:
                completed.append(bg)
        except asyncio.QueueEmpty:
            break
    return completed
```

这是推拉结合的模式：任务完成时主动推送 ID 到队列（push），主循环每轮拉取完成的任务（pull）。

### adopt\_running：接管正在运行的 Agent

```Python
def adopt_running(self, agent, task_description,
                   partial_result="", name="") -> str:
    task_id = uuid.uuid4().hex[:8]
    bg = BackgroundTask(id=task_id, name=name or task_id,
                         agent=agent, task=task_description,
                         result=partial_result)
    self._tasks[task_id] = bg
    async_task = asyncio.create_task(self._continue_background(task_id))
    self._async_tasks[task_id] = async_task
    bg.cancel = async_task.cancel
    return task_id
```

`adopt_running` 用来把一个前台正在跑的 Agent 转成后台任务。它不会重新启动 Agent，而是创建一个新的异步任务来接管剩余的执行。 `partial_result` 保留了转后台之前已经产生的结果，最终结果会拼接上去。

## Worktree 隔离模式

```Python
async def _execute_with_worktree(self, p):
    # ... 获取定义 ...
    wt_name = generate_worktree_name()
    try:
        wt = await self._worktree_manager.create(wt_name, "HEAD")
    except Exception as e:
        return ToolResult(output=f"Failed to create worktree: {e}",
                          is_error=True)
    notice = build_worktree_notice(self._parent_agent.work_dir, wt.path)
    task = notice + "\n\n" + p.prompt
```

Worktree 模式会创建一个临时的 Git worktree，SubAgent 在这个独立的工作目录里操作，不影响父 Agent 的文件系统。执行完后自动清理：

```Python
cleanup = await self._worktree_manager.auto_cleanup(
    wt_name, wt.head_commit
)
if cleanup.kept:
    result_text = (result_text or "") + (
        f"\n[Worktree preserved at {cleanup.path}, "
        f"branch {cleanup.branch}]"
    )
```

如果 SubAgent 修改了文件，worktree 会保留下来（连同分支），让用户后续 review 或 merge。如果没有修改，自动删除。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| Agent 定义格式 | YAML frontmatter + Markdown body |
| 定义加载优先级 | project > user > builtin，先到先得 |
| 热重载 | 每次 `get` 时尝试重新解析文件 |
| 工具过滤 | 五层递进：MCP 直通 → 全局黑名单(7) → 自定义限制(7) → 后台白名单(16) → 定义级限制 |
| Fork 防嵌套 | 扫描对话历史中的 `<fork_boilerplate>` 标签 |
| 未完成工具调用 | Fork 时用 `"interrupted"` 占位符补全 |
| 后台执行 | `asyncio.create_task` + 通知队列 |
| 任务取消 | 存储 `asyncio.Task.cancel` 引用 |
| 模型选择 | 参数级 > 定义级 > 继承父 Agent |
| Worktree 清理 | 有修改则保留，无修改则删除 |
| 团队工具 | In-process 用白名单，pane 模式近乎全量 |