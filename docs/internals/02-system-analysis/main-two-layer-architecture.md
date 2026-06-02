# 03 先抓主线main两层架构

这一篇只做一件事：先把整套 agent 的主链路建立成一个基础认知。

不要先问"这个工具怎么写的"，也不要先问"这个 prompt 文案是什么意思"。先回答这 3 个问题：

1. 系统的控制入口在哪里？
2. 核心状态是什么？
3. 一次用户输入在运行时到底经历了哪些阶段？

## 先把整个系统分成两层

### 外层：控制面 / shell

入口在 `cc/main.py` 。

它负责：

- 初始化 client
- 读取用户输入
- 组装 system prompt
- 注册工具
- 管理 REPL 生命周期
- 管理 session / history / memory extraction

它本身不做"智能推理"，更像 orchestrator。

### 内层：agent runtime

入口在 `cc/core/query_engine.py` 的 `QueryEngine` 类，实际循环在 `cc/core/query_loop.py` 。

调用链为：

```Plaintext
main.py --> QueryEngine.run_turn() / submit() --> query_loop()
```

`QueryEngine` 封装了一次对话的全部运行时依赖（client、model、registry、messages、permission 等），对外暴露 `run_turn()` 和 `submit()` 两个入口。 `query_loop()` 则是真正的状态机循环，由 `QueryEngine` 内部调用。

它们合起来负责：

- 把 transcript 发给模型
- 接收流式事件
- 识别 tool_use
- 执行工具
- 把 tool_result 塞回 transcript
- 做错误恢复和 compact
- 管理权限门控（permission checker）

这一层才是这个项目真正的"agent 内核"。

## 2. 最重要的状态不是对象树，而是 transcript（Message）

如果你是做 agent runtime 的，最先要建立的认知是：

> 这个系统的 source of truth 不是 UI，不是 tool registry，不是 session 文件，而是 `messages` 。

> `messages` 的生命周期是：

1. 在 `main.py` 中初始化
2. 每轮用户输入 append 一条 `UserMessage`
3. `query_loop()` append 一条 `AssistantMessage`
4. 如果有工具结果，再 append 一条带 `ToolResultBlock` 的 `UserMessage`
5. session 将它持久化
6. compact 会对它做结构性压缩
7. memory extractor 会读取它的最近窗口

也就是说， 几乎所有模块都在围绕同一份 transcript（Message） 工作。

## 3. 这个项目为什么是 agent

因为它不是"一次 prompt -> 一次 response"。

普通 LLM app 常见流程是：

```Plaintext
用户输入 -> 调模型 -> 输出结果 -> 结束
```

而这个项目是：

```Plaintext
用户输入
  -> 调模型
  -> 模型可能要求调用工具
  -> 本地执行工具
  -> 工具结果重新回到 transcript
  -> 再调模型
  -> 重复直到任务真的结束
```

也就是说，一次用户输入可以触发多轮模型调用和多次工具执行。

这就是 agent 的最小闭环。

## 4. 主链路先看 REPL

从 REPL 路径看最容易，因为它涵盖的能力最完整。

关键入口在 `cc/main.py` ：

📊 电子表格

表格ID: Hl6UVc

无法加载表格数据，请在飞书中查看

```Python
# ---------------------------------------------------------------------------
# REPL mode
# ---------------------------------------------------------------------------

async def _run_repl(model: str, resume_id: str | None = None) -> None:
    """Interactive REPL mode.

    P0.5a: Uses QueryEngine for runtime wiring.

    === REPL 主循环结构 ===

    整个函数分为三个阶段：

    【初始化阶段】
      - 创建 client + engine + MCP 连接
      - 注册 skills 为 slash commands
      - 创建 ExtractionCoordinator（后台 memory 提取）
      - 如果有 resume_id，加载并修复旧 transcript + task snapshot

    【主循环】 while True:
      A. 读取用户输入（支持多行）
      B. 判断是否是 slash command（/clear, /compact, /model, /skill）
         → 是：立即处理，continue 回到循环顶部
         → 否：构造 UserMessage，append 到 transcript
      C. 收件箱轮询（如果处于 team 模式，拉取 teammate 消息注入 transcript）
      D. engine.run_turn() → 驱动一轮 query_loop，消费事件流渲染 UI
      E. 后处理：
         - save_session(): 持久化 transcript + task snapshot
         - _bg_extract(): 后台异步提取 memory（不阻塞下一轮输入）

    【退出】
      - 用户输入 EOF（Ctrl+D）时退出
    """
    api_key = _get_api_key()
    if not api_key:
        console.print("[red]Error: No API key found. Set ANTHROPIC_API_KEY env var or add it to .env file.[/]")
        sys.exit(1)

    client = create_client(api_key=api_key)
    cwd = str(Path.cwd())

    engine = _build_engine(client, model, cwd)

    # MCP
    await _connect_mcp_servers(cwd, engine.registry)

    # Skills — load and register as slash commands
    skills = load_skills(cwd)
    if skills:
        from cc.commands.registry import register_command

        for skill in skills:
            _name = skill.name

            def _make_skill_handler(name: str) -> object:
                def handler(**_kwargs: object) -> str:
                    return f"__SKILL__{name}"
                return handler

            register_command(skill.name, skill.description, _make_skill_handler(_name))

    # messages 是 engine 内部 transcript 的引用——同一个 list 对象
    # REPL 中对 messages 的操作（append/clear/extend）直接影响 engine 的状态
    messages: list[Message] = engine.messages
    # _bg_tasks 持有后台 memory extraction 的 asyncio.Task 引用，防止被 GC 回收
    _bg_tasks: set[asyncio.Task[None]] = set()

    # ExtractionCoordinator 替代了直接调用 extract_memories()
    # 它内部维护增量计数（_last_extracted_count）和 coalescing 逻辑：
    # 如果上一次提取还在运行，新请求会被合并（设置 dirty 标记），等上一次完成后自动重跑
    from cc.memory.extractor import ExtractionCoordinator

    extraction_coord = ExtractionCoordinator()

    # === Resume 恢复流程 ===
    # 从 ~/.claude/sessions/<session_id>.jsonl 加载旧 transcript
    # 必须做 validate_transcript() 修复，因为上次可能是中途崩溃退出的，
    # transcript 末尾可能有 orphaned tool_use（没有配对的 tool_result）
    # → 不修复的话 API 调用会报协议错误
    if resume_id:
        from cc.session.storage import load_session, load_task_snapshot

        loaded = load_session(resume_id)
        if loaded:
            from cc.session.recovery import validate_transcript

            repaired = validate_transcript(loaded)
            messages.extend(repaired)

            # 恢复 TaskRegistry 快照（后台任务状态）
            # 非终态任务（RUNNING/PENDING）会被标记为 KILLED，因为对应的 asyncio.Task 已丢失
            task_snap = load_task_snapshot(resume_id)
            if task_snap and hasattr(engine, '_task_registry'):
                engine._task_registry.restore(task_snap)

            console.print(f"[dim]Resumed session {resume_id} ({len(messages)} messages)[/]")
        else:
            console.print(f"[yellow]Session {resume_id} not found, starting fresh.[/]")

    from uuid import uuid4

    from cc.ui.renderer import print_welcome

    print_welcome()
    session_id = resume_id or str(uuid4())[:8]
    claude_md = load_claude_md(cwd)

    # ==========================================
    # === REPL 主循环开始 ===
    # ==========================================
    while True:
        # --- A. 读取用户输入 ---
        try:
            user_input = _read_multiline_input()
        except EOFError:
            console.print("\nBye!")
            break

        if not user_input.strip():
            continue

        # --- B. Slash command 处理 ---
        # slash command 不进入 query_loop，而是在 REPL 层直接处理
        # 每个 command handler 返回一个标记字符串（__CLEAR__, __MODEL__xxx 等）
        if user_input.strip().startswith("/"):
            from cc.commands.registry import get_command, parse_slash_command

            cmd_name, cmd_args = parse_slash_command(user_input)
            cmd = get_command(cmd_name)
            if cmd is None:
                console.print(f"[red]Unknown command: /{cmd_name}[/]")
                continue

            result = cmd.handler(
                args=cmd_args,
                current_model=engine.model,
                total_input_tokens=engine.total_input_tokens,
                total_output_tokens=engine.total_output_tokens,
            )

            # 各 slash command 的处理分支：
            # __CLEAR__   → 清空 transcript（messages.clear()）
            # __COMPACT__ → 手动触发 compact（压缩 transcript）
            # __MODEL__x  → 切换模型 + 重建 system prompt
            # __SKILL__x  → 把 skill prompt 作为 UserMessage 注入 transcript
            if result == "__CLEAR__":
                messages.clear()
                console.print("[dim]Conversation cleared.[/]")
                continue
            elif result == "__COMPACT__":
                from cc.compact.compact import compact_messages

                compacted = await compact_messages(
                    messages,
                    engine.make_call_model(max_tokens=4096),
                )
                messages.clear()
                messages.extend(compacted)
                console.print("[yellow]Context compacted.[/]")
                continue
            elif isinstance(result, str) and result.startswith("__MODEL__"):
                engine.model = result[len("__MODEL__"):]
                engine.system_prompt = _build_system(cwd, engine.model, claude_md)
                console.print(f"[dim]Model changed to: {engine.model}[/]")
                continue
            elif isinstance(result, str) and result.startswith("__SKILL__"):
                from cc.skills.loader import get_skill_by_name

                skill_name = result[len("__SKILL__"):]
                found_skill = get_skill_by_name(skills, skill_name)
                if found_skill:
                    messages.append(UserMessage(content=found_skill.prompt))
                    console.print(f"[dim]Skill /{skill_name} activated[/]")
                else:
                    console.print(f"[red]Skill not found: {skill_name}[/]")
                    continue
            else:
                console.print(result)
                continue
        else:
            # 普通用户输入 → 构造 UserMessage 追加到 transcript
            # 这是 transcript 的第一个写入点：用户消息落盘
            messages.append(UserMessage(content=user_input))

        # 记录到输入历史（~/.claude/history.jsonl），用于 session 列表展示
        add_to_history(HistoryEntry(
            display=user_input[:200],
            timestamp=time.time(),
            project=cwd,
            session_id=session_id,
        ))

        # --- C. 收件箱轮询（仅 Team 模式）---
        # 在进入 query_loop 之前，检查是否有 teammate 发来的消息
        # 如果有，把它们包装成 <task-notification> 格式的 UserMessage 注入 transcript
        # 这样模型在下一轮就能看到 teammate 的汇报
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

        # --- D. 驱动内核 ---
        # engine.run_turn() 内部调用 query_loop()，这是状态机运转的入口
        # query_loop 是一个 async generator，每 yield 一个 QueryEvent 就在这里被消费
        # 事件类型：TextDelta（文本增量）、ToolUseStart（工具调用开始）、
        #           ToolResultReady（工具结果）、TurnComplete（单轮结束）、ErrorEvent 等
        try:
            async for event in engine.run_turn():
                render_event(event)  # UI 渲染：纯消费，不影响内核状态
                if isinstance(event, TurnComplete):
                    engine._total_input_tokens += event.usage.input_tokens
                    engine._total_output_tokens += event.usage.output_tokens
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted.[/]")
            continue

        # --- E. 后处理 ---

        # E1. 持久化 transcript + task 状态
        # 每轮结束都存一次，这样即使下次崩溃也能 resume 恢复
        task_snap = engine._task_registry.snapshot() if hasattr(engine, '_task_registry') else None
        save_session(session_id, messages, task_snapshot=task_snap)

        # E2. 后台 memory extraction
        # 用一个低配的 call_model（max_tokens=1024）去扫描最近的对话，
        # 提取值得长期记忆的信息保存到 memory 文件 + 更新 MEMORY.md 索引
        # 整个过程是异步的（asyncio.create_task），不阻塞下一轮用户输入
        _extraction_call = engine.make_call_model(max_tokens=1024)
        _extraction_msgs = messages
        _extraction_cwd = cwd

        async def _bg_extract(
            msgs: list[Message] = _extraction_msgs,
            wd: str = _extraction_cwd,
            call: object = _extraction_call,
        ) -> None:
            try:
                saved = await extraction_coord.request_extraction(msgs, wd, call)
                if saved:
                    console.print(f"[dim]Saved {len(saved)} memory(s): {', '.join(saved)}[/]")
            except Exception as e:
                logger.debug("Memory extraction skipped: %s", e)

        task = asyncio.create_task(_bg_extract())
        _bg_tasks.add(task)                       # 持有引用防止 GC
        task.add_done_callback(_bg_tasks.discard)  # 完成后自动移除
```

这条路径里最重要的 7 件事：

1. 获取 API key
2. 创建 client
3. 调用 `_build_engine()` 构建 `QueryEngine` （内含 registry、permission、hooks、team context 等全部装配）
4. 加载 hooks / skills / memory / CLAUDE.md
5. 构建 `system`
6. 注册工具到 `ToolRegistry`
7. 在 while 循环里处理每次用户输入，通过 `engine.run_turn()` 驱动 agent

## 5. 一次用户输入的完整序列

下面是 REPL 下一轮输入的真实序列：

```Plaintext
sequenceDiagram
    participant U as User
    participant M as main.py
    participant QE as QueryEngine
    participant Q as query_loop
    participant A as Claude API
    participant T as Tools
    participant S as Session/Memory

    U->>M: 输入文本或 slash command
    M->>M: 更新 messages / 处理命令
    M->>QE: engine.run_turn()
    QE->>Q: query_loop(messages, system, tools, call_model, permission_checker, ...)
    Q->>A: 发送规范化 transcript
    A-->>Q: 流式 TextDelta / ToolUseStart / TurnComplete
    Q->>Q: 累积文本、tool_use、usage、stop_reason
    alt 模型要求调用工具
        Q->>T: StreamingToolExecutor.get_results()
        T-->>Q: ToolResult
        Q->>Q: 把 tool_result 包成新的 UserMessage
        Q->>A: 再次发送 transcript
    else 不需要工具
        Q-->>QE: 本轮结束
        QE-->>M: yield 事件流
    end
    M->>S: save_session / add_to_history / extract_memories
```

## 6. 整个Agent这是一台"状态机"

> 因为它不是一条固定直线，而是在以下状态间切换：

- 等待用户输入
- 正在流式接收模型输出
- 等待工具执行
- 正在做错误恢复
- 正在做 compact
- 正在保存长期状态

> 这些状态切换的中心就在 `query_loop()` 。

## 7. 外层和内层的职责边界

📊 电子表格

表格ID: F33VQQ

无法加载表格数据，请在飞书中查看

## 8. 在主线里最关键的 6 个对象

### `QueryEngine`

一次对话的运行时容器。封装 client、model、registry、messages、permission context 等全部依赖，对外暴露 `run_turn()` / `submit()` 入口。 `main.py` 通过 `_build_engine()` 构建它，REPL 循环中通过 `engine.run_turn()` 驱动 agent。

### `messages`

完整 transcript，source of truth。

### `system`

system prompt，提供全局规则和长期上下文。

### `tools`

工具注册表，告诉模型"有哪些动作可选"。

### `call_model`

一个统一的模型调用适配器。由 `QueryEngine.make_call_model()` 生成，绑定特定 model 和 max_tokens。

### `QueryEvent`

模型流式输出在内部的统一表示。共 7 种事件类型：

📊 电子表格

表格ID: KJDPzA

无法加载表格数据，请在飞书中查看

## 这一篇读完后应该立刻去哪里

建议顺序：

1. `cc/core/query_loop.py`
2. `cc/models/messages.py`
3. `cc/api/claude.py`

原因很简单：

📊 电子表格

表格ID: 41iaiv

无法加载表格数据，请在飞书中查看