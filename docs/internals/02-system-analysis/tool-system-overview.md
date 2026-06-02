# 05b 工具系统性讲解

> 上一篇讲了 ToolUseBlock 和 ToolResultBlock 的数据结构（消息层的零件）， 本篇讲这些零件背后的工具系统如何运转。

> 消息层定义了"模型请求调用工具"和"工具返回结果"的数据格式， **但从一个 ToolUseBlock 到达到一个 ToolResult 产生，中间经历了工具查找、并发调度、Hook 拦截、权限校验、异常兜底五个环节** 。这套机制分布在 `cc/tools/` 、 `cc/hooks/` 、 `cc/permissions/` 三个包中，本篇逐层展开。

![img](../assets/images/05b 工具系统性讲解.PNG)

## **1. 工具系统的最小抽象（Tool ABC）**

所有工具的契约定义在 `cc/tools/base.py` 。三个核心类型构成了工具系统的基石。

**ToolSchema** （第 22-32 行）是工具向 API 注册的描述结构，包含三个字段：`name`（唯一标识符）、`description`（功能描述，影响模型是否选择调用）、`input_schema`（JSON Schema 格式的参数定义）。这三个字段直接对应 Anthropic API 请求中 `tools` 数组的每个元素。

**ToolResult** （第 35-66 行）是所有工具执行后的统一返回格式。`content` 字段支持两种类型：`str`（绝大多数工具）和 `list[dict]`（富内容，如 FileReadTool 读取图片时返回 base64 内容块）。`is_error` 标记结果是否为错误——这是一个关键设计决策：工具错误不抛异常，而是返回 `is_error=True` 的 ToolResult，这样 query_loop 不会中断，模型可以看到错误信息并自主决定重试、换参数还是向用户解释。`.text` 属性提供统一的文本提取接口，当 content 是富内容列表时从每个 dict 中提取 `"text"` 字段并用换行连接。

**Tool** （第 69-112 行）是抽象基类，定义四个方法：

```Python
@abstractmethod def get_name(self) -> str           # 工具名，API 层面的唯一标识
@abstractmethod def get_schema(self) -> ToolSchema   # JSON Schema 描述
@abstractmethod async def execute(self, tool_input: dict) -> ToolResult  # 执行逻辑
def is_concurrency_safe(self, tool_input: dict) -> bool  # 默认 False
```

## **2. ToolRegistry：工具的注册与发现**

`ToolRegistry` （ `cc/tools/base.py` 第 115-161 行）是工具发现机制的核心，用 `dict[str, Tool]` 存储，保证 O(1) 查找。四个方法：

- `register(tool)` ：注册工具，不允许重名（重名会导致 tool_use 响应无法正确路由）
- `get(name)` ：按名称查找，返回 `Tool | None` 。返回 None 而非抛异常，因为 API 可能返回未注册的工具名（如 MCP 工具被移除后模型仍尝试调用）
- `list_tools()` ：返回所有工具实例，AgentTool 构建子 registry 时遍历父 registry 用
- `get_api_schemas()` ：将所有工具的 schema 转为 API 请求格式的 dict 列表

`get_api_schemas()`  在每轮 query_loop 的 Phase 1 被调用，生成发给 API 的 `tools`  参数，告诉模型当前可用的工具集。

全部 20+ 工具的注册发生在 `cc/main.py` 的 `_build_registry()` 函数（第 76-187 行），分三个层级：

\- **Tier 1** （顶层 import）：BashTool、FileReadTool、FileEditTool、FileWriteTool、GlobTool、GrepTool——核心文件操作，启动即绑定

\- **Tier 2** （lazy import）：TaskTools、WebFetchTool、WebSearchTool、NotebookEditTool、SkillTool、PlanModeTool、BriefTool、LSPTool、ToolSearchTool、AskUserQuestionTool、TodoWriteTool——延迟导入避免循环依赖和加速启动

\- **Tier 3** （协作工具）：AgentTool、TeamCreateTool、TeamDeleteTool、SendMessageTool——需要 `call_model_factory` 等运行时依赖，在 `_build_engine()` 中做二次布线

> **此处核心理解三层工具加载机制**

![img](../assets/images/05b 工具系统性讲解1.PNG)

**---**

## **3. StreamingToolExecutor：流式提前执行** **【核心优化机制之一】**

这是整个工具系统最核心的创新点，定义在 `cc/tools/streaming_executor.py` 。

**解决的问题** ：传统方案等 API 流式响应完全结束后才开始执行工具。假设模型返回 3 个 tool_use，传统模式需要等 3 个都解析完才启动第 1 个。StreamingToolExecutor 在第 1 个 tool_use block 完整解析出来时就立即启动执行，第 2、3 个到达时第 1 个可能已经完成了。

**触发时机** ：`query_loop.py` 第 166-196 行展示了集成方式。在 Phase 2 的流式循环中，每收到一个 `ToolUseStart` 事件（对应 API 流的 `content_block_stop`），就调用 `executor.add_tool(block)` 将工具交给执行器。流结束后调用 `executor.get_results()` 收集所有结果。

![img](../assets/images/05b 工具系统性讲解2.png)

### **3.1 内部状态**

构造函数（第 54-74 行）初始化五个关键状态：

```Python
self._pending: list[tuple[str, asyncio.Task[ToolResult]]] = []   # 已启动的任务
self._queue: list[ToolUseBlock] = []                              # 排队的工具
self._has_exclusive_running = False                                # 独占执行标记
self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)              # 并发限流（10）
```

`_pending` 记录所有已启动的 `(tool_use_id, asyncio.Task)` 对，用于最终收集结果。 `_queue` 暂存不能立即执行的工具。 `_has_exclusive_running` 标记当前是否有非并发安全工具在独占执行——为 True 时即使是并发安全的工具也必须排队。

### **3.2 add_tool() 的分派逻辑**

`add_tool()` （第 76-96 行）在每个 tool_use block 完整解析时被调用，判断逻辑只有两条路径：

```Python
if is_safe and not self._has_exclusive_running:
    self._start_execution(block)   # 立即启动 asyncio.Task
else:
    self._queue.append(block)      # 排队等待
```

排队的原因不仅是工具自身不安全——如果当前有 Edit 正在独占执行，即使是 Read 也不能并行，因为可能读到 Edit 写了一半的文件内容。这是注释中明确指出的竞态条件防护（第 93-96 行）。

### **3.3 _execute_one() 的四层执行栈**

单个工具的完整执行流程在 `_execute_one()` （第 105-146 行），四层嵌套：

**第一层：Semaphore 限流** 。`async with self._semaphore` 确保并发数不超过 `MAX_CONCURRENCY=10`，防止大量并发文件 I/O 耗尽文件描述符。

**第二层：PreToolUse Hook** 。如果配置了 hooks，调用 `run_pre_tool_hooks()` 检查是否有 hook 阻止执行。被阻止时直接返回 `is_error=True` 的 ToolResult，工具的 `execute()` 根本不会被调用——这就是 Hook 的"短路"能力。

**第三层：Permission Check** 。权限检查器（`self._permission_checker`）判断用户是否授权。在 ACCEPT_EDITS 模式下，Bash 这类高危工具需要用户确认，未授权则返回 `"Denied by permission policy"`。

**第四层：实际执行** 。`await tool.execute(block.input)` 调用工具的具体实现。用 try/except 包裹——异常转为 `ToolResult(content=f"Error: {e}", is_error=True)`，保证单个工具崩溃不中断整个 query_loop。

执行完成后还有 **PostToolUse Hook** 触发，用于审计日志或自动 lint 等后处理。

### **3.4 _process_queue() 的独占执行协议**

`_process_queue()` （第 174-198 行）在 `get_results()` 中被调用，处理 add_tool 期间排队的工具。策略如下：

对于并发安全工具：直接 `_start_execution()` ，信号量自动限流。

对于非并发安全工具： `await self._wait_pending()` 先排空所有已启动的并发任务 -> 设置 `_has_exclusive_running = True` -> 启动该工具 -> `await self._wait_pending()` 等它完成 -> 清除独占标记 -> 继续处理队列下一个。

这确保了非并发安全工具执行期间没有任何其他工具在并行运行。

### **3.5 时序示例：Read(safe) + Edit(unsafe) + Read(safe)**

假设模型返回三个工具调用，按顺序到达：

```Plaintext
t0: Read 到达 → is_safe=True, 无独占锁 → 立即启动 Task-A
t1: Edit 到达 → is_safe=False → 入队 _queue
t2: Read 到达 → _has_exclusive_running=False 但 Edit 在队列中... → 入队
    （注：此时没有独占锁，但 Edit 已入队，后续到的 safe 工具不受影响，
     但 Edit 之后的工具会在 _process_queue 中被正确排序）
t3: 流结束 → get_results() → _process_queue() 开始
    → 出队 Edit: _wait_pending() 等 Task-A 完成 → 启动 Edit → 等 Edit 完成
    → 出队 Read: is_safe=True → 直接启动
t4: 收集所有 Task 结果，按添加顺序返回
```

关键收益：Task-A（第一个 Read）在模型还在输出后续 token 时就已经开始执行了，而不是等三个工具都解析完才启动。

### `3.6 _pending` 的判断逻辑

`_pending` 是一个列表，里面存的是 `(tool_use_id, asyncio.Task)` 对：

```Python
self._pending: list[tuple[str, asyncio.Task[ToolResult]]] = []
```

每次调用 `_start_execution()` 就往里塞一个 Task：

```Python
def _start_execution(self, block):
    task = asyncio.create_task(self._execute_one(block))  # 启动后台任务
    self._pending.append((block.id, task))                # 记录进去
```

## `3.7 has_pending` 怎么判断

```Python
@property
def has_pending(self) -> bool:
    return len(self._pending) > 0 or len(self._queue) > 0
```

它只是简单地看 **两个列表是否非空** ，并不区分 Task 是否真的还在跑。

也就是说，即使某个 Task 已经完成了，只要还没被 `get_results()` 消费，它仍然在 `_pending` 里， `has_pending` 仍然返回 `True` 。

## 3.8 但这样不会误判吗

不会，因为 `has_pending` 的唯一用途是让 `query_loop` 判断 **要不要调用** `get_results()` 。

调用时机是 API 流式响应结束之后，所以：

```Plaintext
API 流结束
    ↓
has_pending == True → 调用 get_results()
    ↓
get_results() 内部 await 所有 task，收集结果，_pending 被消费完
    ↓
此后 has_pending == False（下一轮重置）
```

`_pending` 本质上不是"正在执行"的集合，而是**"已启动、结果尚未收割"的集合**。判断"有没有工具在执行"其实是判断"有没有需要 `await` 的 Task"，语义上是一致的。

> 就是一个排队逻辑，get_results()就是前面的人检票结束，轮到你了

- API不断给排队的队列➕工具
- 并发安全 → 快速通道，add_tool 时直接进场，后台自己跑
- 非并发安全 → 普通通道，先排 _queue，get_results 时检票放行，且前面的人出来才能进

## **4. 关于 orchestration.py**

`cc/tools/orchestration.py` 中有一个 `run_tools()` 函数，采用"流结束后一次性批量执行"的方案。 **当前代码中没有任何地方调用它** ——`query_loop.py:169` 只使用 `StreamingToolExecutor`。它作为未启用的备选方案存在，不需要深入了解。（StreamingToolExecutor的下位替代）

## **5. Hook 系统：PreToolUse / PostToolUse**

Hook 系统（ `cc/hooks/hook_runner.py` ）允许用户在工具执行前后运行自定义 shell 命令。

**HookConfig** （第 30-41 行）包含三个字段：`event`（"PreToolUse" 或 "PostToolUse"）、`command`（shell 命令）、`tool_name`（可选，限定只对特定工具生效）。配置来源是 `~/.claude/settings.json` 的 `hooks` 字段。

**执行协议** （`run_hook()`，第 97-155 行）：通过 `asyncio.create_subprocess_shell` 启动子进程，工具上下文以 JSON 格式通过 stdin 传入，退出码决定行为：

- `0` ：允许通过
- `2` ：阻止工具执行（仅 PreToolUse 有效），stdout 内容作为阻止原因
- 其他：警告日志但不阻止

超时保护设为 10 秒（ `HOOK_TIMEOUT_S` ，第 27 行）。超时后进程被 `proc.kill()` 强制杀死，返回空 HookResult（不阻止工具执行）。这个决策的理由是：hook 脚本的问题不应该影响正常工作流。

**PreToolUse** 的短路语义（`run_pre_tool_hooks()`，第 158-183 行）：按注册顺序依次执行所有匹配的 hook，只要有一个返回 `blocked=True` 就立即停止后续 hook 并返回阻止结果。

**PostToolUse** （`run_post_tool_hooks()`，第 186-209 行）：在工具完成后运行，无法阻止执行（结果已产生）。工具输出截断到 1000 字符传递给 hook，避免大量输出撑爆 hook 进程的内存。返回值为 None，因为 post hook 的结果不影响流程。

> 在工具真正执行之前，让你插入一段自定义的 shell 脚本来检查——决定"放行"还是"阻止"。

## 一个真实场景

> 假设你不想让模型执行 `rm` 命令：

> // ~/.claude/settings.json

> {

> "hooks": [

> {

> "event": "PreToolUse",

> "command": "python check_safety.py",

> "tool_name": "Bash"

> }

> ]

> }

# check_safety.py — 你自己写的脚本

> import json, sys

> context = json.load(sys.stdin) # 从 stdin 读取工具调用信息

> command = context["input"]["command"]

> if "rm " in command:

> print("不允许执行删除命令")

> sys.exit(2) # exit code 2 = 阻止

> else:

> sys.exit(0) # exit code 0 = 放行

## 效果：

> 帮我清理临时文件

> 模型决定调用: Bash(command="rm -rf /tmp/cache/")

> ↓

> PreToolUse Hook 启动

> stdin: {"tool_name":"Bash", "input":{"command":"rm -rf /tmp/cache/"}}

> check_safety.py 检查 → 发现有 "rm" → exit(2)

> ↓

> 工具被阻止！返回 ToolResult("Blocked by hook: 不允许执行删除命令", is_error=True)

> ↓

> 模型看到工具被阻止 → 换一种方式或者告诉用户"我不能删除文件"

## 在执行栈中的位置

> _execute_one(block)

> ↓

> Semaphore(10) 获取并发许可

> ↓

> 工具查找: registry.get("Bash") → BashTool

> ↓

> ★ PreToolUse Hook ← 在这里拦截！执行你的脚本

> ├─ exit(0) → 放行，继续往下

> └─ exit(2) → 阻止，直接返回错误，工具根本不执行

> ↓

> Permission 权限校验

> ↓

> tool.execute() 实际执行

> ↓

> PostToolUse Hook（执行完之后通知，不能阻止）

## 和 Permission 的区别

> **简单说：** Permission 是系统自带的粗粒度开关（按工具名），Hook 是用户自定义的细粒度检查（按具体参数）。

## **6. Permission Gate：权限控制**

权限系统（ `cc/permissions/gate.py` ）的核心理念是白名单模式：只有明确列入白名单的工具才能自动执行，其余一律需要用户确认。这保证了未来新增工具不会因为遗漏配置而意外执行。

**三级权限模式** （`PermissionMode`，第 39-49 行）：

- `BYPASS` ：跳过所有权限检查，所有工具自动允许（仅限受信环境）
- `ACCEPT_EDITS` ：读取 + 编辑操作自动允许，命令执行需确认（推荐的交互模式）
- `DEFAULT` ：仅读取操作自动允许，其余均需确认（最安全）

**两组白名单** （第 29-36 行）：

```Python
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep", "TaskGet", "TaskList", "ToolSearch", "Brief",
    "TaskCreate", "TaskUpdate",
})
EDIT_TOOLS = frozenset({
    "Edit", "Write", "NotebookEdit", "TodoWrite",
})
```

`check_permission()` （第 64-94 行）的判定流程：BYPASS 模式一律 ALLOW -> 只读工具一律 ALLOW -> 编辑工具在 ACCEPT_EDITS 模式下 ALLOW -> 其余返回 ASK。Bash、Agent、WebFetch 等工具不在任何白名单中，始终需要确认。

**PermissionContext** （第 97-206 行）是会话级的权限状态容器。`check()` 方法的完整流程：检查 `_always_allow` 缓存 -> 检查自定义 rules -> 执行模式检查 -> 如果 ASK 且非交互模式则直接拒绝（第 162-166 行）。非交互模式包括 `--print` 管道模式和后台 agent，这些场景无法向用户弹窗确认，宁可拒绝也不能在无人监督时执行高危操作。

`_always_allow` 缓存（第 120 行）存储用户选择 "a"（always）后的工具名。用户在交互确认时选 "a" 后，该工具在当前会话内的后续调用都会自动放行，不再弹窗。

## **7. Skills 系统：Prompt 注入而非工具执行**

Skills 系统的本质是 **Prompt 注入** ，不是工具执行。Skill 不运行代码，不修改文件，只是把一段预定义的 Markdown 文本注入到对话上下文中，让模型"临时获得"某项专业能力。

> **Skill 数据结构** （`cc/skills/loader.py` 第 22-35 行）：`name`、`description`、`prompt`（核心内容）、`trigger`（可选的触发模式）、`source_path`。

> **文件格式** ：Markdown 文件，支持可选的 YAML frontmatter。`_parse_skill_file()`（第 69-117 行）用正则 `^---\s*\n(.*?)\n---\s*\n(.*)$` 解析 frontmatter，没有 frontmatter 的文件以文件名为技能名、整个内容为 prompt。YAML 解析采用简单的逐行 `key: value` 匹配，不依赖 PyYAML 库。

> **加载路径** （`load_skills()`，第 38-66 行）：`~/.claude/skills/`（用户级，跨项目共享）和 `.claude/skills/`（项目级，随代码分发）。按字典序排列保证确定性。

> **两条触发路径** ：

> 1. **用户直接触发** ：在 REPL 层输入 `/skill-name`，REPL 将 skill 的 prompt 作为 user message 注入 transcript

> 2. **模型工具调用** ：模型调用 `SkillTool`（`cc/tools/skill/skill_tool.py`），`execute()` 查找对应 skill 并将 prompt 文本作为 `ToolResult.content` 返回

两条路径最终效果相同——skill 的 Markdown prompt 进入了对话上下文。SkillTool 的 `is_concurrency_safe` 返回 True（第 60-62 行），因为它只是内存中的字符串查找，没有任何副作用。

## **8. 20+ 工具的分类体系**

根据 `is_concurrency_safe` 的实际返回值，所有工具分为两类：

**并发安全（is_concurrency_safe = True）** ：

| 工具                      | 理由                                     |
| ------------------------- | ---------------------------------------- |
| FileReadTool (Read)       | 纯只读文件操作                           |
| GlobTool (Glob)           | 只读文件系统元数据                       |
| GrepTool (Grep)           | 只读搜索                                 |
| WebFetchTool              | HTTP 请求不修改本地状态                  |
| WebSearchTool             | 外部 API 只读调用                        |
| ToolSearchTool            | 只读注册表查询                           |
| SkillTool                 | 内存中字符串查找                         |
| BriefTool                 | 只读内部计数器                           |
| LSPTool                   | 只读语言服务查询                         |
| PlanModeTool (Enter/Exit) | 轻量级状态切换，幂等                     |
| BashTool（部分命令）      | `ls` 、 `cat` 、 `git status` 等只读命令 |

**非并发安全（is_concurrency_safe = False / 默认）** ：

| 工具                            | 理由                             |
| ------------------------------- | -------------------------------- |
| BashTool（写命令）              | 可能修改文件系统                 |
| FileEditTool (Edit)             | 写文件                           |
| FileWriteTool (Write)           | 写文件                           |
| NotebookEditTool                | 写 notebook 文件                 |
| TodoWriteTool                   | 写 todo 文件                     |
| AgentTool                       | 启动子 agent，可能产生递归写操作 |
| AskUserQuestionTool             | 需要用户交互，不能并发弹窗       |
| TaskStopTool                    | 改变任务状态                     |
| TeamCreateTool / TeamDeleteTool | 修改团队状态                     |
| SendMessageTool                 | 发送消息，有顺序语义             |

BashTool 是唯一一个动态判断并发安全性的工具。它的 `is_concurrency_safe()` 解析命令的前一到两个单词，匹配只读命令白名单（ `_READ_ONLY_SINGLE` 和 `_READ_ONLY_TWO_WORD` ）。 `git status` 返回 True， `git commit` 返回 False。空命令保守返回 False。

**为什么默认 False？** 这是整个并发控制策略的安全基石。新增工具的开发者如果忘记覆写 `is_concurrency_safe()`，工具会自动进入串行执行路径。性能损失可以通过后续优化弥补，但并发冲突导致的数据损坏是不可逆的。这种"默认安全"的设计在工具执行这种涉及文件系统副作用的场景中尤为重要。