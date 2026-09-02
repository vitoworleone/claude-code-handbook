# 自动触发、History 替换、持久化与恢复

本文档基于 OpenAI `codex` 公开仓库提交：`633ab19`。

## 1. 手动压缩与自动压缩

手动压缩和自动压缩最终使用同一组实现，但入口、时机和 initial context 处理不同。

| 维度 | 手动压缩 | 自动压缩 |
| --- | --- | --- |
| 触发者 | 用户执行 Command | Agent Loop |
| 原因 | `UserRequested` | `ContextLimit`、`CompHashChanged`、`ModelDownshift` |
| Phase | `StandaloneTurn` | `PreTurn` 或 `MidTurn` |
| 是否独立 Turn | 是 | 可内联于当前 Turn |
| Initial context | 通常下一普通 Turn 重新注入 | MidTurn 时立即插入 replacement history |

## 2. Token 状态怎样计算

入口：

- [`core/src/session/context_window.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/context_window.rs)

核心状态包括：

```rust
pub(crate) struct ContextWindowTokenStatus {
    active_context_tokens: i64,
    auto_compact_scope_tokens: i64,
    auto_compact_scope_limit: Option<i64>,
    full_context_window_limit: Option<i64>,
    auto_compact_window_prefill_tokens: Option<i64>,
    full_context_window_limit_reached: bool,
    token_limit_reached: bool,
}
```

`active_context_tokens` 是完整活动上下文用量。

自动压缩阈值可以采用两种 scope：

### `Total`

```text
auto_compact_scope_tokens = active_context_tokens
```

全部活动上下文都计入阈值。

### `BodyAfterPrefix`

```text
auto_compact_scope_tokens
    = active_context_tokens - window.prefill_input_tokens
```

稳定前缀不计入每个压缩窗口新增的 body 预算，更适合前缀较大的 Agent 请求。

最终近似条件为：

```text
token_limit_reached =
    scope_tokens >= auto_compact_limit + fallback_buffer
    OR
    active_context_tokens >= effective_full_context_window
```

完整 Context Window 是硬上限，不受 scope 配置影响。

## 3. 自动压缩的三个时机

主循环位于：

- [`core/src/session/turn.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs)

### 3.1 PreTurn：发送本轮用户消息之前

`run_turn` 一开始执行：

```rust
run_pre_sampling_compact(...).await
```

它读取当前 token 状态。达到阈值时，在新用户输入和本轮 Context updates 写入 History 之前压缩旧窗口。

当前源码留有一个 TODO：PreTurn 检查尚未把即将加入的用户消息和 Context diff 预估进去。因此它判断的是压缩前已有活动上下文，而不是“已有上下文 + 即将写入内容”的完整预测值。

### 3.2 MidTurn：Tool 调用后仍需继续采样

一次 Agent Turn 可能经历：

```text
LLM -> Tool Call -> Tool Result -> 再次调用 LLM
```

Core 在每次 sampling/tool 阶段完成后重新计算 token 状态。如果模型或输入队列还要求继续，并且已经达到阈值，就在当前 Turn 中途压缩：

```rust
if should_roll_over {
    run_auto_compact(
        ...,
        InitialContextInjection::BeforeLastUserMessage { ... },
        CompactionReason::ContextLimit,
        CompactionPhase::MidTurn,
    ).await?;
    continue;
}
```

压缩完成后，同一个 Agent Turn 继续下一次采样。

### 3.3 模型变化

换模型时还有两个触发原因。

#### `CompHashChanged`

旧模型和新模型都声明了 compaction compatibility hash，且两个值不同。Core 先使用之前模型对应的上下文建立压缩检查点，再切换。

#### `ModelDownshift`

从大 Context Window 模型切换到小 Context Window 模型，并且现有用量已经超过新模型可接受阈值。Core 在新模型正式采样之前压缩。

## 4. 为什么 MidTurn 要立即插入 initial context

代码定义：

```rust
pub(crate) enum InitialContextInjection {
    BeforeLastUserMessage {
        world_state: Arc<WorldState>,
        step_context: Arc<StepContext>,
    },
    DoNotInject,
}
```

手动压缩和 PreTurn 压缩使用 `DoNotInject`：

- replacement history 先只安装压缩结果；
- `reference_context_item` 被清除；
- 下一次普通 Turn 发现没有 reference context，于是重新构建完整 initial context。

MidTurn 使用 `BeforeLastUserMessage`：

- 当前 Turn 压缩结束后马上还要调用模型；
- 不能等下一轮用户消息才恢复环境；
- Core 把 canonical initial context 插入最后一条真实 User Message 或 Compaction Item 之前；
- Compaction Summary/Item 仍保持在模型预期的末尾边界。

相关代码：

- [`core/src/compact.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/compact.rs)

## 5. 怎样替换活动 History

三条主要压缩路径最终都调用：

```rust
sess.replace_compacted_history(...).await;
```

源码：

- [`core/src/session/mod.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/mod.rs)

这个方法执行五件事：

1. 给 replacement history 中缺失 ID 的 item 分配 ID；
2. 创建可持久化的 `CompactedItem`；
3. 使用 `HistoryReplacement::Compaction` 替换内存中的活动 History；
4. 必要时保存新的完整 WorldState baseline 和 TurnContext；
5. 将检查点和相关状态写入 Rollout。

核心调用：

```rust
state.replace_annotated_history(
    items,
    reference_context_item.clone(),
    HistoryReplacement::Compaction,
);
```

替换完成后 Core 重新计算 token usage：

```rust
sess.recompute_token_usage(&turn_context).await;
```

## 6. `CompactedItem` 保存什么

定义位于：

- [`history/src/lib.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/history/src/lib.rs)

```rust
pub struct CompactedItem {
    pub message: String,
    pub replacement_history: Option<Vec<ResponseItemEnvelope>>,
    pub mcp_resource_origins: Option<McpResourceOriginCheckpoint>,
    pub window_number: Option<u64>,
    pub first_window_id: Option<String>,
    pub previous_window_id: Option<String>,
    pub window_id: Option<String>,
    pub compaction_response_id: Option<String>,
    pub latest_token_usage_record: Option<TokenUsageRecord>,
}
```

字段可以分成四组：

| 字段 | 作用 |
| --- | --- |
| `message` | 本地摘要文本；远程 opaque 路径可能为空 |
| `replacement_history` | 恢复模型活动上下文的完整检查点 |
| `window_*` | 标识压缩前后上下文窗口之间的关系 |
| `compaction_response_id` | 对应模型支持的压缩响应 ID |
| `mcp_resource_origins` | 保存 MCP Resource 来源检查点 |
| `latest_token_usage_record` | 恢复最近可达的 token usage 状态 |

## 7. 旧 History 是否删除

通常不会把 Rollout 中压缩前的行原地删除。写入顺序更接近：

```text
旧 Rollout items...
旧 Turn events...
CompactedItem { replacement_history: ... }
WorldState full baseline（如果需要）
TurnContext（如果需要）
Thread settings event
新 Turn items...
```

但是模型活动 History 已经切换到 `replacement_history`。所以：

```text
磁盘上存在旧记录
    !=
下一次模型仍能看到全部旧记录
```

这是理解 Rollout 与 Context 区别的关键。

## 8. Session 恢复怎样利用检查点

恢复逻辑位于：

- [`core/src/session/rollout_reconstruction.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/rollout_reconstruction.rs)
- [`rollout/src/model_context.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/rollout/src/model_context.rs)

重建器从新到旧扫描 Rollout。一旦找到最新、完整的：

```text
CompactedItem.replacement_history
+
对应 TurnContext / WorldState baseline
```

就可以把它作为模型上下文基线，再重放检查点之后的新 items，不需要从 Thread 的第一条消息开始重建全部模型输入。

```mermaid
flowchart LR
    Rollout[持久化 Rollout]
    Scan[从新到旧扫描]
    Compact[找到最新 CompactedItem]
    Base[载入 replacement_history]
    Suffix[重放检查点后的 items]
    Active[恢复活动 History]

    Rollout --> Scan --> Compact --> Base --> Suffix --> Active
```

旧格式如果没有 `replacement_history` 或 `window_number`，扫描器不能使用有界检查点，会继续向 Rollout 开头扫描以保持兼容性。

## 9. Hooks、事件和错误处理

### Hooks

每次压缩外围都有：

```text
PreCompact hook
压缩实现
PostCompact hook
```

Hook 可以中止流程。中止会被记录为 interrupted，而不是成功安装一半 History。

### 生命周期事件

Core 发出 `ContextCompactionItem` started/completed。客户端据此显示进度。

### 重试与上下文溢出

本地摘要流支持 stream retry。如果压缩请求本身超过 Context Window，并且还有多条输入，Core 会从最旧 History item 开始移除后重试，以尽量保留近期内容。

远程压缩之前还会优先把过大的 Function Call Output 改写为：

```text
Output exceeded the available model context and was truncated
```

这样既保持 Call/Output 配对结构，又为压缩请求腾出空间。

V2 对 transport retry 设置了比普通 Responses stream 更小的上限，防止一次压缩长时间反复重试。

## 10. 有损边界

Compaction 的目标是让任务可继续，不是无损归档。风险包括：

- 具体 Tool 输出被摘要成结论；
- 旧 Assistant 推理和中间尝试不再逐项存在；
- 临时 Skill 指令可能不再逐字保留；
- 连续多次对摘要再次压缩，会累积信息损失；
- 模型可能保留了结论，却遗漏结论背后的证据或精确数值。

Codex 在本地压缩完成后也会向客户端发送警告，提示长 Thread 和多次压缩可能降低准确性。工程上应该让 Thread 尽量聚焦；任务已经明显转向时，新建 Thread 往往比继续多次压缩更可靠。
