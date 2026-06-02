# 12 Agent Teams

到目前为止我们看到的都是单个 Agent 的循环——一个 query_loop 处理一个用户请求。但有些任务太大，需要多个 Agent 并行工作。Agent Teams 就是 Claude Code 的多 Agent 协作框架。本章讲清楚它是怎么触发的、内部怎么实现、数据怎么流转。

全文以一个贯穿例子来说明：用户输入"帮我重构这个项目的测试"，Claude 决定创建一个团队来并行处理。

![img](../assets/images/12 Agent Teams.PNG)

## 什么时候触发——从单 Agent 到多 Agent

默认情况下，Claude Code 是单 Agent 模式。用户输入一条请求， `_run_repl()` 调用 `engine.run_turn()` ，驱动一次 `query_loop()` ，循环结束就是一轮完整交互。不涉及任何团队。

多 Agent 模式有两个触发入口。

**入口 A：模型主动调用 TeamCreate 工具**

这是最常见的路径。模型在分析用户请求后，判断任务足够复杂（比如"重构项目测试"涉及多个目录、多种测试框架），于是调用 TeamCreate 工具创建团队。创建成功后，TeamContext 被激活，后续模型就可以通过 AgentTool 的 `spawn_teammate()` 派生队友。

**入口 B：设置环境变量** `CLAUDE_CODE_COORDINATOR_MODE=1`

这条路径不创建 TeamFile，而是在 system prompt 前面注入一段 Coordinator 指令（ `cc/swarm/coordinator.py:36-56` ）。注入后，模型会按照 Coordinator prompt 的四阶段工作流（Research → Synthesis → Implementation → Verification）来编排任务，使用 AgentTool 派生 worker。这条路径更像"prompt 驱动的编排"，详见第 7 节。

**TeamContext——团队状态开关**

两条入口最终都依赖 TeamContext 这个类（ `cc/swarm/team_context.py` ）。它的实现极其简单——只有一个 `_team_name` 字段：

- `_team_name = None` → `is_active = False` → 团队功能关闭
- `_team_name = "refactor-team"` → `is_active = True` → 团队功能开启

TeamContext 是会话级状态容器，由 `_build_engine()` 在 Step 4 创建，初始为空（ `cc/main.py:394-396` ），通过二次布线注入到 TeamCreateTool、TeamDeleteTool 和 SendMessageTool 中（ `cc/main.py:429-445` ）。

TeamContext 激活后影响三件事：

1. SendMessageTool 变为可用（能从 team_context 动态获取 team_name）
2. REPL 主循环中的 inbox 轮询逻辑被触发（ `cc/main.py:741-759` ）
3. AgentTool 调用 `spawn_teammate()` 时能正确注册到团队

## 2. 三个角色

Agent Teams 中有三种角色，它们的区别在于 system prompt 和可用工具集。

| 角色        | 身份标识                                         | system_prompt                            | 工具集                                                |
| ----------- | ------------------------------------------------ | ---------------------------------------- | ----------------------------------------------------- |
| Team Lead   | "team-lead@{team}"                               | 完整版（11 段拼装）                      | 全部工具                                              |
| Teammate    | "{name}@{team}"（如 "researcher@refactor-team"） | DEFAULT_AGENT_PROMPT + TEAMMATE_ADDENDUM | 排除 Agent/TeamCreate/TeamDelete/AskUserQuestion      |
| Coordinator | 与 Lead 相同                                     | 前置 COORDINATOR_SYSTEM_PROMPT + 完整版  | 全部（但 prompt 指导只用 Agent/SendMessage/TaskStop） |

**Team Lead** 就是主 Agent 本身。当模型调用 TeamCreate 后，当前会话的 Agent 自动成为 Team Lead，身份标识为 `"team-lead@{team_name}"` 。它拥有完整的工具集和 system prompt，负责分配任务、接收汇报、汇总结果。 `TEAM_LEAD_NAME` 是一个固定常量 `"team-lead"` （ `cc/swarm/identity.py:17` ）。

**Teammate** 是被 Lead 派生出来的子 Agent。它的 system prompt 由两部分拼接而成： `DEFAULT_AGENT_PROMPT` （约 60 字的通用 agent 行为指令， `cc/prompts/sections.py:35` ）加上 `build_teammate_prompt_addendum()` 生成的附加段落（ `cc/prompts/teammate_prompt.py:36-81` ）。附加段落包含通信规则、身份信息、团队协作规范和任务生命周期说明。它的工具集通过过滤机制排除了四个工具：Agent（防递归）、TeamCreate/TeamDelete（只有 Lead 能管理团队生命周期）、AskUserQuestion（后台无法交互）。

**Coordinator** 是一种可选的增强模式。它不改变 Agent 的运行时结构，只是在 system prompt 前面追加了一大段编排指令（约 180 行， `cc/prompts/coordinator_prompt.py:32-181` ），教模型如何扮演协调者角色。

## 3. 团队的生命周期

这是本章的核心。用"帮我重构这个项目的测试"这个例子，完整走一遍从创建到销毁的 5 个步骤。

### Step 1：TeamCreate——创建团队

模型分析用户请求后，决定创建团队：

```Plaintext
Tool call: TeamCreate(team_name="refactor-team", description="重构项目测试")
```

`TeamCreateTool.execute()` （ `cc/tools/team/team_create_tool.py:60-114` ）执行以下操作：

**1.1 检查团队是否已存在**

调用 `load_team_file("refactor-team")` （ `cc/swarm/team_file.py:151-168` ）读取 `~/.claude/teams/refactor-team/config.json` 。如果文件存在，返回错误 `"Team 'refactor-team' already exists."` 。团队名称在系统中必须唯一。

**1.2 生成 Lead 的 agent_id**

调用 `format_agent_id(TEAM_LEAD_NAME, "refactor-team")` （ `cc/swarm/identity.py:55-61` ），返回 `"team-lead@refactor-team"` 。这个格式类似 email 地址， `@` 前面是 agent 名称，后面是团队名称。

**1.3 构造 TeamFile 数据结构**

```Python
team = TeamFile(
    name="refactor-team",
    description="重构项目测试",
    created_at=time.time(),
    lead_agent_id="team-lead@refactor-team",
    members=[
        TeamMember(
            agent_id="team-lead@refactor-team",
            name="team-lead",
            agent_type="team-lead",
            joined_at=time.time(),
            cwd=str(Path.cwd()),  # 记录 Lead 的工作目录
        )
    ],
)
```

初始成员列表只有 Lead 自己。TeamFile 和 TeamMember 是两个 dataclass（ `cc/swarm/team_file.py:83-133` 和 `cc/swarm/team_file.py:29-80` ），它们的 `to_dict()` 方法使用 camelCase 作为 JSON key（如 `leadAgentId` 、 `isActive` ），与 TS 版本的数据格式兼容。

**1.4 持久化到磁盘**

`save_team_file(team)` （ `cc/swarm/team_file.py:171-186` ）将 TeamFile 写入 `~/.claude/teams/refactor-team/config.json` 。团队目录通过 `_get_team_dir()` 计算，其中团队名称会经过 `sanitize_name()` 处理（非字母数字字符替换为连字符并转小写， `cc/swarm/identity.py:64-73` ）。

**1.5 激活 TeamContext**

`self._team_context.enter_team("refactor-team")` （ `cc/swarm/team_context.py:46-50` ）将 `_team_name` 设为 `"refactor-team"` ， `is_active` 变为 `True` 。从这一刻起，SendMessage 工具、inbox 轮询、teammate spawn 全部可用。

TeamCreate 返回的 ToolResult 包含 `team_name` 、 `team_file_path` 和 `lead_agent_id` ，模型据此知道团队已就绪。

### Step 2：Spawn Teammates——派生队友

团队创建后，模型决定派生两个队友并行工作：

```Plaintext
Tool call: Agent(prompt="分析src/目录下所有测试文件的代码质量，找出需要重构的部分", run_in_background=True)
Tool call: Agent(prompt="分析tests/目录的测试覆盖率，找出缺失的测试用例", run_in_background=True)
```

AgentTool 的 `execute()` 方法（ `cc/tools/agent/agent_tool.py:129-264` ）处理这两个请求。但当 TeamContext 处于激活状态时，AgentTool 会走 `spawn_teammate()` 路径（ `cc/swarm/spawn.py:35-125` ）。整个 spawn 流程分三步：

**2.1 注册到 TeamFile**

`spawn_teammate()` 首先构造一个 TeamMember：

```Python
member = TeamMember(
    agent_id="researcher@refactor-team",  # format_agent_id(agent_name, team_name)
    name="researcher",
    joined_at=time.time(),
    is_active=True,
)
```

然后调用 `add_member("refactor-team", member)` （ `cc/swarm/team_file.py:189-209` ）。 `add_member()` 执行读-追加-写操作：先 `load_team_file()` 读取当前 config.json，检查是否有重复的 `agent_id` （防止 teammate 异常重启后重复注册），追加新成员，再 `save_team_file()` 写回。

**2.2 创建 InProcessTeammate 并启动后台任务**

```Python
teammate = InProcessTeammate(
    agent_id="researcher@refactor-team",
    team_name="refactor-team",
    agent_name="researcher",
    call_model_factory=call_model_factory,
    parent_registry=parent_registry,
    claude_dir=None,
)
task = asyncio.create_task(teammate.run(prompt))
```

`asyncio.create_task()`  将 teammate 的执行注册为一个后台协程 （ `cc/swarm/spawn.py:91` ）。这意味着 teammate 与主 Agent 并发运行在同一个事件循环中，不阻塞 REPL 主循环。

**2.3 注册到 TaskRegistry 并设置完成回调**

生成唯一的 `task_id` （如 `"teammate-a1b2c3d4"` ， `cc/swarm/spawn.py:59` ），将 task 存入模块级的 `_running_tasks` 字典。如果 `task_registry` 可用，还会注册到 TaskRegistry 中供统一管理。

`_on_done` 回调函数（ `cc/swarm/spawn.py:106-121` ）在 task 完成时自动执行：从 `_running_tasks` 中移除、更新 TaskRegistry 状态（COMPLETED 或 FAILED）、记录异常日志。

### Step 3：Teammate 内部执行

InProcessTeammate 是 teammate 的核心执行器（ `cc/swarm/in_process_runner.py:54-239` ）。 `run()` 方法启动后，经历以下 5 步：

**3.1 设置 contextvars 身份**

```Python
token_id = _teammate_agent_id.set("researcher@refactor-team")
token_team = _teammate_team_name.set("refactor-team")
token_name = _teammate_agent_name.set("researcher")
```

（ `cc/swarm/in_process_runner.py:94-96` ）

三个 contextvars 在模块顶层定义（ `cc/swarm/in_process_runner.py:27-35` ），默认值都是 `None` 。 `set()` 返回的 token 会在 finally 块中用于精确重置。为什么用 contextvars 而不是实例变量？因为多个 teammate 以 `asyncio.Task` 形式并发运行在同一线程中，contextvars 能保证每个协程任务拥有独立的上下文副本（详见第 5 节）。

**3.2 构建子工具注册表**

`_execute_with_query_loop()` 方法（ `cc/swarm/in_process_runner.py:114-214` ）首先遍历 parent_registry 的所有工具，过滤掉四个：

```Python
for tool in self._parent_registry.list_tools():
    if tool.get_name() in ("Agent", "AskUserQuestion", "TeamCreate", "TeamDelete"):
        continue
    if tool.get_name() == "SendMessage":
        # 特殊处理：注入 teammate 身份
        child_registry.register(SendMessageTool(
            team_name=self.team_name,
            sender_name=self.agent_name,
        ))
        continue
    child_registry.register(tool)
```

（ `cc/swarm/in_process_runner.py:139-152` ）

关键点：SendMessage 工具被替换为一个新实例，构造时注入了 `team_name="refactor-team"` 和 `sender_name="researcher"` 。这样 teammate 调用 SendMessage 时，消息会自动携带正确的发件人身份，无需模型手动指定。

**3.3 组装 system prompt**

```Python
teammate_addendum = build_teammate_prompt_addendum("refactor-team", "researcher")
system_prompt = f"{DEFAULT_AGENT_PROMPT}\n\n{teammate_addendum}"
```

（ `cc/swarm/in_process_runner.py:157-158` ）

`build_teammate_prompt_addendum()` （ `cc/prompts/teammate_prompt.py:36-81` ）生成的附加段落包含四部分：

**英文原文**

```Plaintext
Agent Teammate Communication: You MUST use SendMessage to communicate with other agents.
  Plain text responses are not visible to other agents.

Your Identity: You are "researcher", a member of team "refactor-team".
  Report to team-lead.

Team Context: You are working as part of a team. Follow team collaboration norms.
  Report results to team-lead when your task is complete.

Task Lifecycle: Receive task → Execute autonomously → Report results → Wait for next task.
```

**中文翻译**

```Plaintext
Agent 队友通信：你必须使用 SendMessage 与其他 agent 通信。纯文本回复对其他 agent 不可见。

你的身份：你是"researcher"，是团队"refactor-team"的成员。向 team-lead 汇报。

团队上下文：你作为团队的一部分工作。遵守团队协作规范。任务完成时将结果汇报给 team-lead。

任务生命周期：接收任务 → 自主执行 → 汇报结果 → 等待下一个任务。
```

**3.4 运行 query_loop 独立循环**

```Python
perm_ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)
messages: list[Message] = [UserMessage(content=task)]

async for event in query_loop(
    messages=messages,
    system_prompt=system_prompt,
    tools=child_registry,
    call_model=call_model,
    max_turns=30,
    permission_checker=_perm_check,
):
    if isinstance(event, TextDelta):
        output_parts.append(event.text)
    elif isinstance(event, TurnComplete) and event.stop_reason == "end_turn":
        break
```

（ `cc/swarm/in_process_runner.py:162-189` ）

几个关键参数：

- `is_interactive=False` ：teammate 运行在后台，遇到需要用户确认的操作时直接失败（fail-fast）
- `max_turns=30` ：限制最大轮次，防止 teammate 陷入无限循环
- `messages` 只有一条 UserMessage，就是 spawn 时传入的 task prompt
- `call_model` 通过 factory 创建，与主 Agent 使用同一个 Anthropic 客户端，但拥有独立的对话上下文

query_loop 的执行过程与主 Agent 完全一致——调用 API、接收流式事件、执行工具、拼回结果——只是运行在一个独立的 asyncio.Task 中。

**3.5 通过 mailbox 将结果发送给 Team Lead**

query_loop 结束后，teammate 将收集到的文本输出通过 mailbox 发送给 Lead：

```Python
mailbox = TeammateMailbox(self.team_name, claude_dir=self._claude_dir)
mailbox.send(
    TEAM_LEAD_NAME,
    TeammateMessage(
        from_name=self.agent_name,
        text=result_text,
        timestamp=time.time(),
        summary=f"{self.agent_name} completed",
    ),
)
```

（ `cc/swarm/in_process_runner.py:201-212` ）

`mailbox.send()` 将消息写入 `~/.claude/teams/refactor-team/inboxes/team-lead.json` 。注意 `TEAM_LEAD_NAME` 是固定常量 `"team-lead"` ，所有 teammate 的结果都发送到同一个收件箱。

即使 query_loop 执行失败（抛异常），错误信息也会被捕获并作为消息内容发送（ `"(Error: {e})"` ），确保 Lead 总能收到回应。

### Step 4：Lead 收到结果——inbox 轮询

REPL 主循环的每次迭代中，在进入 `engine.run_turn()` 之前，会检查 Lead 的收件箱（ `cc/main.py:741-759` ）：

```Python
if hasattr(engine, '_team_context') and engine._team_context.is_active:
    from cc.swarm.identity import TEAM_LEAD_NAME
    from cc.swarm.mailbox import TeammateMailbox

    try:
        mailbox = TeammateMailbox(engine._team_context.team_name)
        inbox = mailbox.receive(TEAM_LEAD_NAME)
        if inbox:
            mailbox.mark_all_read(TEAM_LEAD_NAME)
            for msg in inbox:
                notification = f"<task-notification>\n[From {msg.from_name}]: {msg.text}\n</task-notification>"
                messages.append(UserMessage(content=notification))
                console.print(f"[dim]Received message from {msg.from_name}[/]")
    except Exception as e:
        logger.debug("Inbox poll failed: %s", e)
```

轮询逻辑的工作方式：

1. 检查 `team_context.is_active` ——如果团队未激活，跳过
2. 创建 `TeammateMailbox("refactor-team")`
3. 调用 `mailbox.receive("team-lead")` ，读取 `~/.claude/teams/refactor-team/inboxes/team-lead.json` 中所有 `read=False` 的消息
4. 如果有未读消息，先 `mark_all_read()` 标记已读（防止下次重复处理）
5. 将每条消息包装为 `<task-notification>` 格式的 UserMessage，注入到 transcript 的 `messages` 列表中

注入格式如下：

**英文原文（注入到 transcript 的消息格式）**

```XML
<task-notification>
[From researcher]: Analysis complete. Found 3 test files in src/ that need refactoring...
</task-notification>
```

**中文翻译**

```XML
<task-notification>
[来自 researcher]：分析完成。发现 src/ 下有 3 个测试文件需要重构……
</task-notification>
```

这样，当 `engine.run_turn()` 驱动下一次 query_loop 时，模型就能在 transcript 中看到 teammate 的汇报，综合两个 teammate 的报告，做出下一步决策——比如开始实际的重构操作。

### Step 5：TeamDelete——销毁团队

当所有任务完成后，模型调用 TeamDelete 清理：

```Plaintext
Tool call: TeamDelete(team_name="refactor-team")
```

`TeamDeleteTool.execute()` （ `cc/tools/team/team_delete_tool.py:48-106` ）执行以下操作：

**5.1 检查团队存在性**

`load_team_file("refactor-team")` ——如果返回 None，报错。

**5.2 安全检查——拒绝删除有活跃成员的团队**

```Python
non_lead = [m for m in team.members if m.name != TEAM_LEAD_NAME]
active = [m for m in non_lead if m.is_active]
if active:
    # 返回错误，列出仍然活跃的成员名称
    names = ", ".join(m.name for m in active)
    return ToolResult(content=..., is_error=True)
```

（ `cc/tools/team/team_delete_tool.py:71-84` ）

这个检查防止在 teammate 还在运行时删除团队，避免数据丢失。只有当所有非 Lead 成员的 `is_active` 都为 False 时才允许删除。

**5.3 递归删除团队目录**

```Python
team_dir = _get_team_dir("refactor-team")  # ~/.claude/teams/refactor-team/
shutil.rmtree(team_dir)
```

整个目录（包含 config.json 和 inboxes/ 子目录）被一次性清除。

**5.4 退出 TeamContext**

`team_context.leave_team()` 将 `_team_name` 重置为 None（ `cc/swarm/team_context.py:52-58` ）。从这一刻起， `is_active` 变为 False，inbox 轮询停止，SendMessage 不再可用，系统回到单 Agent 模式。

## 4. 通信机制——文件系统邮箱

Agent Teams 的通信不走内存 Queue，也不走网络，而是基于文件系统的邮箱（ `cc/swarm/mailbox.py` ）。

**目录结构**

```Plaintext
~/.claude/teams/{team_name}/
    config.json                      # 团队配置（成员列表等）
    inboxes/
        team-lead.json               # Lead 的收件箱
        researcher.json              # researcher 的收件箱
        coverage-analyst.json        # coverage-analyst 的收件箱
```

**TeammateMailbox 类**

每个邮箱实例绑定一个 team_name，构造时计算出 inbox 目录路径：

```Python
self._inbox_dir = self._claude_dir / "teams" / sanitize_name(team_name) / "inboxes"
```

（ `cc/swarm/mailbox.py:86-92` ）

**send()——发送消息**

```Python
def send(self, to: str, message: TeammateMessage) -> None:
    message.read = False              # 强制设为未读
    messages = self._read_inbox(to)   # 读取收件人现有消息
    messages.append(message)          # 追加新消息
    self._write_inbox(to, messages)   # 全量写回
```

（ `cc/swarm/mailbox.py:128-145` ）

注意这是一个读-追加-全量覆盖的操作，不是增量追加。每次写入都会覆盖整个文件。当前实现没有文件锁，在高并发写入同一收件箱时理论上可能丢失消息——但对于 Agent swarm 场景（消息频率低），这种简化是可以接受的。

**receive()——读取未读消息**

```Python
def receive(self, agent_name: str) -> list[TeammateMessage]:
    messages = self._read_inbox(agent_name)
    return [m for m in messages if not m.read]
```

（ `cc/swarm/mailbox.py:147-157` ）

只返回 `read=False` 的消息。调用方处理完后需要调用 `mark_all_read()` 标记已读。

**mark_all_read()——标记已读**

遍历所有消息，将 `read` 设为 True，如果确实有变化才写回文件（ `cc/swarm/mailbox.py:166-180` ）。

**为什么用文件系统而非内存 Queue？**

三个原因：

1. Agent 可能跨进程运行——文件系统是最简单的跨进程通信介质
2. 消息需要持久化——Agent 重启后仍然可读
3. 不依赖外部消息中间件——无需 Redis、RabbitMQ 等额外基础设施

**TeammateMessage 数据结构**

```Python
@dataclass
class TeammateMessage:
    from_name: str        # 发送者名称
    text: str             # 消息正文
    timestamp: float      # Unix 时间戳
    read: bool = False    # 是否已读
    summary: str | None = None  # 可选摘要，供 Lead 快速预览
```

（ `cc/swarm/mailbox.py:30-45` ）

JSON 序列化时使用 `"from"` 作为 key（而非 `"from_name"` ），与 TS 版本的数据格式保持一致。

## 5. 身份隔离——contextvars

问题：同一进程内，多个 teammate 以 `asyncio.Task` 形式并发运行在同一线程中。它们共享全局变量和实例变量。如果 teammate A 正在执行 SendMessage，它怎么知道自己是 `"researcher"` 而不是 `"coverage-analyst"` ？

解决方案：Python 的 `contextvars` 模块（ `cc/swarm/in_process_runner.py:27-35` ）。

```Python
_teammate_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_agent_id", default=None
)
_teammate_team_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_team_name", default=None
)
_teammate_agent_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_agent_name", default=None
)
```

三个 ContextVar 分别记录当前协程的 agent_id、team_name 和 agent_name。

**工作原理**

`asyncio.Task` 在创建时会自动复制当前上下文。 `InProcessTeammate.run()` 方法在进入时设置这三个变量：

```Python
token_id = _teammate_agent_id.set(self.agent_id)
token_team = _teammate_team_name.set(self.team_name)
token_name = _teammate_agent_name.set(self.agent_name)
```

（ `cc/swarm/in_process_runner.py:94-96` ）

之后这个 Task 内的所有代码（包括 query_loop、工具执行、SendMessage 等）都能通过 `get_current_teammate_id()` 、 `get_current_team_name()` 等函数查询到正确的身份（ `cc/swarm/in_process_runner.py:38-51` ）。

`is_in_process_teammate()` （ `cc/swarm/in_process_runner.py:48-51` ）通过检查 `_teammate_agent_id` 是否为 None 来判断当前代码是否运行在 teammate 内部——这用于区分 Lead 和 Teammate 的行为差异。

**为什么不用实例变量？**

InProcessTeammate 确实有实例变量（ `self.agent_id` 等），但这些只在 `_execute_with_query_loop()` 方法内部直接可用。query_loop 调用的工具（如 SendMessage）拿不到 InProcessTeammate 的实例引用。contextvars 提供了一种"隐式传参"机制，让任何深度的调用栈都能查询当前身份。

**cleanup 保证**

finally 块中使用 token 精确重置：

```Python
finally:
    _teammate_agent_id.reset(token_id)
    _teammate_team_name.reset(token_team)
    _teammate_agent_name.reset(token_name)
```

（ `cc/swarm/in_process_runner.py:110-112` ）

`reset(token)` 比 `set(None)` 更安全——它恢复到设置前的精确状态，在嵌套场景下不会误清外层的值。

## 6. 工具过滤规则

不同运行模式下的工具集是不同的。这个过滤发生在构建子工具注册表时，是防止递归和越权的关键机制。

| 运行模式     | 排除的工具                                        | 原因                         |
| ------------ | ------------------------------------------------- | ---------------------------- |
| 前台子 Agent | Agent                                             | 防止无限递归派生子 Agent     |
| 后台子 Agent | Agent + AskUserQuestion                           | 后台无法与用户交互           |
| Teammate     | Agent + TeamCreate + TeamDelete + AskUserQuestion | 只有 Lead 能管理团队生命周期 |

**前台子 Agent 的过滤** （ `cc/tools/agent/agent_tool.py:171-178` ）：

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

前台模式只排除 Agent 自身。后台模式额外排除 AskUserQuestion。

**Teammate 的过滤** （ `cc/swarm/in_process_runner.py:139-152` ）：

排除四个工具：Agent、AskUserQuestion、TeamCreate、TeamDelete。此外，SendMessage 被替换为一个注入了 teammate 身份的新实例：

```Python
child_registry.register(SendMessageTool(
    team_name=self.team_name,
    sender_name=self.agent_name,
))
```

这意味着当 teammate 调用 SendMessage 时，不需要自己指定 `team_name` 和 `sender_name` ——它们已经被硬编码在工具实例中。消息发出时自动携带正确的 `from_name` 。

## 7. Coordinator 模式

Coordinator 是一种可选的增强模式，通过环境变量 `CLAUDE_CODE_COORDINATOR_MODE=1` （或 `CC_COORDINATOR=1` ）启用。

**触发机制**

`is_coordinator_mode()` （ `cc/swarm/coordinator.py:18-23` ）检查环境变量。如果启用， `maybe_inject_coordinator_prompt()` （ `cc/swarm/coordinator.py:36-56` ）在 `_build_engine()` 的 Step 2 中被调用（ `cc/main.py:371-373` ），将 Coordinator prompt 前置到 base system prompt 之前。

**Coordinator prompt 的内容**

`COORDINATOR_SYSTEM_PROMPT` （ `cc/prompts/coordinator_prompt.py:32-181` ）是一段约 180 行的编排指令，包含 5 个部分：

**英文原文（结构概要）**

```Plaintext
1. Your Role
   You are a coordinator. Your job is to: decompose tasks, direct workers,
   synthesize results, and communicate with the user.

2. Your Tools
   Use only: Agent (spawn workers), SendMessage (continue existing workers),
   TaskStop (stop workers).

3. Workers
   Workers execute autonomously. They have access to standard tools and MCP tools.

4. Task Workflow
   Phase 1 - Research:      Workers (parallel) — explore codebase, find files,
                                                  understand the problem
   Phase 2 - Synthesis:     Coordinator (self) — read findings, understand problem,
                                                  write implementation spec
   Phase 3 - Implementation: Workers            — modify code per spec, commit
   Phase 4 - Verification:  Workers            — test and verify

5. Writing Worker Prompts
   Must be self-contained. Include file paths and line numbers.
   State completion criteria clearly.
```

**中文翻译**

```Plaintext
1. 你的角色
   你是协调者。你的职责是：分解任务、指挥 worker、综合结果、与用户沟通。

2. 你的工具
   只使用：Agent（派生 worker）、SendMessage（继续已有 worker）、
   TaskStop（停止 worker）。

3. Worker
   Worker 自主执行。它们能访问标准工具和 MCP 工具。

4. 任务工作流
   阶段 1 - 调研：  Worker（并行）—— 探索代码库、找文件、理解问题
   阶段 2 - 综合：  协调者（自己）—— 阅读发现、理解问题、撰写实施规范
   阶段 3 - 实施：  Worker        —— 按规范修改代码、提交
   阶段 4 - 验证：  Worker        —— 测试验证

5. 撰写 Worker Prompt
   必须自包含。包含文件路径和行号。清晰说明完成标准。
```

**与普通 Agent Teams 的区别**

**普通 Agent Teams 模式中，模型自行判断是否需要多 Agent 协作、如何分配任务。Coordinator 模式通过 prompt 给模型植入了一套完整的编排方法论——何时并行、何时串行、如何综合、如何验证。区别在于决策依据的来源：一个靠模型自由判断，一个靠 prompt 注入的结构化流程。**

两者的运行时机制完全相同——都是通过 TeamCreate 创建团队、AgentTool 派生 teammate、mailbox 通信。Coordinator 模式不引入新的代码路径，只是 prompt 不同。

## 8. Agent ID 格式

Agent ID 是整个 Teams 系统中标识 agent 的唯一字符串，格式为 `"name@team"` ，类似 email 地址。

**构造与解析**

`format_agent_id("researcher", "refactor-team")` 返回 `"researcher@refactor-team"` （ `cc/swarm/identity.py:55-61` ）。

`parse_agent_id("researcher@refactor-team")` 返回 `AgentRoute(agent_name="researcher", team_name="refactor-team")` （ `cc/swarm/identity.py:33-52` ）。解析时使用 `split("@", 1)` ——只在第一个 `@` 处分割，允许 team_name 中包含 `@` 字符。如果格式不合法（缺少 `@` 或者 `@` 前后为空），抛出 ValueError。

**AgentRoute 数据结构**

```Python
@dataclass
class AgentRoute:
    agent_name: str  # 如 "researcher"
    team_name: str   # 如 "refactor-team"
```

（ `cc/swarm/identity.py:21-30` ）

**sanitize_name()**

`sanitize_name()` （ `cc/swarm/identity.py:64-73` ）将名称中的非字母数字字符替换为连字符并转小写。用途是生成文件系统安全的路径名——团队目录名、邮箱文件名都经过 sanitize 处理。例如 `"My Test Team!"` 变为 `"my-test-team-"` 。

**TEAM_LEAD_NAME 常量**

```Python
TEAM_LEAD_NAME = "team-lead"
```

（ `cc/swarm/identity.py:17` ）

这是一个固定常量。所有 teammate 完成任务后都向这个名称对应的 inbox 发送结果。Lead 的完整 agent_id 是 `"team-lead@{team_name}"` 。

## 9. 持久化——TeamFile

团队的所有元数据存储在 `~/.claude/teams/{team_name}/config.json` 中（ `cc/swarm/team_file.py` ）。

**目录与路径**

```Python
def _get_team_dir(team_name: str, claude_dir: Path | None = None) -> Path:
    base = claude_dir or _DEFAULT_CLAUDE_DIR  # 默认 ~/.claude
    return base / "teams" / sanitize_name(team_name)

def _get_team_file_path(team_name: str, claude_dir: Path | None = None) -> Path:
    return _get_team_dir(team_name, claude_dir) / "config.json"
```

（ `cc/swarm/team_file.py:136-148` ）

所有路径计算都接受可选的 `claude_dir` 参数，方便测试时指定临时目录。

**TeamFile 数据结构**

```Python
@dataclass
class TeamFile:
    name: str                          # 团队名称
    description: str | None = None     # 可选描述
    created_at: float = 0              # 创建时间（Unix timestamp）
    lead_agent_id: str = ""            # Lead 的 agent_id
    members: list[TeamMember] = field(default_factory=list)
```

（ `cc/swarm/team_file.py:83-102` ）

**TeamMember 数据结构**

```Python
@dataclass
class TeamMember:
    agent_id: str                      # "name@team"
    name: str                          # 可读名称
    agent_type: str | None = None      # 预留
    model: str | None = None           # 预留
    joined_at: float = 0               # 加入时间
    cwd: str = ""                      # 工作目录
    is_active: bool = True             # 是否活跃
```

（ `cc/swarm/team_file.py:29-49` ）

**JSON 格式——camelCase**

`to_dict()` 方法输出的 JSON 使用驼峰命名： `agentId` 、 `leadAgentId` 、 `isActive` 、 `joinedAt` 等。 `from_dict()` 也从 camelCase key 读取。这是为了与 TS 版本的数据格式兼容——Python 版和 TS 版可以读取彼此生成的 team file。

**add_member 和 remove_member**

两个函数都遵循读-修改-写的模式（ `cc/swarm/team_file.py:189-233` ）：

```Python
def add_member(team_name, member, claude_dir=None):
    team = load_team_file(team_name, claude_dir)  # 读
    if team is None:
        raise ValueError(...)
    existing_ids = {m.agent_id for m in team.members}
    if member.agent_id in existing_ids:
        return  # 防重复
    team.members.append(member)                     # 改
    save_team_file(team, claude_dir)                # 写
```

`remove_member()` 按 `name` 而非 `agent_id` 匹配，因为调用方通常只知道 agent 的可读名称。只在确实删除了成员时才写回文件，避免不必要的磁盘 IO。

`load_team_file()` 在文件不存在时返回 None（而非抛异常），让调用方自行决定如何处理"团队不存在"的情况。JSON 解析失败时也返回 None 并记录 warning——defensive coding，确保 team file 损坏不会中断主流程。

`save_team_file()` 使用 `indent=2` 格式化输出，便于人工检查和调试。首次创建团队时， `team_dir.mkdir(parents=True, exist_ok=True)` 确保所有层级目录都存在。