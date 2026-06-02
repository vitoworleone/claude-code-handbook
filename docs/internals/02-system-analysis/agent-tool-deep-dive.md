# 05d - AgentTool 实现详解

> 05c 中我们用 3 行介绍了 AgentTool "生成子 Agent，三种模式"。但 AgentTool 是所有工具中最复杂的一个——它是唯一递归调用 `query_loop` 的工具，涉及 3 个文件、两种执行模式、一套隔离机制。本篇逐层展开。

![img](../assets/images/05d - AgentTool 实现详解.PNG)

## **1. AgentTool 在系统中的独特性**

在 `cc/tools/`  下的所有工具中，AgentTool 有着不可替代的结构性地位：它是唯一一个调用 `query_loop`  的工具。

其他工具——BashTool 执行命令、FileReadTool 读取文件、FileEditTool 修改文件——都是"叶子操作"，它们接收参数、执行单一动作、返回结果。 AgentTool 不同：它生成一个完整的子 Agent，子 Agent 拥有独立的 `messages[]`  、独立的 `ToolRegistry`  、独立的 `system_prompt`  ，然后在这套独立上下文中运行一个完整的 `query_loop`  对话循环。子 Agent 可以调用工具、处理工具结果、多轮对话，直到完成任务。

这里有一个关键事实需要强调： **子 Agent 不是独立进程，而是同一进程内的另一个 `query_loop` 调用。** 前台模式下，父 Agent 的 `query_loop` 在等待 `execute()` 返回时被 `await` 阻塞；后台模式下，子 Agent 以 `asyncio.Task` 在同一事件循环中并发运行。两种情况都没有跨进程通信。

为了防止无限递归（子 Agent 再创建子 Agent 再创建子 Agent...），子 Agent 的工具注册表中 **排除了 AgentTool 自身** 。这是一个硬性限制，写在 `execute()` 的 registry 构建逻辑中。

## **2. execute() 的完整流程**

> 源码文件： `cc/tools/agent/agent_tool.py`

`AgentTool` 继承自 `Tool` 基类，核心逻辑集中在 `execute()` 方法（第 129-264 行）。按代码执行顺序，流程如下：

### **2.1 参数提取（第 134-138 行）**

```Python
prompt = tool_input.get("prompt", "")
agent_model = tool_input.get("model") or self._model
run_in_bg = tool_input.get("run_in_background", False)
description = tool_input.get("description", "agent task")
isolation = tool_input.get("isolation")
```

`prompt` 是唯一必需参数（第 140 行做了空值检查）。 `model` 可选——未指定时使用构造函数传入的默认模型（第 57 行 `self._model` ，默认 `claude-sonnet-4-20250514` ）。 `run_in_background` 决定前台还是后台执行。 `isolation` 目前只支持 `"worktree"` 一个枚举值。

### **2.2 Worktree 创建（第 147-163 行）**

如果 `isolation == "worktree"` ，在进入主执行流程之前先创建 git worktree：

```Python
agent_wt_id = f"agent-{uuid4().hex[:8]}"
worktree_path = await create_agent_worktree(self._cwd or ".", agent_wt_id)
```

`uuid4().hex[:8]` 生成 8 位十六进制标识符，保证多个并发 agent 的 worktree 路径不冲突。如果创建失败（例如当前目录不在 git 仓库中），直接返回 `is_error=True` 的 ToolResult，不继续执行。

注意 worktree 创建发生在 registry 构建和模式分支之前——这意味着无论前台还是后台模式，都可以使用 worktree 隔离。

### **2.3 子 registry 构建（第 171-178 行）**

这是 AgentTool 的关键设计之一：子 Agent 继承父 Agent 的所有工具，但有选择性地排除某些工具。

```Python
child_registry = ToolRegistry()
interactive_tools = {"AskUserQuestion"} if run_in_bg else set()
for tool in self._parent_registry.list_tools():
    if tool.get_name() == AGENT_TOOL_NAME:
        continue
    if tool.get_name() in interactive_tools:
        continue
    child_registry.register(tool)
```

遍历父 registry 中的每个工具实例，逐个注册到子 registry，过程中做两层过滤：

1. **始终排除 AgentTool 自身** （`AGENT_TOOL_NAME = "Agent"`），防止递归派生。
2. **后台模式额外排除交互式工具** （`AskUserQuestion`），因为后台 agent 没有与用户交互的通道。

在 Swarm（Teammate）模式下，排除规则更严格——还会移除 `TeamCreate` 和 `TeamDelete` ，确保只有 Leader 能管理团队。不过这部分逻辑不在当前三个文件中，此处仅提及。

排除规则汇总：

| 模式            | 排除的工具                        | 原因                        |
| --------------- | --------------------------------- | --------------------------- |
| 前台            | AgentTool                         | 防止无限递归                |
| 后台            | AgentTool, AskUserQuestion        | 后台无法与用户交互          |
| Teammate(Swarm) | AgentTool, TeamCreate, TeamDelete | 只有 Leader 能创建/解散团队 |

### **2.4 System prompt 与 call_model 准备（第 181-183 行）**

```Python
system_prompt = DEFAULT_AGENT_PROMPT
call_model = self._call_model_factory(model=agent_model)
```

子 Agent 使用预定义的 `DEFAULT_AGENT_PROMPT` （定义在 `cc/prompts/sections.py` 第 35 行），内容是一段简洁的行为准则："你是 Claude Code 的 agent，用可用工具完成任务，不要过度工程，完成后简洁汇报。"

`call_model_factory` 是构造函数注入的工厂函数（第 55 行 `self._call_model_factory` ）。它接受 `model` 参数，返回一个绑定了特定模型的 `call_model` 可调用对象。这一机制的实际用途是：简单任务可以用便宜模型（如 `claude-haiku-4-5` ），复杂任务用强模型（如 `claude-sonnet-4-20250514` ）。模型选择权交给了调用 AgentTool 的父 Agent——父 Agent 在 tool_input 中通过 `model` 字段指定。

### **2.5 权限检查器（第 189 行）**

```Python
sub_perm_checker = self._build_sub_permission_checker(is_background=run_in_bg)
```

`_build_sub_permission_checker` （第 104-127 行）构建子 Agent 专用的权限检查器。所有子 Agent 使用 `ACCEPT_EDITS` 权限模式。前台子 Agent 的 `is_interactive=True` ，遇到需要确认的操作时可以询问用户；后台子 Agent 的 `is_interactive=False` ，遇到需要确认的操作时直接失败（fail-fast），因为没有交互通道。

### **2.6 模式分支**

参数准备完毕后，代码分为两条路径：后台模式（第 194-221 行）和前台模式（第 226-264 行）。

**---**

## **3. 前台模式详解**

> 源码文件： `cc/tools/agent/agent_tool.py` ，第 226-264 行

前台模式是默认模式，也是更简单的路径。

### **3.1 初始消息构建**

```Python
messages: list[Message] = [UserMessage(content=prompt)]
```

子 Agent 的对话历史从一条 `UserMessage` 开始——内容就是父 Agent 传入的 `prompt` 。子 Agent 没有父 Agent 的对话历史，它是一个全新的对话。

### **3.2 运行 query_loop**

```Python
async for event in query_loop(
    messages=messages,
    system_prompt=system_prompt,
    tools=child_registry,
    call_model=call_model,
    max_turns=30,
    permission_checker=sub_perm_checker,
):
```

这就是递归调用的核心。 `query_loop` （定义在 `cc/core/query_loop.py` 第 95 行）是一个 `AsyncIterator[QueryEvent]` ——它 yield 事件流。子 Agent 的 `query_loop` 使用独立的 `messages` 、 `system_prompt` 、 `tools` （子 registry）和 `call_model` ，与父 Agent 完全隔离。

`max_turns=30` 是安全阀。 `query_loop` 的默认 `max_turns` 是 100，但子 Agent 被限制为 30 轮。这防止子 Agent 在复杂任务中陷入无限循环消耗大量 token。

注意这里使用的是延迟导入（第 132 行 `from cc.core.query_loop import query_loop` ），因为 `agent_tool.py` 和 `query_loop.py` 之间存在循环依赖： `query_loop` 通过 `ToolRegistry` 调用工具，工具中包含 `AgentTool` ，而 `AgentTool.execute()` 又调用 `query_loop` 。延迟导入打破了这个模块级循环。

### **3.3 事件收集**

```Python
if isinstance(event, TextDelta):
    output_parts.append(event.text)
elif isinstance(event, TurnComplete) and event.stop_reason == "end_turn":
    break
```

子 Agent 的 `query_loop` 产生的事件流中，只关注两类事件：

- `TextDelta` ：模型输出的文本片段，累积到 `output_parts` 列表中。
- `TurnComplete` 且 `stop_reason == "end_turn"` ：模型认为任务完成，提前终止循环。

其他事件（如 `ToolUseStart` 、 `ToolResult` 等）被静默忽略——子 Agent 的工具调用过程对父 Agent 不可见，父 Agent 只关心最终文本输出。

### **3.4 异常处理与 worktree 清理**

```Python
except Exception as e:
    logger.warning("Agent failed: %s", e)
    return ToolResult(content=f"Agent error: {e}", is_error=True)
finally:
    if worktree_path is not None:
        await cleanup_agent_worktree(worktree_path, self._cwd or ".")
```

`try/except` 捕获子 Agent 执行中的所有异常，转化为 `is_error=True` 的 ToolResult 返回给父 Agent。这遵循项目的关键约束："工具错误不中断循环"。

`finally` 块保证即使子 Agent 崩溃，worktree 也会被清理。 `cleanup_agent_worktree` 本身的异常被单独捕获（第 257-258 行），清理失败不影响结果返回。

### **3.5 结果返回**

```Python
result_text = "".join(output_parts)
if not result_text.strip():
    return ToolResult(content="(Agent produced no output)")
return ToolResult(content=result_text)
```

将累积的文本片段拼接成完整字符串。如果子 Agent 没有产生任何文本输出（可能只做了工具调用没有文字总结），返回占位文本。

**前台模式的核心特征** ：阻塞。父 Agent 的 `execute()` 在整个子 Agent 的 `query_loop` 完成之前不会返回。父 Agent 的对话循环在此期间暂停。

**---**

## **4. 后台模式详解**

> 源码文件： `cc/tools/agent/agent_tool.py` ，第 194-221 行

> 源码文件： `cc/tools/agent/background.py`

后台模式的核心区别是 **不阻塞父 Agent** 。子 Agent 的执行被封装为协程，交给 `BackgroundAgentManager` 管理，`execute()` 立即返回。

### **4.1 协程封装（第 197-211 行）**

```Python
async def _run_agent() -> str:
    msgs: list[Message] = [UserMessage(content=prompt)]
    parts: list[str] = []
    async for event in query_loop(
        messages=msgs,
        system_prompt=system_prompt,
        tools=child_registry,
        call_model=call_model,
        max_turns=30,
        permission_checker=_bg_perm,
    ):
        if isinstance(event, TextDelta):
            parts.append(event.text)
    return "".join(parts) or "(no output)"
```

这个闭包捕获了所有准备好的上下文（ `prompt` 、 `system_prompt` 、 `child_registry` 、 `call_model` 、 `_bg_perm` ），定义了子 Agent 的完整执行逻辑。注意与前台模式的两个区别：(1) 没有 `TurnComplete` 的提前 break 检查——后台 Agent 总是运行到 `query_loop` 自然结束；(2) 没有 `try/except` ——异常处理由 `BackgroundAgentManager._run_and_notify()` 统一负责。

还有一个细节：第 195 行 `_bg_perm = sub_perm_checker` 将权限检查器绑定到局部变量，确保闭包捕获的是当前值而非引用。

### **4.2 任务提交（第 213-221 行）**

```Python
agent_id = f"agent-{uuid4().hex[:8]}"
task_id = await self._bg_manager.spawn(agent_id, description, _run_agent())
return ToolResult(
    content=f"Agent '{description}' launched in background (task_id: {task_id}). "
    "You will be notified when it completes."
)
```

`_run_agent()` 被调用（注意是带括号的——创建协程对象），传给 `bg_manager.spawn()` 。 `execute()` 立即返回一个包含 task_id 的 ToolResult。父 Agent 拿到这个结果后继续自己的对话循环，不需要等待。

### **4.3 BackgroundAgentManager 内部机制**

`BackgroundAgentManager` （ `background.py` 第 32 行）维护三个数据结构：

- `_task_registry` （第 43 行）：可选的全局 `TaskRegistry` ，支持通过 `/tasks` 命令查看所有任务。
- `_results` （第 46 行）： `asyncio.Queue[tuple[str, str]]` ——完成通知队列，存储 `(agent_id, result_text)` 对。
- `_tasks` （第 50 行）： `dict[str, asyncio.Task]` ——持有 `asyncio.Task` 的强引用。

为什么需要 `_tasks` 字典？ `asyncio` 不持有对 `Task` 的强引用。如果代码中没有任何变量引用一个 Task，Python 的垃圾回收器可能在 Task 完成前将其回收。 `_tasks` 字典充当"保活"容器。

#### **spawn()（第 52-77 行）**

```Python
task = asyncio.create_task(self._run_and_notify(agent_id, coro))
self._tasks[agent_id] = task
```

`spawn()` 将原始协程包装在 `_run_and_notify()` 中，创建为 `asyncio.Task` 。如果有 `TaskRegistry` ，在其中注册任务元数据。返回 task_id。

#### **_run_and_notify()（第 79-115 行）**

这是后台 Agent 的执行包装器，负责三件事：

1. `await coro` ——执行子 Agent 协程，获取结果文本。
2. `await self._results.put((agent_id, result_text))` ——将结果放入通知队列。
3. 更新 `TaskRegistry` 中的任务状态为 `COMPLETED` 或 `FAILED` 。

异常处理分两类： `CancelledError` （任务被外部取消，如用户中断）只记录日志，不放入结果队列；其他异常将错误信息放入结果队列（ `f"Agent error: {e}"` ），并更新状态为 `FAILED` 。

TaskRegistry 状态更新有一个实现细节值得注意（第 94-97 行）：因为 TaskRegistry 的主键是 `task_id` （由 registry 自动生成，可能与 `agent_id` 不同），代码需要遍历所有记录、通过 metadata 中的 `agent_id` 字段匹配来找到对应记录。这是一个 O(n) 查找，但后台 agent 数量通常很少，不构成性能问题。

#### **poll_completed()（第 117-131 行）**

```Python
async def poll_completed(self) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    while not self._results.empty():
        try:
            results.append(self._results.get_nowait())
        except asyncio.QueueEmpty:
            break
    return results
```

非阻塞地从结果队列中取出所有已完成的 agent 结果。使用 `get_nowait()` 而非 `get()` ——后者在队列为空时会阻塞。 `empty()` 和 `get_nowait()` 之间可能存在竞态条件（另一个协程可能在两个调用之间取走了元素），所以额外捕获 `QueueEmpty` 异常。

主 REPL 循环在每次对话轮次开始时调用 `poll_completed()` ，将后台 Agent 的完成结果注入到对话上下文中，让模型知道哪些后台任务已完成及其结果。

**---**

## **5. Worktree 隔离机制**

> 源码文件： `cc/tools/agent/worktree.py`

Worktree 隔离解决的问题是：多个并发 Agent 修改同一个仓库时文件冲突。Git worktree 创建同一仓库的独立工作副本——共享 `.git` 数据库但拥有独立的工作目录和索引。

### **5.1 create_agent_worktree()（第 31-70 行）**

```Python
worktree_dir = os.path.join(
    tempfile.gettempdir(),
    f"cc-agent-worktree-{agent_id}",
)
proc = await asyncio.create_subprocess_exec(
    "git", "worktree", "add", "--detach", worktree_dir,
    cwd=cwd, ...
)
```

在系统临时目录下创建 worktree，路径形如 `/tmp/cc-agent-worktree-agent-a1b2c3d4` 。 `--detach` 参数创建 detached HEAD 的 worktree——不关联任何分支，子 Agent 在其中的 commit 不会移动任何分支指针。

使用 `asyncio.create_subprocess_exec` 而非 `os.system` ，遵循项目规范。创建失败时（例如 cwd 不在 git 仓库中）抛出 `RuntimeError` ，由调用方捕获。

### **5.2 cleanup_agent_worktree()（第 73-123 行）**

清理分两步：

**第一步——检查未提交修改** （第 88-103 行）：

```Python
status_proc = await asyncio.create_subprocess_exec(
    "git", "status", "--porcelain",
    cwd=worktree_path, ...
)
```

`git status --porcelain` 输出机器可读的状态。如果有输出，说明 worktree 中有未提交的修改。此时 **保留 worktree 不删除** ，只记录警告。这是安全策略——如果子 Agent 修改了文件但没来得及提交（或者 Agent 崩溃了），删除 worktree 会导致这些修改永久丢失。

**第二步——删除 worktree** （第 109-123 行）：

```Python
remove_proc = await asyncio.create_subprocess_exec(
    "git", "worktree", "remove", worktree_path,
    cwd=cwd, ...
)
```

使用 `git worktree remove` 而非直接删除目录。这是因为 git 在 `.git/worktrees` 中维护 worktree 的注册信息，直接删除目录会留下悬空记录。删除失败不抛异常，只记录警告。

### **5.3 生命周期保障**

在 `agent_tool.py` 的 `execute()` 中（第 247-258 行），worktree 清理放在 `finally` 块中：

```Python
finally:
    if worktree_path is not None:
        try:
            await cleanup_agent_worktree(worktree_path, self._cwd or ".")
        except Exception as e:
            logger.warning("Worktree cleanup failed: %s", e)
```

双重保护： `finally` 保证即使子 Agent 异常也会尝试清理；内层 `try/except` 保证清理自身的异常不影响结果返回。

**局限性** ：worktree 只隔离 git 管理的文件。非 git 文件（如临时文件写到 `/tmp` 其他位置）、网络请求、进程操作都不在隔离范围内。

**---**

## **6. call_model_factory 的作用**

`AgentTool` 的构造函数接受 `call_model_factory` （第 45 行），而非直接接受 `call_model` 。这个设计允许子 Agent 使用与父 Agent 不同的模型。

使用流程：

1. 父 Agent 调用 AgentTool 时在 tool_input 中指定 `"model": "claude-haiku-4-5"` 。
2. `execute()` 第 135 行提取 `agent_model = tool_input.get("model") or self._model` 。
3. 第 183 行调用 `self._call_model_factory(model=agent_model)` ，工厂函数返回一个绑定了 haiku 模型的新 `call_model` 闭包。
4. 子 Agent 的 `query_loop` 使用这个闭包与 haiku 通信。

实际场景：父 Agent 用 Sonnet 处理复杂推理，将简单的文件搜索子任务委派给 Haiku 执行，节省成本和时间。

**---**

## **7. 三文件协作全景**

最后用一张调用关系把三个文件串起来：

```Plaintext
AgentTool.execute()                        [agent_tool.py]
  |
  |-- isolation=="worktree"?
  |     +--> create_agent_worktree()       [worktree.py]
  |
  |-- build child_registry (exclude self)
  |-- build call_model via factory
  |-- build permission checker
  |
  |-- run_in_background?
  |     |
  |     +--(yes)--> bg_manager.spawn()     [background.py]
  |     |             |
  |     |             +--> asyncio.create_task(_run_and_notify())
  |     |             |      |
  |     |             |      +--> await coro (query_loop)
  |     |             |      +--> results.put()
  |     |             |
  |     |             +--> return task_id (immediate)
  |     |
  |     +--(no)---> async for event in query_loop(...)
  |                   +--> collect TextDelta
  |                   +--> break on TurnComplete(end_turn)
  |
  |-- finally: cleanup_agent_worktree()    [worktree.py]
  |
  +--> return ToolResult
```

`agent_tool.py` 是入口和编排者，负责参数处理、registry 裁剪、模式分支。 `background.py` 负责后台任务的生命周期管理——启动、追踪、通知。 `worktree.py` 负责文件系统级隔离的创建和清理。三者职责清晰，通过 `execute()` 中的条件分支协调。