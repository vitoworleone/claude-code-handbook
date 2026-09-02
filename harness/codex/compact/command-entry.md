# 从“压缩”命令到 `Op::Compact`

本文档基于 OpenAI `codex` 公开仓库提交：`633ab19`。

## 1. Command 不是 Tool

图片中的“压缩”属于 Command，更准确地说是客户端命令菜单中的一个控制操作。

| 对比项 | Command | Tool | 普通用户消息 |
| --- | --- | --- | --- |
| 发起者 | 用户或客户端 UI | LLM | 用户 |
| 主要接收者 | App、App Server、Core | ToolRouter | LLM |
| 是否需要 Tool schema | 否 | 是 | 否 |
| 是否进入 `Prompt.input` | 通常不进入 | Call 和 Result 会进入 | 会进入 |
| Compact 的表现 | 转换成 `Op::Compact` | 不适用 | 不会把 `/compact` 当问题回答 |

只有少数命令会故意转换成一条模型消息。例如 TUI 的 `/init` 会提交内置提示词；`/compact` 不是这一类。

## 2. 两个客户端入口

Codex 当前至少有两个可以观察的入口：

```mermaid
flowchart LR
    subgraph TUIPath[TUI]
        Slash[/compact]
        Enum[SlashCommand::Compact]
        Dispatch[slash_dispatch]
        AppEvent[AppCommand::Compact]
        Client[App Server client]
        Slash --> Enum --> Dispatch --> AppEvent --> Client
    end

    subgraph DesktopPath[Desktop]
        Menu[点击压缩]
        Rpc[thread/compact/start]
        Menu --> Rpc
    end

    Client --> Rpc
    Rpc --> Processor[thread_processor]
    Processor --> Op[Op::Compact]
    Op --> Handler[session handler]
    Handler --> Task[CompactTask]
```

当前仓库没有完整公开 Desktop 菜单的前端组件，所以无法从这里证明中文标签、图标和点击回调分别位于哪个前端文件。但是 Desktop 和 TUI 最终使用的 App Server/Core 协议是可以完整追踪的。

## 3. TUI 怎样注册和展示命令

命令定义在：

- [`tui/src/slash_command.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/slash_command.rs)

简化后：

```rust
pub enum SlashCommand {
    Model,
    New,
    Archive,
    Compact,
    Goal,
    Status,
    Feedback,
    // ...
}
```

枚举顺序同时是 Popup 中的展示顺序。`Compact` 的说明为：

```rust
SlashCommand::Compact =>
    "summarize conversation to prevent hitting the context limit"
```

Popup 位于：

- [`tui/src/bottom_pane/command_popup.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/bottom_pane/command_popup.rs)

它读取输入框第一行开头的 `/`，提取命令前缀，再过滤命令列表。

统一的可见性和 Feature Gate 位于：

- [`tui/src/bottom_pane/slash_commands.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/bottom_pane/slash_commands.rs)

这里还会把动态 Service Tier commands 插在 `Model` 后面。因此“快速”一类选项可能是服务等级命令，不一定是写死在 `SlashCommand` 枚举中的固定变体。

## 4. TUI 怎样分发 `/compact`

分发代码位于：

- [`tui/src/chatwidget/slash_dispatch.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/chatwidget/slash_dispatch.rs)

主干代码：

```rust
SlashCommand::Compact => {
    if self.blocks_direct_input {
        self.add_error_message(PARENT_OWNED_INPUT_MESSAGE.to_string());
        return;
    }
    self.clear_token_usage();
    if !self.bottom_pane.is_task_running() {
        self.bottom_pane.set_task_running(true);
    }
    self.input_queue.user_turn_pending_start = true;
    self.app_event_tx.compact();
}
```

关键点是它调用：

```rust
self.app_event_tx.compact();
```

而不是：

```rust
self.submit_user_message("/compact");
```

`AppEventSender` 再把它封装为 `AppCommand::Compact`：

- [`tui/src/app_event_sender.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/app_event_sender.rs)

TUI 的 App Server routing 最终执行：

```rust
AppCommand::Compact => {
    app_server.thread_compact_start(thread_id).await?;
    Ok(true)
}
```

源码：

- [`tui/src/app/thread_routing.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/app/thread_routing.rs)

## 5. Desktop/App Server 协议

App Server 对客户端暴露：

```text
thread/compact/start
```

协议注册位置：

- [`app-server-protocol/src/protocol/common.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server-protocol/src/protocol/common.rs)

参数只有 Thread ID：

```rust
pub struct ThreadCompactStartParams {
    pub thread_id: String,
}
```

源码：

- [`app-server-protocol/src/protocol/v2/thread.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server-protocol/src/protocol/v2/thread.rs)

Request Processor 加载对应 Thread，检查是否允许直接输入，然后提交 Core Op：

```rust
let (_, thread) = self.load_thread(&thread_id).await?;
ensure_direct_input_allowed(thread.as_ref()).await?;
self.submit_core_op(request_id, thread.as_ref(), Op::Compact)
    .await?;
```

源码：

- [`app-server/src/request_processors/thread_processor.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server/src/request_processors/thread_processor.rs)

## 6. `Op::Compact` 怎样进入 Session

Core 的控制协议中定义了：

```rust
/// Request the agent to summarize the current conversation context.
Compact,
```

源码：

- [`protocol/src/protocol.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/protocol.rs)

Session 的 Op dispatcher 收到它以后调用：

```rust
Op::Compact => {
    compact(&sess, sub.id.clone()).await;
    false
}
```

Handler 创建一个使用默认 Turn settings 的 TurnContext，再启动 `CompactTask`：

```rust
pub async fn compact(sess: &Arc<Session>, sub_id: String) {
    let turn_context = sess
        .new_turn_with_default_settings(sub_id, Default::default())
        .await;

    sess.spawn_task(turn_context, Vec::new(), CompactTask).await;
}
```

源码：

- [`core/src/session/handlers.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/handlers.rs)

## 7. 为什么 Compact 是不可 steer 的独立 Turn

协议把 Compact 和 Review 归入 `NonSteerableTurnKind`。压缩期间不能像普通 Agent Turn 那样再追加用户输入，因为它正在用一个确定的历史快照建立新的上下文边界。

手动 `/compact` 也不能在正在执行的普通任务中直接运行。这样可以避免：

1. 压缩读取 History 的同时，Tool Result 继续写入；
2. 新输入究竟属于旧窗口还是新窗口变得不确定；
3. 持久化检查点与内存中的 replacement history 不一致。

## 8. 完成事件怎样回到 UI

Compact 执行时会创建 `ContextCompactionItem`，发送 started/completed 生命周期事件。App Server 将 Core item 映射成 V2 的：

```text
ThreadItem::ContextCompaction
```

因此 UI 显示的“正在压缩”或完成状态来自事件流，不是 LLM 给用户回复一句“压缩完成”。

下一步阅读：[三类主要压缩实现](implementations.md)。
