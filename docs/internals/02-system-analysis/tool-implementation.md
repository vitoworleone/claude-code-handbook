# 05c - 每个工具的作用与实现

> 上一篇讲了工具系统的架构——注册、调度、权限。本篇逐个介绍 20+ 个工具各自做什么、核心实现逻辑、以及值得注意的设计细节。每个工具按统一格式呈现：文件位置、并发安全性、一句话功能、核心逻辑、设计亮点。

> 工具核心：

> **1、什么时候调用，怎么调用**

> **2、权限设置（hook，permisssion）**

> **3、并发安全**

![img](../assets/images/05c - 每个工具的作用与实现.PNG)

![img](../assets/images/05c - 每个工具的作用与实现1.PNG)

## 文件操作组（Tier 1，6 个）

这六个工具是整个系统的基石，启动时立即注册，不走延迟加载。它们覆盖了模型与文件系统交互的全部场景：执行命令、读文件、改文件、写文件、找文件、搜内容。

### Bash

**文件：** `cc/tools/bash/bash_tool.py` | **并发安全：** 动态判断

执行 shell 命令并返回输出。通过 `asyncio.create_subprocess_shell` 启动子进程，默认超时 2 分钟（上限 10 分钟，第 100 行硬编码 `600_000` 毫秒），输出超过 200KB 时截断（第 19 行 `MAX_OUTPUT_BYTES` ）。超时处理采用两阶段杀进程策略（第 122-128 行）：先 `proc.terminate()` （SIGTERM）给进程 2 秒优雅退出时间，未退出则 `proc.kill()` （SIGKILL）强杀。输出使用 `errors="replace"` 解码（第 135 行），确保二进制输出不触发 `UnicodeDecodeError` 。

**设计亮点 — 动态并发安全判断（第 76-95 行）：** `is_concurrency_safe` 不是返回固定值，而是解析命令的前一到两个单词来判断。单词命令白名单 `_READ_ONLY_SINGLE` （第 25-29 行）包含 `ls` 、 `cat` 、 `git status` 等 20+ 个只读命令；双词命令白名单 `_READ_ONLY_TWO_WORD` （第 33-36 行）覆盖 `git status` 、 `git log` 、 `git diff` 等 git 查询子命令。只有命中白名单才返回 `True` ，其余保守返回 `False` 。这使得多个 `git status` 和 `ls` 可以并行执行，而 `rm` 、 `git commit` 则必须串行。

### Read

**文件：** `cc/tools/file_read/file_read_tool.py` | **并发安全：** 始终 `True` （第 69 行）

读取文件内容。处理两种文件类型：图片文件（ `.png` 、 `.jpg` 等，第 30 行 `IMAGE_EXTENSIONS` ）以 base64 编码返回富内容块（第 93-104 行），模型通过多模态能力直接"看到"图片；文本文件以 `cat -n` 格式返回带行号的内容（第 132-133 行），默认读取前 2000 行（第 28 行 `DEFAULT_LIMIT` ），支持 `offset` / `limit` 分页参数。

**设计亮点 — 行号是绝对的：** 无论 `offset` 参数是多少，行号始终反映文件中的实际位置（第 132 行 `start=start_idx + 1` ）。这使得模型看到的行号可以直接用于后续 Edit 操作定位，不需要心算偏移量。

### Edit

**文件：** `cc/tools/file_edit/file_edit_tool.py` | **并发安全：** `False` （默认）

通过精确字符串替换修改文件。核心逻辑：在文件内容中查找 `old_string` ，替换为 `new_string` 。如果 `old_string` 出现多次且未指定 `replace_all=True` ，报错要求消歧（第 103-110 行）——这是关键安全机制，防止模型在不了解全部上下文的情况下意外修改了文件中其他位置的相同文本。替换完成后返回 unified diff 格式的变更摘要（第 130-133 行）。

**设计亮点 — 二进制读保留行尾（第 89-92 行）：** 使用 `path.read_bytes()` + 手动 `decode("utf-8")` 而非 `read_text()` 。原因是 Python 的 `read_text()` 在某些平台上启用 universal newlines，会将 CRLF 静默转为 LF，导致写回时行尾被意外修改。二进制读 + 手动解码精确保留原始行尾字符，写回时同样使用 `write_bytes` （第 123 行）。

### Write

**文件：** `cc/tools/file_write/file_write_tool.py` | **并发安全：** `False` （默认）

创建新文件或完全覆盖已有文件。自动创建父目录（第 71 行 `mkdir(parents=True, exist_ok=True)` ），返回写入的行数（第 103 行）。

**设计亮点 — 原子写入（第 83-96 行）：** 先在目标文件的同一目录下创建临时文件（ `tempfile.mkstemp` ），写入内容后用 `os.replace()` 原子性替换目标文件。 `os.replace` 在 POSIX 上是单次 `rename` 系统调用，保证原子性——要么旧文件完整，要么新文件完整，写入过程中断电不会产生半截的损坏文件。临时文件放在同一目录是因为 `os.replace` 要求源和目标在同一文件系统。写入失败时在 `except` 块中清理临时文件（第 94-95 行）。

### Glob

**文件：** `cc/tools/glob_tool/glob_tool.py` | **并发安全：** 始终 `True` （第 51 行）

通过 glob 模式匹配文件路径。使用 `pathlib.glob` （第 67 行），结果按修改时间降序排列（ `key=lambda p: p.stat().st_mtime, reverse=True` ），让模型优先看到最近修改的文件。只保留文件、排除目录（第 69 行），截断到 100 个结果（第 15 行 `MAX_RESULTS` ）。

**设计亮点 — 按修改时间排序：** 同类工具通常按字母序排列，而这里按修改时间降序。这个决策基于实际使用模式——模型通常对最近修改过的文件更感兴趣（比如刚编辑过的代码文件、刚生成的日志）。

### Grep

**文件：** `cc/tools/grep_tool/grep_tool.py` | **并发安全：** 始终 `True` （第 61 行）

在文件内容中搜索正则表达式。支持三种输出模式： `content` （匹配行及内容）、 `files_with_matches` （仅文件路径，默认）、 `count` （每文件匹配数）。结果截断到 250 行（第 18 行 `DEFAULT_HEAD_LIMIT` ）。

**设计亮点 — 双后端策略（第 76-80 行）：** 优先使用 ripgrep（ `shutil.which("rg")` ），不可用时回退到纯 Python `re` 模块。ripgrep 比 Python 遍历文件快 10-100 倍且自动尊重 `.gitignore` ，但它是外部依赖，不能保证所有环境都安装了。Python 回退保证了基线可用性。ripgrep 通过 `asyncio.create_subprocess_exec` 调用（第 108 行），30 秒超时（第 114 行）；Python 回退则在到达 `head_limit` 时提前退出遍历（第 197-198 行），避免在大仓库中浪费 IO。

## 2. Web 组（2 个）

### WebFetch

**文件：** `cc/tools/web_fetch/web_fetch_tool.py` | **并发安全：** 始终 `True` （第 49 行）

抓取 URL 内容并返回文本或 Markdown。使用 `httpx.AsyncClient` （延迟导入，第 57 行）， `follow_redirects=True` 自动跟随重定向，30 秒超时（第 62 行）。内容超过 100KB 时截断（第 17 行 `MAX_CONTENT_BYTES` ）。 HTML 内容通过 `markdownify`  库转换为 Markdown 格式（第 80 行），去除 script/style 标签，让模型更易提取有用信息。 `markdownify`  是可选依赖，未安装时直接返回原始 HTML（第 82 行）。

**设计亮点 — 延迟导入 httpx：** 在 `execute` 方法内部而非模块顶层导入 `httpx` 。大多数对话不需要抓网页，延迟导入避免了每次启动时加载 httpx 及其依赖链的开销。

### WebSearch

**文件：** `cc/tools/web_search/web_search_tool.py` | **并发安全：** 始终 `True` （第 55 行）

网页搜索。当前为 stub 实现（第 64 行），返回配置提示要求设置 `BRAVE_API_KEY` 或 `SERPAPI_KEY` 环境变量。接口已定义完整（ `query` 和 `max_results` 参数），后续实现只需在 `execute` 中调用搜索 API 即可。

## 3. 交互组（1 个）

### AskUserQuestion

**文件：** `cc/tools/ask_user/ask_user_tool.py` | **并发安全：** `False` （默认）

暂停执行，向用户提问并等待回答。通过构造函数注入 `input_fn` （第 26-29 行）解耦输入来源：REPL 模式注入实际输入函数， `--print` 管道模式和子 Agent 中 `input_fn` 为 `None` ，此时返回 `"(Cannot ask user in non-interactive mode)"` （第 57 行）。交互模式下使用 `rich.Console` 美化输出（第 63-65 行），Ctrl+C 和 EOF 被优雅捕获而非崩溃（第 67-69 行）。

**设计亮点 — 依赖注入实现多模式复用：** 同一个工具类同时服务于交互式 REPL 和非交互式管道模式，通过构造时注入的 `input_fn` 决定行为，无需两套实现。

## 4. 任务管理组（5 个）

**文件：** `cc/tools/task_tools/task_tools.py`

五个工具共享一个模块级 `TaskStore` 单例（第 82 行 `_store = TaskStore()` ），内存 `dict[str, Task]` 存储，生命周期与会话绑定。 `Task` 是 dataclass（第 23-33 行），包含 `id` 、 `subject` 、 `description` 、 `status` 四个字段，状态机为 `pending` -> `in_progress` -> `completed` / `stopped` 。

### TaskCreate

**并发安全：** `False` | 创建新任务，用 `uuid4()[:8]` 生成 8 位短 ID（第 49 行），初始化为 `pending` 状态。返回格式 `"Task #{id} created: {subject}"` 。

### TaskGet

**并发安全：** `False` | 按 ID 查询任务详情，返回 JSON 格式（第 150-155 行）便于模型结构化解析。

### TaskList

**并发安全：** `False` | 列出所有任务，输出紧凑的单行摘要格式 `"#{id} [{status}] {subject}"` （第 180 行），节省上下文空间。

### TaskUpdate

**并发安全：** `False` | 更新任务属性。支持部分更新——提取除 `taskId` 之外的所有非 `None` 字段（第 211 行），通过 `setattr` 动态更新（第 66 行）。

### TaskStop

**并发安全：** `False` | 停止运行中的任务。支持两种任务来源的停止操作（第 253-269 行）：先在内存 `TaskStore` 中查找并标记为 `stopped` ，未找到时再查 `_task_registry` （后台 Agent 管理器，由 `main.py` 运行时注入到 `self._task_registry` ，第 235 行）并取消其 `asyncio.Task` 。这种"瀑布式"查找策略兼容了内存任务和后台 Agent 两种来源。

## 5. 开发辅助组（5 个）

### NotebookEdit

**文件：** `cc/tools/notebook/notebook_edit_tool.py` | **并发安全：** `False` （第 99 行）

编辑 Jupyter notebook（ `.ipynb` ）文件。支持三种操作： `insert_cell` 、 `replace_cell` 、 `delete_cell` （第 20 行 `VALID_COMMANDS` ）。直接操作 `.ipynb` 的 JSON 结构，无需启动 Jupyter 内核。 `_make_cell` 辅助函数（第 25-43 行）构造符合 nbformat v4 规范的 cell 字典，source 按行分割并保留行尾换行符（ `splitlines(keepends=True)` ，第 37 行）。

**设计亮点 — 自动创建空 notebook（第 123-138 行）：** 当文件不存在且操作为 `insert_cell` 时，自动创建一个符合 nbformat v4 规范的空 notebook（含 `kernelspec` 、 `language_info` 等完整 metadata），避免用户需要先手动创建文件。 `insert_cell` 的索引会 clamp 到 `[0, len(cells)]` （第 171 行），越界不报错而是插在头/尾。

### ToolSearch

**文件：** `cc/tools/tool_search/tool_search_tool.py` | **并发安全：** 始终 `True` （第 60 行）

搜索已注册的工具。接受 `ToolRegistry` 实例（第 31 行），遍历所有工具，按关键词评分：名称匹配 +10、描述匹配 +5、精确名称匹配 +50（第 86-94 行），按分数降序返回最多 5 个结果。

**设计亮点 — 为 deferred tools 而生：** 在 deferred tools（延迟加载工具）场景下，模型只知道工具名称而没有完整 schema。通过 ToolSearch 搜索关键词，模型可以发现需要的工具并获取其描述信息，再决定是否调用。

### Skill

**文件：** `cc/tools/skill/skill_tool.py` | **并发安全：** 始终 `True` （第 61 行）

加载技能（Skill）的 prompt 文本。通过 `get_skill_by_name` （第 70 行）按名称查找技能，找到后将技能的 prompt 文本作为 `ToolResult` 返回（第 85 行）。如果有 `args` 参数，追加到 prompt 末尾（第 82-83 行）。未找到时列出所有可用技能帮助模型自纠错（第 73 行）。

**设计亮点 — 本质是 prompt 注入：** Skill 工具的返回值不是执行结果，而是一段指令文本。模型调用此工具后，技能的 prompt 会作为 `tool_result` 出现在对话历史中，模型在后续回复中会"遵守"这些指令。这是一种优雅的运行时 prompt 扩展机制。

### LSP

**文件：** `cc/tools/lsp/lsp_tool.py` | **并发安全：** 始终 `True` （第 73 行）

语言服务器协议（LSP）集成。当前为 stub 实现，返回配置提示（第 90-95 行）。接口定义完整，支持四种操作： `diagnostics` （编译错误/警告）、 `hover` （类型信息/文档）、 `definition` （跳转定义）、 `references` （查找引用）。 `character` 参数非必填（第 68 行），因为 `diagnostics` 操作只需要文件路径和行号。

### TodoWrite

**文件：** `cc/tools/todo/todo_write_tool.py` | **并发安全：** `False` （默认）

项目级持久化待办列表。与 TaskTools 的内存存储不同，TodoWrite 将数据写入磁盘 `~/.claude/todos/{project_hash}.json` （第 41 行），跨会话持久。项目路径通过 SHA-256 哈希生成 16 位标识符（第 31-32 行 `_get_project_hash` ），避免路径中的特殊字符导致文件名问题。

**设计亮点 — 全量覆盖策略（第 133 行）：** 每次调用写入完整的 todo 列表，而非增量修改。这简化了并发和一致性问题——不需要处理"两个 agent 同时添加 todo"的竞态条件，由模型自己负责维护列表的完整性。

## 6. 模式切换组（3 个）

### EnterPlanMode / ExitPlanMode

**文件：** `cc/tools/plan_mode/plan_mode_tool.py` | **并发安全：** 始终 `True` （第 63 行、第 90 行）

切换计划模式（Plan Mode）的全局标志。 `EnterPlanMode` 将模块级变量 `_plan_mode_active` 设为 `True` （第 68 行）， `ExitPlanMode` 设为 `False` （第 94 行）。操作是幂等的——重复进入/退出不会报错。引擎通过 `is_plan_mode_active()` （第 28-32 行）查询当前状态，决定是否允许执行写操作。

**设计亮点 — 最简实现的全局开关：** 用一个模块级布尔变量实现模式切换，两个工具类各自只做一件事（设 `True` / 设 `False` ）。在当前单进程架构下这足够了，后续迁移到正式会话状态管理时，只需修改 `_plan_mode_active` 的存储位置。

### Brief

**文件：** `cc/tools/brief/brief_tool.py` | **并发安全：** 始终 `True` （第 54 行）

返回当前对话的统计摘要。消息计数由引擎通过 `update_message_count()` 注入（第 57-60 行），而非让工具直接访问对话历史（保持解耦）。根据消息数量分级给出建议（第 76-84 行）：0 条提示"无消息"，<5 条为"短对话"，<20 条为"中等"，>=20 条建议使用 `/compact` 压缩上下文。

## 7. 协作组（Tier 3，3 个）

这三个工具构成了 swarm（多智能体协作）的通信基础设施。它们需要 `team_context` 等运行时依赖，在 `_build_engine()` 中完成二次布线。

### TeamCreate

**文件：** `cc/tools/team/team_create_tool.py` | **并发安全：** `False` （默认）

创建新团队。为 `team_lead` （主协调者）生成唯一标识符（ `format_agent_id(TEAM_LEAD_NAME, team_name)` ，第 80 行），构造 `TeamFile` 数据结构（第 84-99 行），通过 `save_team_file` 持久化为 JSON 文件（第 102 行），然后激活会话级 `team_context` （第 106 行 `team_context.enter_team()` ），使 `SendMessage` 等工具能感知团队状态。

### TeamDelete

**文件：** `cc/tools/team/team_delete_tool.py` | **并发安全：** `False` （默认）

解散团队。在删除前执行安全检查：遍历团队成员，排除 `team_lead` 后检查是否存在活跃成员（第 71-84 行 `m.is_active` ），如有则拒绝删除并列出活跃成员名单。通过后使用 `shutil.rmtree` （第 90 行）递归删除整个团队目录（包含团队文件、邮箱文件等），最后清除会话级 `team_context` （第 98 行 `leave_team()` ）。

**设计亮点 — 防止误删活跃团队：** 强制要求先通过 `requestShutdown` 终止所有队友，再执行清理。这避免了正在执行任务的队友因团队目录被删除而意外崩溃。

### SendMessage

**文件：** `cc/tools/send_message/send_message_tool.py` | **并发安全：** `False` （默认）

向队友发送消息。支持两种模式：点对点（ `to="agent_name"` ）和广播（ `to="*"` ）。消息通过 `TeammateMailbox` 写入文件系统中的收件箱文件（第 111 行），基于文件而非内存，实现了跨进程的异步消息传递。广播时加载团队文件获取成员列表（第 150 行），排除发送者自己（第 161 行，大小写不敏感比较），逐一写入每个队友的收件箱。

**设计亮点 — 团队名多来源解析（第 98-108 行）：** 团队名优先使用构造时显式指定的值，其次从运行时 `team_context` 动态获取，都不可用时报错。这种优先级链条支持了不同的初始化场景（直接构造 vs 由引擎装配）。

## 8. Agent 组（Tier 3，1 个）

### Agent

**文件：** `cc/tools/agent/agent_tool.py` | **并发安全：** `False` （默认）

生成子 Agent 来执行复杂任务。支持三种运行模式（第 12-17 行注释）：前台模式（默认，阻塞父 Agent 等待完成，第 226-260 行）、后台模式（ `run_in_background=True` ，立即返回 `task_id` ，第 194-221 行）、worktree 隔离模式（ `isolation="worktree"` ，在 git worktree 中执行，第 147-163 行）。子 Agent 继承父 Agent 的工具集但排除 `AgentTool` 自身（第 174 行，防止无限递归），后台模式额外排除交互式工具（第 172 行）。前台模式限制最大 30 轮（第 230 行 `max_turns=30` ），遇到 `end_turn` 停止原因时提前终止（第 241-242 行）。

由于 Agent 工具的实现涉及 `query_loop` 的递归调用、 `BackgroundAgentManager` 的生命周期管理、worktree 的创建与清理等多个子系统，详细分析见下一篇 05d。

## 并发安全性总结

下表汇总所有工具的 `is_concurrency_safe` 返回值。 `StreamingToolExecutor` 根据此值决定哪些工具可以并行执行，哪些必须串行等待。

| 分类     | 工具名          | safe? | 判断方式                                |
| -------- | --------------- | ----- | --------------------------------------- |
| 文件操作 | Bash            | 动态  | 解析命令前缀，只读命令 True，其余 False |
| 文件操作 | Read            | true  | 始终安全（纯只读）                      |
| 文件操作 | Edit            | false | 写文件，可能与其他读写冲突              |
| 文件操作 | Write           | false | 写文件                                  |
| 文件操作 | Glob            | true  | 只读文件系统元数据                      |
| 文件操作 | Grep            | true  | 只读文件内容                            |
| Web      | WebFetch        | true  | HTTP 请求不修改本地状态                 |
| Web      | WebSearch       | true  | 外部 API 调用                           |
| 交互     | AskUserQuestion | false | 阻塞等待用户输入                        |
| 任务管理 | TaskCreate      | false | 写内存状态                              |
| 任务管理 | TaskGet         | false | 默认值（可优化为 True）                 |
| 任务管理 | TaskList        | false | 默认值（可优化为 True）                 |
| 任务管理 | TaskUpdate      | false | 写内存状态                              |
| 任务管理 | TaskStop        | false | 写内存状态 + 可能取消协程               |
| 开发辅助 | NotebookEdit    | false | 写文件                                  |
| 开发辅助 | ToolSearch      | true  | 只读注册表                              |
| 开发辅助 | Skill           | true  | 只读 prompt 文本                        |
| 开发辅助 | LSP             | true  | 只读查询（当前为 stub）                 |
| 开发辅助 | TodoWrite       | false | 写磁盘文件                              |
| 模式切换 | EnterPlanMode   | true  | 幂等写全局标志                          |
| 模式切换 | ExitPlanMode    | true  | 幂等写全局标志                          |
| 模式切换 | Brief           | true  | 只读内部计数器                          |
| 协作     | TeamCreate      | false | 写磁盘 + 修改 team_context              |
| 协作     | TeamDelete      | false | 删除目录 + 修改 team_context            |
| 协作     | SendMessage     | false | 写邮箱文件                              |
| Agent    | Agent           | false | 启动子进程 / 后台协程                   |

规律很清晰：所有只读操作（ `Read` 、 `Glob` 、 `Grep` 、 `WebFetch` 、 `ToolSearch` 、 `Skill` ）为 `True` ，所有写操作（ `Edit` 、 `Write` 、 `NotebookEdit` 、 `TodoWrite` 、Team 系列）为 `False` ， `Bash` 是唯一一个根据输入动态判断的工具。 `PlanMode` 虽然写了全局变量但标记为 `True` ，因为布尔赋值是原子操作且具有幂等性。 `TaskGet` / `TaskList` 目前使用默认值 `False` ，理论上可以优化为 `True` （纯读操作），但在当前系统中任务工具调用频率不高，这点优化收益不大。