# Context 关键源码摘录

基线：`codex` commit `633ab19`。

这些片段用于学习调用链，不参与编译。真实实现以链接的原始文件为准。

## 1. ContextContributor 有三个注入层级

来源：[`ext/extension-api/src/contributors.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/extension-api/src/contributors.rs)

```rust
pub trait ContextContributor: Send + Sync {
    fn contribute_thread_context<'a>(
        &'a self,
        session_store: &'a ExtensionData,
        thread_store: &'a ExtensionData,
    ) -> ExtensionFuture<'a, Vec<PromptFragment>>;

    fn contribute_turn_context<'a>(
        &'a self,
        input: TurnContextContributionInput<'a>,
    ) -> ExtensionFuture<'a, Vec<PromptFragment>>;

    fn contribute_world_state<'a>(
        &'a self,
        input: WorldStateContributionInput<'a>,
    ) -> ExtensionFuture<'a, Vec<WorldStateSectionContribution>>;
}
```

## 2. PromptFragment 带有位置与来源类型

来源：[`ext/extension-api/src/contributors/prompt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/extension-api/src/contributors/prompt.rs)

```rust
pub enum PromptSlot {
    DeveloperPolicy,
    DeveloperCapabilities,
    ContextWindow,
}

pub struct PromptFragment {
    slot: PromptSlot,
    text: String,
    content_kind: ContentItemKind,
}
```

转换成模型上下文时，普通 PromptFragment 使用 `developer` role：

```rust
impl From<PromptFragment> for RenderedFragment {
    fn from(fragment: PromptFragment) -> Self {
        Self::new(
            "developer",
            AnnotatedContent::input_text(fragment.text, fragment.content_kind),
        )
    }
}
```

## 3. Session 收集 Thread Context

来源：[`core/src/session/mod.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

```rust
let context_contributors = self.services.extensions.context_contributors().to_vec();
for contributor in &context_contributors {
    for fragment in contributor
        .contribute_thread_context(
            &self.services.session_extension_data,
            &self.services.thread_extension_data,
        )
        .await
    {
        match fragment.slot() {
            PromptSlot::ContextWindow => {
                context_window_hints.push(fragment.text().to_string());
            }
            PromptSlot::DeveloperPolicy | PromptSlot::DeveloperCapabilities => {
                developer_sections.push(fragment.into());
            }
        }
    }
}
```

这段代码证明不同 slot 会进入不同装配位置。

## 4. Session 同时收集 Turn Context

```rust
for contributor in &context_contributors {
    for fragment in contributor
        .contribute_turn_context(TurnContextContributionInput {
            thread_id: self.thread_id(),
            turn_id: turn_context.sub_id.as_str(),
            session_store: &self.services.session_extension_data,
            thread_store: &self.services.thread_extension_data,
            turn_store: turn_context.extension_data.as_ref(),
            model_context_window: turn_context.model_context_window(),
        })
        .await
    {
        developer_sections.push(fragment.into());
    }
}
```

## 5. WorldState 第一次完整注入，之后计算差异

```rust
let should_inject_full_context = reference_context_item.is_none();
let world_state = Arc::new(self.build_world_state_for_step(step_context).await?);

let (context_items, world_state_item) = if should_inject_full_context {
    let context_items = self
        .build_initial_context_with_world_state(turn_context, world_state.as_ref())
        .await;
    // 保存完整 baseline
    (context_items, Some(WorldStateItem::full(/* snapshot */)))
} else {
    // 根据旧 baseline 只生成变更 fragments
    let (fragments, rollout_item) =
        state.history.update_world_state(world_state.as_ref());
    (merge_contextual_fragments(fragments), rollout_item)
};
```

代码为突出主线省略了锁、转换和错误处理，请在原文件中查看完整实现。

## 6. Memory 同时注册 Prompt 与 Tool contributors

来源：[`ext/memories/src/extension.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/memories/src/extension.rs)

```rust
pub fn install(
    registry: &mut ExtensionRegistryBuilder<Config>,
    metrics_client: Option<MetricsClient>,
) {
    let extension = Arc::new(MemoriesExtension::new(metrics_client));
    registry.thread_lifecycle_contributor(extension.clone());
    registry.config_contributor(extension.clone());
    registry.prompt_contributor(extension.clone());
    registry.tool_contributor(extension);
}
```

## 7. Memory 启用条件与 developer fragment

```rust
enabled: config.features.enabled(Feature::MemoryTool)
    && config.memories.use_memories,
dedicated_tools: config.memories.dedicated_tools,
```

```rust
PromptFragment::developer_policy(
    instructions,
    ContentItemKind("memories.instructions".to_string()),
)
```

## 8. Memory Summary 只直接注入最多 2500 tokens

来源：[`ext/memories/src/prompts.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/memories/src/prompts.rs)

```rust
let base_path = codex_home.join("memories");
let memory_summary_path = base_path.join("memory_summary.md");
let memory_summary = fs::read_to_string(&memory_summary_path)
    .await
    .ok()?
    .trim()
    .to_string();

let memory_summary = truncate_text(
    &memory_summary,
    TruncationPolicy::Tokens(
        MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT,
    ),
);
```

限制常量位于 [`ext/memories/src/lib.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/memories/src/lib.rs)：

```rust
pub(crate) const MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT: usize = 2_500;
```

## 9. Memory 专用工具是四个

来源：[`ext/memories/src/tools/mod.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/memories/src/tools/mod.rs)

```rust
vec![
    Arc::new(ad_hoc_note::AddAdHocNoteTool { /* ... */ }),
    Arc::new(list::ListTool { /* ... */ }),
    Arc::new(read::ReadTool { /* ... */ }),
    Arc::new(search::SearchTool { /* ... */ }),
]
```
