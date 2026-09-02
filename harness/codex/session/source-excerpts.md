# Session 关键源码摘录

本文档只保存理解调用关系所需的短片段。完整实现以 `codex/` 当前源码为准。

## 1. Runtime Session

源码：[session/session.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/session.rs)

```rust
/// Context for an initialized model agent
///
/// A session has at most 1 running task at a time, and can be interrupted by user input.
pub(crate) struct Session {
    pub(crate) thread_id: ThreadId,
    pub(super) state: Mutex<SessionState>,
    pub(crate) active_turn: Mutex<Option<ActiveTurn>>,
    pub(crate) input_queue: InputQueue,
    pub(crate) services: SessionServices,
    // ...
}
```

## 2. 活动 History 在 SessionState 中

源码：[state/session.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/state/session.rs)

```rust
pub(crate) struct SessionState {
    pub(crate) session_configuration: SessionConfiguration,
    pub(crate) history: ContextManager,
    pub(crate) latest_rate_limits: Option<RateLimitSnapshot>,
    pub(crate) latest_token_usage_record: Option<TokenUsageRecord>,
    pub(crate) additional_context: AdditionalContextStore,
    previous_turn_settings: Option<PreviousTurnSettings>,
    auto_compact_window: AutoCompactWindow,
    // ...
}
```

## 3. App Server 与 ThreadManager 的恢复入口

源码：[thread_processor.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server/src/request_processors/thread_processor.rs)、[thread_manager.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/thread_manager.rs)

```rust
// App Server：从 ThreadStore 得到可重建的历史
let history = InitialHistory::Resumed(ResumedHistory {
    conversation_id: model_context.thread_id,
    history: Arc::new(model_context.items),
    rollout_path: stored_thread.rollout_path.clone(),
});

// Core：恢复 source/metadata 后创建运行中的 Thread
pub async fn resume_thread_with_history(/* ... */) -> CodexResult<NewThread> {
    let options = StartThreadOptions { initial_history, /* ... */ };
    self.state.spawn_thread(ThreadSpawnRequest::new(options, /* ... */)).await
}
```

## 4. 新建、恢复和 Fork

源码：[history/src/lib.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/history/src/lib.rs)

```rust
pub enum InitialHistory {
    New,
    Cleared,
    Resumed(ResumedHistory),
    Forked(Vec<RolloutItem>),
}
```

源码：[session/mod.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

```rust
async fn record_initial_history(&self, conversation_history: InitialHistory) {
    match conversation_history {
        InitialHistory::New | InitialHistory::Cleared => {
            // 首个真实 Turn 再注入 initial context
        }
        InitialHistory::Resumed(resumed_history) => {
            self.apply_rollout_reconstruction(&turn_context, &rollout_items).await;
        }
        InitialHistory::Forked(mut rollout_items) => {
            self.apply_rollout_reconstruction(&turn_context, &rollout_items).await;
            // 建立子 Thread 的持久化边界
        }
    }
}
```

## 5. Rollout item 并不都属于模型历史

源码：[history/src/lib.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/history/src/lib.rs)

```rust
pub enum RolloutItem {
    SessionMeta(SessionMetaLine),
    ResponseItem(ResponseItemEnvelope),
    InterAgentCommunication(InterAgentCommunication),
    Compacted(CompactedItem),
    TurnContext(TurnContextItem),
    TokenUsageRecord(TokenUsageRecord),
    WorldState(WorldStateItem),
    SecurityRiskScore(SecurityRiskScore),
    EventMsg(EventMsg),
    RealtimeItem(RealtimeItem),
    // ...
}
```

源码：[session/rollout_reconstruction.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/rollout_reconstruction.rs)

```rust
match item {
    RolloutItem::ResponseItem(response_item) => {
        history.record_annotated_items(/* ... */);
    }
    RolloutItem::Compacted(compacted) => {
        if let Some(replacement_history) = &compacted.replacement_history {
            history.replace_annotated(replacement_history.clone());
        }
    }
    RolloutItem::EventMsg(EventMsg::ThreadRolledBack(rollback)) => {
        history.drop_last_n_user_turns(rollback.num_turns);
    }
    RolloutItem::EventMsg(_)
    | RolloutItem::TurnContext(_)
    | RolloutItem::WorldState(_)
    | RolloutItem::TokenUsageRecord(_)
    | RolloutItem::SessionMeta(_) => {}
    // ...
}
```

## 6. Context 第一次完整注入，以后注入 diff

源码：[session/mod.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

```rust
let should_inject_full_context = reference_context_item.is_none();

let (mut context_items, world_state_item) = if should_inject_full_context {
    let context_items = self
        .build_initial_context_with_world_state(turn_context, world_state.as_ref())
        .await;
    // 保存完整 baseline
    (context_items, Some(WorldStateItem::full(/* ... */)))
} else {
    // 只渲染 WorldState diff
    (world_state_items, world_state_item)
};

if !context_items.is_empty() {
    self.record_conversation_items(turn_context, &context_items).await;
}
```

## 7. 记录一次模型可见 item

源码：[session/mod.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

```rust
async fn record_prepared_conversation_items(/* ... */) {
    state.history.record_annotated_items(&items, truncation_policy);

    let rollout_items = items
        .into_iter()
        .map(RolloutItem::ResponseItem)
        .collect();
    self.persist_rollout_items(&rollout_items).await;
}
```

同一个逻辑 item 同时进入活动 History 与 Rollout，这是二者保持可恢复一致性的核心路径。需要注意，`ContextManager` 在克隆 Tool Result 时可能截断自己的活动副本；Rollout 持久化的是传入的原 envelope，Resume 时再按模型 policy 重建活动副本。

## 8. 生成一次真实 Prompt

源码：[session/turn.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs)、[client_common.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/client_common.rs)

```rust
let sampling_request_input = sess
    .clone_history()
    .await
    .for_prompt(&step_context.settings.model_info.input_modalities);

Prompt {
    input,
    tools: step_context.tool_router.model_visible_specs(),
    parallel_tool_calls: true,
    base_instructions,
    // ...
}
```

## 9. Tool Call 与 Result 都被记录

源码：[stream_events_utils.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/stream_events_utils.rs)

```rust
match ToolRouter::build_tool_call(item.clone()) {
    Ok(Some(call)) => {
        record_completed_response_item(/* model-emitted call */).await;
        output.tool_future = Some(tool_runtime.handle_tool_call(/* ... */));
        output.needs_follow_up = true;
    }
    // ...
}
```

源码：[session/turn.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs)

```rust
while let Some(res) = in_flight.next().await {
    if let Ok(envelope) = res {
        sess.record_annotated_conversation_items(&turn_context, vec![envelope])
            .await;
    }
}
```

## 10. Tool Result 会截断，Prompt 会规范化

源码：[context_manager/history.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/context_manager/history.rs)

```rust
if let ResponseItem::FunctionCallOutput { output, .. }
| ResponseItem::CustomToolCallOutput { output, .. } = &mut processed.item
{
    truncate_function_output_payload(output, policy, estimate_audio_token_count);
}
```

```rust
fn normalize_history(&mut self, input_modalities: &[InputModality]) {
    normalize::ensure_call_outputs_present(items);
    normalize::remove_orphan_outputs(items);
    normalize::strip_images_when_unsupported(input_modalities, items);
    normalize::strip_audio_when_unsupported(input_modalities, items);
}
```

## 11. 哪些 ResponseItem 可进入活动 History

源码：[context_manager/history.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/context_manager/history.rs)

```rust
fn is_api_message(message: &ResponseItem) -> bool {
    match message {
        ResponseItem::Message { role, .. } => role.as_str() != "system",
        ResponseItem::FunctionCall { .. }
        | ResponseItem::FunctionCallOutput { .. }
        | ResponseItem::CustomToolCall { .. }
        | ResponseItem::CustomToolCallOutput { .. }
        | ResponseItem::Reasoning { .. }
        | ResponseItem::WebSearchCall { .. }
        | ResponseItem::ImageGenerationCall { .. }
        | ResponseItem::Compaction { .. } => true,
        ResponseItem::CompactionTrigger { .. } | ResponseItem::Other => false,
        // 其他 API item 省略
    }
}
```

## 12. UI resume 脱敏是展示层行为

源码：[thread_resume_redaction.rs](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server/src/request_processors/thread_resume_redaction.rs)

```rust
// Keep this response-only so persisted rollout history,
// model resume history, and other APIs stay unchanged.
pub(super) fn redact_thread_resume_payloads(turns: &mut [Turn]) {
    // 对指定远程客户端隐藏 MCP payload，并去掉 image-generation payload
}
```
