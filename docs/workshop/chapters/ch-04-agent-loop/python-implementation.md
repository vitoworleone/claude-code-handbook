# Python 源码解析：Agent 主循环

## 模块概览

Python 版把 Agent Loop 的所有代码集中在一个文件里：

|     |     |
| --- | --- |
| 文件  | 职责  |
| `mewcode/agent.py` | 全部。事件类型定义、Agent 类、StreamCollector 流消费、StreamingExecutor 并行执行、工具分批逻辑 |

一个文件 1098 行，集中度很高。原因是 Python 版把事件定义、流收集器、执行器都内聚到同一个模块里了，没有拆文件。所有和 Agent Loop 相关的类型和逻辑都在一个地方找到，理解起来不需要跳来跳去。

## 核心类型

### Agent 类

```Python
class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 50,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
    ) -> None:
```

驱动循环的核心三件套是 `client` 、 `registry` 、 `protocol` 。Python 版的构造函数直接把所有可选能力都用关键字参数传进来了，一次性配好。Python 天然适合这种风格：关键字参数自带命名和默认值，调用方只需要指定想改的参数，其余的全用默认值。

`max_iterations` 默认 50，选择了比较保守的默认值，防止 Agent 失控运行。

额外多出来的几个字段值得注意： `compact_breaker` 是上下文压缩的熔断器，防止压缩反复失败时陷入死循环； `_loop_count` 追踪循环完成次数，用于触发定期记忆提取； `active_skills` 管理当前激活的技能提示词。

### AgentEvent：用 Union 模拟代数类型

Python 版用 `dataclass` + `Union` 类型别名定义封闭的事件类型集合：

```Python
@dataclass
class StreamText:
    text: str

@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float
```

12 种事件用 Union 类型别名汇总：

```Python
AgentEvent = (
    StreamText | ThinkingText | RetryEvent
    | ToolUseEvent | ToolResultEvent | TurnComplete
    | LoopComplete | UsageEvent | ErrorEvent
    | PermissionRequest | CompactNotification | HookEvent
)
```

12 种事件类型，涵盖了 Agent Loop 的所有通知场景。 `ThinkingText` （思维链片段）、 `RetryEvent` （重试通知）、 `CompactNotification` （上下文压缩通知）、 `HookEvent` （Hook 执行结果）这四个事件拆得比较细，让 UI 层可以对每种情况做精确的展示处理。

权限请求事件用 `asyncio.Future` 实现反向通信：

```Python
@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]
```

Agent 创建一个 Future，通过事件交给 UI，然后 `await future` 阻塞等待。UI 那边调 `future.set_result()` 写入用户选择。Future 只能 set 一次，语义上非常精确，因为一个权限请求就该只有一次回应。这比用更通用的并发原语更安全，不会出现多次写入导致的竞态问题。

### StreamCollector：流消费器

Python 版抽出了一个专门的收集器来消费流事件：

```Python
class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(...)
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
```

`consume()` 是一个 async generator：它接收 LLM 的流式事件（ `AsyncIterator[StreamEvent]` ），内部做汇总（累积文本、收集工具调用），同时把转换后的 `AgentEvent` 往外 yield。调用方在外面 `async for` 消费的时候，每拿到一个事件就可以立刻推给 UI。

这个设计把「读 LLM 流 + 汇总 + 转发」封装成了一个可复用的 generator，主循环只需要 `async for event in collector.consume(llm_stream): yield event` 一行。流的读取逻辑和主循环的控制流彻底解耦，各自独立演进。

## 主循环走读

### 入口：run()

```Python
async def run(
    self, conversation: ConversationManager
) -> AsyncIterator[AgentEvent]:
    # 注入环境上下文、长期记忆
    env_context = build_environment_context(...)
    conversation.inject_environment(env_context)
    memory_content = self.memory_manager.load() if self.memory_manager else ""
    conversation.inject_long_term_memory(self.instructions_content, memory_content)

    # 触发 session_start hook
    if self.hook_engine:
        ctx = self._build_hook_context("session_start")
        await self.hook_engine.run_hooks("session_start", ctx)
        for he in self._drain_hook_events():
            yield he
```

`run()` 本身就是一个 async generator，调用方直接 `async for event in agent.run(conv)` 消费。async generator 天然就是惰性推送：消费者拉一个，生产者才产一个。不需要缓冲区管理，不需要操心生产和消费的速度差异，事件按需产生。

在进入循环之前， `run()` 先做两件准备工作：把环境上下文和长期记忆注入对话，然后触发 session\_start Hook。这些准备逻辑内聚在 Agent 自己，不散落在上层调用者里。

### 循环骨架

```Python
while True:
    iteration += 1
    if iteration > self.max_iterations:
        yield ErrorEvent(...)
        break
    # 1. turn_start hook     →  9. 消费流式响应
    # 2. 消费邮箱（团队模式）  → 10. post_receive hook
    # 3. 工具输出预算控制      → 11. max_tokens 恢复
    # 4. 自动上下文压缩        → 12. 没有工具调用 → 结束
    # 5. pre_send hook       → 13. 分批执行工具
    # 6. 构建系统提示词        → 14. 连续未知工具检查
    # 7. Plan Mode 注入      → 15. turn_end hook
    # 8. 获取工具 schema，调用 LLM
```

步骤不少，因为 Python 版在每个关键节点都穿插了 Hook 调用。turn\_start/turn\_end/pre\_send/post\_receive 四个事件点覆盖了 Agent Loop 的完整生命周期，让 Hook 开发者可以在任何阶段介入。

### 调用 LLM 和消费流式响应

```Python
collector = StreamCollector()
llm_stream = self.client.stream(
    conversation, system=system, tools=tools
)
async for event in collector.consume(llm_stream):
    yield event

response = collector.response
```

只有四行。 `client.stream()` 返回一个 async iterator， `collector.consume()` 把它包装成另一个 async iterator，主循环 `async for` 消费并 yield 出去。整个流处理链是懒的：LLM 产一个 token → collector 收集 + 转换 → 主循环 yield → UI 消费。

流消费结束后， `collector.response` 里已经汇总好了完整的文本、工具调用列表、Token 用量。

Python 版的设计选择是在流消费完毕后再统一执行工具，而不是在流式阶段就提交。这让控制流更清晰：先完整消费 LLM 输出，汇总好所有工具调用，然后一次性分批执行。牺牲了一些延迟（工具执行不能和 LLM 输出重叠），但换来了更可预测的执行顺序和更简单的调试体验。

### 终止判断

```Python
if not response.tool_calls:
    conversation.add_assistant_message(
        response.text, thinking_blocks=conv_thinking
    )
    self._loop_count += 1
    if (
        self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
        and self.memory_manager
    ):
        asyncio.ensure_future(self._extract_memories(conversation))
    yield LoopComplete(total_turns=iteration)
    break
```

没有工具调用就结束循环。Python 版还有一个记忆提取的定时触发：每完成 `MEMORY_EXTRACTION_INTERVAL` （5）次循环，就异步启动一次记忆提取。 `asyncio.ensure_future` 是 Python 的 fire-and-forget 方式，启动一个后台 coroutine 但不等它完成，不阻塞主循环的退出。

### 工具结果收集

```Python
tool_uses = [
    ToolUseBlock(
        tool_use_id=tc.tool_id,
        tool_name=tc.tool_name,
        arguments=tc.arguments,
    )
    for tc in response.tool_calls
]
conversation.add_assistant_message(
    response.text, tool_uses, thinking_blocks=conv_thinking
)

tool_results: list[ToolResultBlock] = []
batches = partition_tool_calls(response.tool_calls, self.registry)
```

先把工具调用作为 assistant 消息写入对话历史，然后把工具调用分批。分批逻辑由 `partition_tool_calls` 函数负责，下面工具执行部分详细讲。

## 四个停止条件

理论篇讲了四个停止条件，看 Python 版怎么实现的：

**1\. LLM 不再调用工具**

就是上面那个 `if not response.tool_calls` 判断。这是最常见的正常退出路径。Generator 走到 `break` ， `run()` 方法自然结束，调用方的 `async for` 循环也随之退出。

**2\. 迭代次数上限**

```Python
if iteration > self.max_iterations:
    yield ErrorEvent(
        message=f"Agent reached maximum iterations ({self.max_iterations})"
    )
    break
```

在循环最开头检查。默认 50 次，比较保守。如果 50 轮还没完成，多半是哪里出了问题。

**3\. 连续未知工具**

```Python
if br.is_unknown:
    consecutive_unknown += 1
else:
    consecutive_unknown = 0
# ...
if consecutive_unknown >= 3:
    yield ErrorEvent(
        message="Agent terminated: too many consecutive unknown tool calls"
    )
    break
```

连续 3 次调用不存在的工具就终止，中间有一次正常调用就重置计数。

**4\. 用户取消**

Python 版没有显式的 context 取消检查。这是因为 async generator 有天然的取消机制：调用方只要停止迭代（break 出 `async for` 循环），generator 就会被垃圾回收，里面的循环自动终止。如果需要更主动的取消，上层代码可以 cancel 掉运行 `run()` 的 task。

## 工具执行

### 分批策略：partition\_tool\_calls

Python 版在执行工具之前有一个分批步骤：

```Python
def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = (tool is not None
                and tool.is_concurrency_safe
                and registry.is_enabled(tc.tool_name))
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches
```

这个函数把工具调用序列切分成多个 batch。连续的「并发安全」工具（比如读文件）合并成一个并发 batch，其他工具（比如写文件、执行命令）各自成为独立 batch。

比如 LLM 返回了 `[Read, Read, Write, Read]` ，会被切成三个 batch： `[Read, Read]` （并发执行）、 `[Write]` （串行执行）、 `[Read]` （串行执行）。这保证了写操作不会和其他操作并行，避免竞态条件。分批策略在性能和安全之间取了一个平衡点：读操作尽可能并行提速，写操作严格串行保安全。

### 并行执行：asyncio.gather

并发 batch 用 `asyncio.gather` 一次性并发执行：

```Python
async def _execute_batch_parallel(
    self, calls: list[ToolCallComplete]
) -> list[_ToolExecResult]:
    tasks = [self._execute_single_tool_direct(tc) for tc in calls]
    return list(await asyncio.gather(*tasks))
```

`asyncio.gather` 同时启动所有 coroutine，等全部完成后一起返回。如果一个 batch 里有 5 个 Read 工具调用，它们会在同一个事件循环里并发执行，IO 等待时间重叠。

主循环里的分派逻辑：

```Python
for batch in batches:
    if batch.concurrent and len(batch.calls) > 1:
        batch_results = await self._execute_batch_parallel(batch.calls)
        for br in batch_results:
            # 收集结果，更新 consecutive_unknown
    else:
        for tc in batch.calls:
            async for item in self._execute_tool(tc):
                if isinstance(item, PermissionRequest):
                    yield item
                else:
                    result, elapsed, is_unknown = item
```

注意并发 batch 和串行 batch 走不同的代码路径。并发路径调 `_execute_single_tool_direct` （直接执行，不做权限检查），串行路径调 `_execute_tool` （完整的四关流程，包含权限检查和 Hook）。这是因为并发执行的读操作通常是安全的，不需要逐个询问权限。

### 单工具执行流程：\_execute\_tool

`_execute_tool` 是一个 async generator，走完整的四关：

**第一关：查找工具**

```Python
tool = self.registry.get(tc.tool_name)
if tool is None:
    result = ToolResult(
        output=f"Error: unknown tool '{tc.tool_name}'",
        is_error=True,
    )
    is_unknown = True
    yield result, elapsed, is_unknown
    return
```

找不到就标记 `is_unknown` ，为连续未知工具检查提供依据。

**第二关：权限检查**

```Python
if self.permission_checker:
    decision = self.permission_checker.check(tool, tc.arguments)
    if decision.effect == "deny":
        yield result, elapsed, is_unknown
        return
    if decision.effect == "ask":
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        desc = self._build_permission_description(tc)
        yield PermissionRequest(
            tool_name=tc.tool_name,
            description=desc,
            future=future,
        )
        response = await future
```

这里的精妙之处在于 `_execute_tool` 是 async generator。当需要请求权限时，它 yield 一个 `PermissionRequest` 事件出去，然后 `await future` 阻塞自己。主循环收到这个 yield 后再 yield 给 UI，UI 拿到 future 后让用户选择，选完 `future.set_result()` 写回结果。Generator 恢复执行，继续后面的逻辑。

这种设计让控制流看起来像是线性的：yield 出权限请求，然后 await 用户回应，继续后面的逻辑。async generator 天然支持这种「暂停等待外部输入后继续」的模式，不需要额外的同步机制。

**第三关：Pre-tool Hook**

Hook 检查在主循环里，在调 `_execute_tool` 之前完成：

```Python
if self.hook_engine:
    rejection = await self.hook_engine.run_pre_tool_hooks(hook_ctx)
    if rejection is not None:
        result = ToolResult(
            output=f"Hook rejected: {rejection.reason}",
            is_error=True,
        )
        # 直接 continue，不进入 _execute_tool
```

Python 版把 Hook 拦截放在了 `_execute_tool` 的外面，而不是里面。这样 `_execute_tool` 只关心权限 + 执行，Hook 逻辑由主循环管理。

**第四关：真正执行**

```Python
params = tool.params_model.model_validate(tc.arguments)
result = await tool.execute(params)
```

Python 版在执行前做了一步显式参数校验： `model_validate` 用 Pydantic 校验 LLM 传来的参数是否符合工具定义的 schema。显式校验比隐式的 JSON 反序列化更严格，能给出更友好的错误信息，比如「参数 file\_path 缺失」而不是笼统的反序列化失败。

### StreamingExecutor

StreamingExecutor 负责并行工具执行的调度：

```Python
class StreamingExecutor:
    def __init__(self) -> None:
        self._tasks: list[tuple[int, asyncio.Task[_ToolExecResult]]] = []
        self._order = 0

    def submit(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.append((self._order, task))
        self._order += 1

    async def collect_results(self) -> list[_ToolExecResult]:
        tasks = [t for _, t in sorted(self._tasks, key=lambda x: x[0])]
        results = await asyncio.gather(*tasks, return_exceptions=True)
```

`submit` 接收一个 coroutine，用 `asyncio.create_task` 把它调度到事件循环里立刻开始执行。 `collect_results` 用 `asyncio.gather` 等所有 task 完成， `return_exceptions=True` 让失败的 task 不会中断其他 task。结果按提交顺序返回。

Python 版用 asyncio 的 task + gather 来实现并行调度，代码很简洁。理解这段代码的关键是 asyncio 的调度模型： `create_task` 把 coroutine 注册到事件循环里立刻开始执行， `gather` 等待所有 task 完成并收集结果。

### 工具输出截断

```Python
def _maybe_persist_or_truncate(self, tool_use_id: str, text: str) -> str:
    if len(text) > SINGLE_RESULT_CHAR_LIMIT:
        fp = persist_tool_result(tool_use_id, text, self.session_dir)
        return make_persisted_preview(text, fp)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
    return text
```

截断策略分三级处理。特别大的输出（超过 `SINGLE_RESULT_CHAR_LIMIT` ）会被持久化到磁盘文件，对话里只保留预览和文件路径。中等大小的输出（超过 `MAX_OUTPUT_CHARS` ）直接截断。小输出原样保留。三级策略让上下文管理非常精细：超大输出不丢失（存磁盘可以用 ReadFile 读回来），中等输出减负，小输出无损。

## Plan Mode

```Python
if self.plan_mode:
    plan_path = str(self._get_plan_path())
    plan_exists = self._get_plan_path().exists()
    plan_reminder = build_plan_mode_reminder(
        plan_path, plan_exists, iteration
    )
    conversation.add_system_reminder(plan_reminder)
```

核心思路是不改变循环结构，只在每轮迭代开头注入一段 system-reminder。 `plan_mode` 是一个 property，直接读 `permission_mode` 的状态：

```Python
@property
def plan_mode(self) -> bool:
    return self.permission_mode == PermissionMode.PLAN
```

提示词告诉 LLM 只能思考和分析，不能执行写操作。即使 LLM 尝试调用写工具，权限系统也会拦住。提示词引导加权限硬拦截，两层保障确保 Plan Mode 的安全性。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 异步事件流 | async generator， `yield` 推送事件，调用方 `async for` 消费 |
| 主循环 | `while True` + `break` 出口 |
| 工具并行 | `partition_tool_calls` 分批 + `asyncio.gather` 并发执行读操作 |
| 权限交互 | `asyncio.Future` ，generator yield 出 `PermissionRequest` ， `await future` 等待回应 |
| 流消费 | `StreamCollector` async generator，读 LLM 流同时 yield AgentEvent |
| Plan Mode | 注入 system-reminder + 权限层拦截 |
| 上下文保护 | 三级策略：磁盘持久化 / 截断 / 原样保留 |
| 记忆提取 | `asyncio.ensure_future` fire-and-forget，每 5 次循环触发 |
| 参数校验 | Pydantic `model_validate` ，显式校验每个字段 |