# History 与“谁能看到什么”

## 1. 先把“看到”拆成四个观察面

| 观察面 | 数据结构 | 目的 |
| --- | --- | --- |
| 持久化 | Rollout JSONL / `RolloutItem` | 恢复、审计运行过程、投影 Thread |
| Runtime | `SessionState` + `ContextManager` | 驱动当前 Agent Loop |
| 模型请求 | `Prompt.instructions/input/tools` | 当前这一次 LLM 调用 |
| 产品界面 | `Thread` / `Turn` / `ThreadItem` + events | 给用户展示会话与状态 |

“存在于 Session 文件”不等于“发给模型”；“发给模型”也不等于“UI 原样展示”。

## 2. 内容可见性总表

表中的“模型”指下一次普通采样请求，且仍要受 compaction、截断和 modality 过滤影响。

| 内容 | Rollout | 活动 History | 模型 | UI | 说明 |
| --- | --- | --- | --- | --- | --- |
| Base instructions | `SessionMeta` 可持久化 | 不作为普通 history item | 是，走 `Prompt.base_instructions` | 通常不显示 | 与 `input` 分开 |
| Tool schemas | 动态工具可在 metadata 保存；实时集合不靠历史保存 | 否 | 是，走 `Prompt.tools` | 通常只间接体现 | 每个 Step 重新计算可见集合 |
| 用户消息 | `ResponseItem`，另有 lifecycle event | 是 | 是 | 是 | 普通 user message |
| 助手消息 | `ResponseItem` | 是 | 是 | 是 | commentary/final 等 phase 可被保留 |
| Developer / Context fragments | `ResponseItem` | 是 | 是 | 通常不作为聊天消息展示 | 包含 initial context 或 diff |
| Tool Call | `ResponseItem` | 是 | 是 | 通常显示工具卡片/命令 | Function、Custom、Shell、Search 等形态 |
| Tool Result | `ResponseItem` | 是，写入时可能截断 | 是 | 通常显示结果或摘要 | MCP result 也会转成模型 output item |
| Reasoning item | `ResponseItem` | 是 | 是，具体内容受 provider 协议控制 | 通常只展示允许展示的摘要/状态 | 可能含 encrypted content |
| Compaction item / replacement | `CompactedItem` | 是，替换旧活动历史 | 是 | UI 可显示压缩事件 | 旧 JSONL 行仍可能存在 |
| Raw `WorldStateItem` | 是 | 不直接作为普通 item | 不直接发送 | 通常不显示 | 它生成的 rendered diff 才进 History |
| `TurnContextItem` | 是 | 只作 baseline metadata | 不直接发送 | 设置可能另行展示 | 用于恢复和增量判断 |
| Token/rate-limit 状态 | 可记录 | 在 SessionState 维护 | 通常否 | 是 | 运行状态，不是对话语义 |
| `EventMsg` | 是 | 否 | 否 | 是或驱动 UI | TurnStarted、Warning、Approval 等 |
| Security score | 是 | 否 | 否 | 视产品而定 | 控制/审计数据 |
| Realtime presentation facts | 是 | 独立状态 | 通常否 | 是 | 源码明确标为 model-invisible facts |

## 3. Tool Call 与 Tool Result 的完整生命周期

### 3.1 Call 从模型出来

模型流返回一个 `ResponseItem::FunctionCall`、`CustomToolCall` 等 item。`handle_output_item_done` 调用 `ToolRouter::build_tool_call`：

```text
模型输出 call
-> 解析为 ToolCall
-> 立即 record_completed_response_item
-> 写 ContextManager
-> 追加 RolloutItem::ResponseItem
-> 启动 tool future
```

先记录 call 很重要：即使工具执行慢、失败或 Turn 被中断，系统也知道模型请求过什么。

### 3.2 Result 从 Runtime 回来

工具 future 返回 `ResponseItemEnvelope`，`drain_in_flight` 调用 `record_annotated_conversation_items`。结果通常是：

```text
FunctionCallOutput
CustomToolCallOutput
ToolSearchOutput
```

MCP 的 `McpToolCallOutput` 会转换为 `FunctionCallOutput`，所以在模型协议层依然能和先前的 call 配对。

### 3.3 下一次模型采样

Agent Loop 因 `needs_follow_up` 继续：

```text
History = [..., ToolCall, ToolResult]
-> for_prompt()
-> Prompt.input
-> 模型读取工具结果并继续回答或再次调用工具
```

结论：**工具调用和工具结果都能进入模型后续上下文。**

## 4. 为什么又不能说“模型能看到所有工具结果”

### 4.1 写入时截断

`ContextManager::record_items_with_metadata` 对 `FunctionCallOutput` 和 `CustomToolCallOutput` 调用 `truncate_function_output_payload`。默认使用模型 truncation policy，并给工具序列化留一定余量；工具还可以通过 metadata 提供 fallback token limit。

这里还要再分一层：`record_prepared_conversation_items` 把准备后的 envelope 交给两条路径；`ContextManager` 克隆并按 policy 截断自己的活动副本，而 Rollout 持久化原 envelope。因而 Rollout 中可能保留比活动 History 更大的结果；Resume 回放时又会按当前模型 policy 构造活动副本。无论哪种情况，都不应假设下一次模型请求会拿到工具底层产生的无限原始 stdout 或 payload。

### 4.2 发送前规范化

`for_prompt` 会保证 call/output 成对，移除孤立 output，并根据模型输入能力移除不支持的图片、音频。

### 4.3 Compaction 替换活动历史

压缩后：

- 旧 Tool Call/Result 可能仍在 JSONL 的早期行；
- `ContextManager.items` 已换成 `replacement_history`；
- 模型只拿新的活动 History，不会扫描 JSONL 找旧结果；
- 关键结论可能被保留在摘要或 opaque compaction item 中，但不能保证逐字保留每个 result。

### 4.4 UI 可能使用不同投影

UI 使用 `ThreadItem` 和 lifecycle events 展示工具卡片，并不直接展示 `Prompt.input`。源码还对特定远程移动客户端的 `thread/resume` 响应执行展示层脱敏：MCP arguments/result 被替换，image-generation payload 被移除；注释明确说明这不会改变持久化 Rollout 或模型 resume history。

## 5. History 本身还保留什么

`ContextManager::is_api_message` 接受：

- 非 system 的 Message；
- AdditionalTools、AgentMessage；
- Function/Custom Tool Call 与 Output；
- ToolSearch Call 与 Output；
- LocalShellCall；
- Reasoning；
- WebSearchCall、ImageGenerationCall；
- Compaction、ContextCompaction。

它拒绝：

- system-role Message；
- `CompactionTrigger`，因为它只是一次请求控制；
- `Other`。

Base instructions 因而不依靠 system-role history item 维持，而由 `SessionConfiguration` 和 `Prompt.base_instructions` 单独维护。

## 6. Compaction 后还有一份 review history

第一次 compaction 时，`ContextManager` 会把原先的非上下文历史保存在 `review_history`，供审批审查等内部消费者使用；普通模型请求仍读取压缩后的 `items`。

这再次说明：

```text
Runtime 为内部安全判断保留的证据
!= 下一次模型请求使用的活动 History
```

显式 reset 会替换活动 History，也会重置这份 retained evidence；普通 compaction 只替换模型活动历史。

## 7. 最准确的一句话

> Rollout 尽量记录可恢复的运行事实；ContextManager 维护当前有效的模型历史；Prompt 只发送经过重建、截断、压缩和规范化后的活动 History，并把本轮工具定义作为独立字段附上。
