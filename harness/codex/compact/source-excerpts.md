# Compact 关键源码摘录

基线：`codex` commit `633ab19`。

这些片段用于学习，不参与编译。为突出调用链，省略了部分埋点、错误处理和生命周期通知；真实行为以链接的原始源码为准。

## 1. Command 转成 App Server 请求

来源：[`tui/src/chatwidget/slash_dispatch.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/chatwidget/slash_dispatch.rs)

```rust
SlashCommand::Compact => {
    self.clear_token_usage();
    self.input_queue.user_turn_pending_start = true;
    self.app_event_tx.compact();
}
```

来源：[`tui/src/app/thread_routing.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/tui/src/app/thread_routing.rs)

```rust
AppCommand::Compact => {
    app_server.thread_compact_start(thread_id).await?;
    Ok(true)
}
```

## 2. App Server 转成 Core Op

来源：[`app-server/src/request_processors/thread_processor.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/app-server/src/request_processors/thread_processor.rs)

```rust
async fn thread_compact_start_inner(...) {
    let (_, thread) = self.load_thread(&thread_id).await?;
    ensure_direct_input_allowed(thread.as_ref()).await?;
    self.submit_core_op(request_id, thread.as_ref(), Op::Compact)
        .await?;
}
```

## 3. Session 创建 CompactTask

来源：[`core/src/session/handlers.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/handlers.rs)

```rust
pub async fn compact(sess: &Arc<Session>, sub_id: String) {
    let turn_context = sess
        .new_turn_with_default_settings(sub_id, Default::default())
        .await;

    sess.spawn_task(turn_context, Vec::new(), CompactTask).await;
}
```

## 4. 压缩实现选择

来源：[`core/src/tasks/compact.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tasks/compact.rs)

```rust
if ctx.config.features.enabled(Feature::TokenBudget) {
    compact_token_budget::run_manual_compact_task(session, ctx).await?;
    return Ok(None);
}

match ctx.provider.capabilities().remote_compaction {
    RemoteCompactionSupport::V2
        if ctx.config.features.enabled(Feature::RemoteCompactionV2) =>
    {
        compact_remote_v2::run_remote_compact_task(session, ctx).await
    }
    RemoteCompactionSupport::V2 => {
        compact_remote::run_remote_compact_task(session, ctx).await
    }
    RemoteCompactionSupport::Unsupported => {
        compact::run_compact_task(session, ctx, input).await
    }
}
```

## 5. 本地压缩 Prompt 不提供 Tools

来源：[`core/src/compact.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/compact.rs)

```rust
let prompt = Prompt {
    input: history
        .clone()
        .for_prompt(&turn_context.model_info().input_modalities),
    base_instructions: sess.get_prompt_base_instructions().await,
    ..Default::default()
};
```

`Prompt::default().tools` 是空数组。

## 6. 本地 replacement history

来源：[`core/src/compact.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/compact.rs)

```rust
const COMPACT_USER_MESSAGE_MAX_TOKENS: usize = 20_000;

let user_messages = collect_annotated_user_messages(history_items);
let mut new_history =
    build_compacted_history(Vec::new(), &user_messages, &summary_text);
```

```rust
for message in &selected_messages {
    history.push(user_message(message));
}

history.push(ResponseItemEnvelope::new(
    ContextualUserFragment::into(
        CompactionSummary::new(summary_text),
    ),
));
```

## 7. 远程 Compact endpoint

来源：[`core/src/client.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/client.rs)

```rust
const RESPONSES_COMPACT_ENDPOINT: &str = "/responses/compact";

pub(crate) async fn compact_conversation_history(...) {
    let transport = self.build_api_transport(
        &client_setup.api_provider,
        RESPONSES_COMPACT_ENDPOINT,
    )?;

    let payload = ApiCompactionInput {
        model: &model,
        input: &input,
        instructions: &instructions,
        tools,
        parallel_tool_calls,
        // ...
    };
}
```

## 8. V2 使用 CompactionTrigger

来源：[`core/src/compact_remote_v2_attempt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/compact_remote_v2_attempt.rs)

```rust
let (mut input, prompt_input_metadata) = history
    .for_prompt_annotated(&turn_context.model_info().input_modalities)
    .into_iter()
    .map(|envelope| (envelope.item, envelope.metadata))
    .unzip();

input.push(ResponseItem::CompactionTrigger {});

let prompt = Prompt {
    input,
    tools: tool_router.model_visible_specs(),
    parallel_tool_calls: true,
    base_instructions,
    // ...
};
```

## 9. V2 近期消息预算

来源：[`core/src/compact_remote_v2.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/compact_remote_v2.rs)

```rust
pub(crate) const RETAINED_MESSAGE_TOKEN_BUDGET: usize = 64_000;

let mut retained = truncate_retained_messages(
    retained,
    RETAINED_MESSAGE_TOKEN_BUDGET,
    image_budget,
);
retained.push(ResponseItemEnvelope::new(compaction_output));
```

## 10. 自动压缩阈值

来源：[`core/src/session/context_window.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/context_window.rs)

```rust
let full_context_window_limit_reached =
    full_context_window_limit
        .is_some_and(|limit| active_context_tokens >= limit);

let token_limit_reached = buffered_auto_compact_limit
    .is_some_and(|limit| auto_compact_scope_tokens >= limit)
    || full_context_window_limit_reached;
```

## 11. MidTurn 压缩

来源：[`core/src/session/turn.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs)

```rust
if should_roll_over {
    run_auto_compact(
        &sess,
        Arc::clone(&step_context),
        None,
        &mut client_session,
        InitialContextInjection::BeforeLastUserMessage {
            world_state: Arc::clone(&world_state),
            step_context: Arc::clone(&step_context),
        },
        CompactionReason::ContextLimit,
        CompactionPhase::MidTurn,
    )
    .await?;
    continue;
}
```

## 12. 安装并持久化检查点

来源：[`core/src/session/mod.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

```rust
let compacted_item = CompactedItem {
    message: metadata.message,
    replacement_history: Some(items.clone()),
    mcp_resource_origins: self.services.mcp_runtime.resource_origin_checkpoint(),
    window_number: Some(metadata.window_number),
    compaction_response_id: metadata.compaction_response_id,
    latest_token_usage_record: self
        .state
        .lock()
        .await
        .latest_token_usage_record
        .clone(),
    // window IDs ...
};

state.replace_annotated_history(
    items,
    reference_context_item.clone(),
    HistoryReplacement::Compaction,
);

self.persist_rollout_items(&rollout_items).await;
```

## 13. 恢复时以 replacement history 为基线

来源：[`core/src/session/rollout_reconstruction.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/rollout_reconstruction.rs)

```rust
RolloutItem::Compacted(compacted) => {
    if active_segment.base_replacement_history.is_none()
        && let Some(replacement_history) = &compacted.replacement_history
    {
        active_segment.base_replacement_history = Some(replacement_history);
        rollout_suffix = &rollout_items[index + 1..];
    }
}
```
