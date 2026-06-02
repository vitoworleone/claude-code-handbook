# 04 query_loop 详解

**这一篇讲整个项目最重要的函数：**

- `[cc/core/query_loop.py](../cc/core/query_loop.py)`

如果你要完全掌握这个项目的 agent 内核， **这个文件必须吃透。** 它不是“业务逻辑之一”，而是整台 runtime 的主状态机。

- 搞清楚整个loop循环
- 搞清楚Phase1-6的实现逻辑

> === 架构角色 ===

> query_loop 是整个系统的「心脏」——一个 while(true) 状态机，

> 驱动 "调用模型 → 解析响应 → 执行工具 → 拼回结果 → 再次调用模型" 的循环。

> 它是纯函数式的（所有依赖通过参数注入），不持有任何长期状态，长期状态在控制面

> 这使得它可以被 QueryEngine、AgentTool、测试 mock 等多种场景复用。

> === 状态机四阶段 ===

> 每次循环迭代包含四个阶段：

> **Phase 1 (准备):** normalize_messages_for_api() 转换消息格式 + auto-compact 检查

> **Phase 2 (调用):** call_model() 流式调用 API，期间通过 StreamingToolExecutor 提前启动工具

> **Phase 3 (恢复):** 错误处理与恢复（prompt_too_long / max_output_tokens / 429/529 重试）

> **Phase 4 (执行):** 收集工具执行结果，拼装 ToolResultBlock，追加到 messages

> === 循环的三个 continue 和退出条件 ===

> continue 1: Phase 3 错误恢复成功后 → 重试本轮（turn_count 不增加）

> continue 2: Phase 2 max_tokens 正常截断 → 追加 "请继续" 消息后继续

> continue 3: Phase 4 有工具调用 → 拼回结果后继续下一轮

> 退出 1: Phase 3 不可恢复错误 → yield ErrorEvent + return

> 退出 2: Phase 4 后无工具调用（stop_reason != tool_use）→ return（正常结束）

> 退出 3: turn_count >= max_turns → yield ErrorEvent（超限）

> === transcript 写入点（6处） ===

1. Phase 1: compact 后 messages.clear() + messages.extend(compacted)
2. Phase 3: prompt_too_long reactive compact 后同上
3. Phase 3: max_output_tokens recovery 追加 assistant + "请继续" 消息
4. Phase 2 后: max_tokens 正常截断追加 assistant + "请继续" 消息
5. Phase 4 前: 追加 AssistantMessage（模型本轮的输出）
6. Phase 4: 追加 UserMessage（工具执行结果）

> === 模块关系 ===

> 依赖: events.py（事件类型）、token_estimation.py、compact/compact.py、

> streaming_executor.py（P1b 流式工具执行）

> 被依赖: query_engine.py（唯一调用方）

> """

## **1. 一句话定义**

`query_loop()` 是一台围绕 transcript 运转的多阶段状态机：

```Plaintext
准备 transcript
  -> 调模型
  -> 归并流式事件
  -> 处理恢复逻辑
  -> 生成 assistant message
  -> 执行工具
  -> 生成 tool_result message
  -> 回到下一轮
```

![img](../assets/images/04 query_loop 详解.PNG)

![img](../assets/images/04 query_loop 详解2.png)

![img](../assets/images/04 query_loop 详解3.png)

![img](../assets/images/04 query_loop 详解4.png)

## **2. 先看输入参数的语义**

签名里每个参数都对应一个运行时职责：

- `messages`

当前完整 transcript。最核心状态。

- `system_prompt`

全局行为约束。

- `tools`

本轮允许模型调用的工具集合。

- `call_model`

一个统一的模型适配器，返回 `QueryEvent` 流。

- `max_turns`

单次用户输入的最大 agent 回合数。

- `auto_compact_fn`

用于长上下文压缩的额外模型调用。

- `context_window`

自动压缩的阈值参考。

- `hooks`

工具执行前后的扩展逻辑。

- `permission_checker`

权限门控回调。工具执行前调用，决定是否需要用户确认。为 `None` 时所有工具直接执行。

把这 9 个参数理解透了，整个函数就不神秘了。

## **3. 局部状态变量其实就是算法骨架**

函数顶部的局部变量非常值得仔细看，因为它们直接定义了运行时语义：

- `turn_count`
- `retry_count`
- `max_retry`
- `max_output_recovery_count`
- `has_attempted_reactive_compact`
- `compact_consecutive_failures`
- `current_max_tokens`
- `last_error`

这几个变量实际上就是 4 个子状态机的状态寄存器：

### **回合控制**

- `turn_count`
- `max_turns`

### **网络/服务恢复**

- `retry_count`
- `max_retry`
- `last_error`

### **长输出恢复**

- `max_output_recovery_count`
- `current_max_tokens`

### **长上下文恢复**

- `has_attempted_reactive_compact`
- `compact_consecutive_failures`

也就是说， `query_loop()` 不是单一循环，而是把多个局部恢复策略叠在同一个 while 循环里。

## **4. Phase 1：规范化 transcript（Message）**

代码位置：

- `[cc/core/query_loop.py](../cc/core/query_loop.py)`

```
api_messages = normalize_messages_for_api(messages)
```

这一阶段的职责非常关键：

> 把“内部历史”变成“模型 API 能安全接受的历史”。

为什么这里是核心？

因为内部 transcript 未必永远合法。比如：

- 可能有连续两个 user message
- 可能有连续两个 assistant message
- 可能有孤儿 `tool_result`
- compact 之后可能插入边界消息

所以系统必须在“每次发给 API 之前”做一次协议层修正。

这也是为什么 `normalize_messages_for_api()` 不是可有可无的 helper，而是 runtime 必需模块。

## **5. Phase 2：auto-compact 判定**

代码位置：

- `[cc/core/query_loop.py](../cc/core/query_loop.py)`

```
should_auto_compact(...)
```

- `[cc/compact/compact.py](../cc/compact/compact.py)`

这里的逻辑可以理解成：

```Plaintext
如果估算 token 快撞 context window：
    调 compact_messages()
    把旧消息总结成 CompactBoundaryMessage
    再继续本轮
```

这一步的算法目标不是“做最优压缩”，而是“尽可能低成本地把对话继续跑下去”。

值得注意的工程点：

- 它用的是粗略 token 估算，不是精确 tokenizer
- 压缩失败不会直接崩，会记一次失败并继续
- 失败次数太多会停掉 auto-compact，避免无限抖动
- 

## **6. Phase 3：调用模型并消费事件流**

代码位置：

- `[cc/core/query_loop.py](../cc/core/query_loop.py)`

```
async for event in call_model(...)
```

这一层的关键不是“调模型”，而是：

> `query_loop()` 完全不关心底层 SSE 细节，它只消费统一的 `QueryEvent` 。

这是一层很重要的抽象边界：

- `api/claude.py` 负责把外部协议变成内部事件
- `query_loop.py` 只处理内部事件，不处理 SSE 细节

这样做的好处是：

- runtime 核心不会被 API SDK 细节污染
- 测试可以直接 mock `QueryEvent`
- 未来替换 provider 时，query loop 基本不用动

## **7. Phase 4：流式归并**

在流式循环里，它并不立即构造 `AssistantMessage` ，而是先把本轮状态归并到这些临时变量里：

- `accumulated_text`
- `usage`
- `stop_reason`
- `tool_use_blocks`
- `error_event`

### **为什么必须先归并，再落盘到 transcript**

因为一轮模型响应在流式模式下是分片到达的：

- 文本是 delta
- thinking 是 delta
- tool_use 是先有 start，再有完整输入
- stop_reason 和 usage 往往最后才知道

所以运行时必须经历两个阶段：

1. 事件归并阶段
2. transcript materialization 阶段

这和很多 streaming agent 实现的常见结构是一致的。

### **为什么`ToolUseStart` 这么重要**

当收到 `ToolUseStart` 时，query loop 会做两件事：

1. 把它 append 到 `tool_use_blocks` （用于后续构造 `AssistantMessage` ）
2. 调用 `StreamingToolExecutor.add_tool(block)` ，让工具在流式期间就开始执行

`StreamingToolExecutor` 的核心设计是：并发安全的工具立即启动，非安全工具排队等待。当整个流式响应结束后，调用 `executor.get_results()` 收集所有工具的执行结果。

但从 transcript 的视角看，顺序仍然必须是：

```Plaintext
assistant 先说”我要调工具”（AssistantMessage 先落盘）
然后 tool_result 才能进入 transcript
```

这不是实现偏好，而是协议层要求。 `StreamingToolExecutor` 只是在不破坏 transcript 顺序的前提下，提前启动了工具执行以节省延迟。

## 8. **Phase 5：错误恢复不是补丁，而是主流程的一部分**

很多人第一次看会把 recovery 当成“边角逻辑”，这是错的。

在 agent runtime 里，恢复逻辑本来就是主流程的一部分。

### **8.1`prompt_too_long`**

处理方式：

- 尝试 reactive compact
- 如果 compact 真的让 transcript 变短，就继续本轮

这体现的是：

> 长上下文溢出优先用 transcript 变换来恢复，而不是立刻报错。

### **8.2`max_output_tokens`**

这是另一个典型 runtime 场景。

处理策略是两步：

1. 先把 `current_max_tokens` 升到更高值
2. 如果仍然被截断，就把当前文本落回 transcript，并补一条：

```Plaintext
Please continue from where you left off.
```

也就是说，它不是简单“重试同一次请求”，而是显式把“继续输出”建模成下一轮对话的一部分。

### **8.3 recoverable API error**

如果错误被标记成 recoverable：

- 重试
- sleep 退避
- 不消耗真实 turn budget

这一点极其关键。否则 agent 很容易出现：

```Plaintext
底层只是连接错误
但上层最后看到的是 Max turns reached
```

### **8.4. transcript materialization：什么时候真正 append assistant message**

当流结束且没有立即退出时，query loop 会把本轮归并结果真正变成一个 `AssistantMessage` 。

这一步对应运行时语义是：

> 到这里为止，这一轮 assistant 的“意图”才算被正式写进系统状态。

assistant message 里有两类信息：

- 文本回答
- `tool_use_blocks`

这一点很重要，因为 assistant message 不是纯文本，而是“文本 + 行动意图”的组合。

## **9. Phase 6：为什么工具结果要被包装成新的`UserMessage`**

代码位置：

- `[cc/core/query_loop.py](../cc/core/query_loop.py)`

构造 `ToolResultBlock`

这一步是初学者最常困惑的点之一。

为什么工具明明是 runtime 执行的，结果却要变成 user message？

答案是：Anthropic 对话协议就是这么规定的。

语义上：

- assistant 产生命令： `tool_use`
- 外部世界返回执行结果： `tool_result`

在 transcript 里， `tool_result` 被放在 user 侧，是因为从模型视角看，这是“外界给它的反馈”。

这意味着工具执行本质上不是副作用而已，而是：

> 一次会话协议中的显式消息交换。

## **10.`stop_reason` 不是绝对可信的**

当前实现专门修了一点：

> 是否进入工具执行，不只看 `stop_reason == "tool_use"` ，而是看 `tool_use_blocks` 是否真的存在。

这非常合理，因为在真实 API 交互里：

- `stop_reason` 可能不稳定
- 但 transcript 中有没有 `tool_use block` 才是更坚实的事实

这类设计属于 runtime 的“协议健壮性”问题，而不是代码风格问题。

## **11. 抽象成伪代码最容易看懂**

可以把整个函数压成下面这样：

```Python
while turn_count < max_turns:
    api_messages = normalize(messages)
    maybe_auto_compact()

    executor = StreamingToolExecutor(registry, hooks, permission_checker)

    async for event in call_model(api_messages):
        accumulate(event)
        if event is ToolUseStart:
            executor.add_tool(block)       # 流式期间就开始执行

    if error:
        if recoverable:
            repair_or_retry()
            continue
        else:
            return error

    append_assistant_message()

    if tool_use_blocks:
        results = await executor.get_results()  # 等待所有工具完成
        append_tool_results_as_user_message(results)
        continue
    return
```

## **12.`query_loop()` 实际修改全局状态的时机只有 6 个**

这点非常重要。

很多人第一次读这个文件，会以为 `messages` 在函数里一直被频繁改写。其实真正的写点很少，主要只有 6 个：

### **写点 1：auto-compact 成功后**

```Plaintext
messages.clear()
messages.extend(compacted)
```

这是一次 transcript 级替换。

### **写点 2：`max_tokens` 恢复时**

如果已经产生了部分文本，会先 append：

- 一个 `AssistantMessage(TextBlock(accumulated_text))`
- 一个 `UserMessage(“Please continue from where you left off.”)`

这是把”继续生成”显式建模成下一轮对话。

### **写点 3：正常一轮结束时**

把本轮 assistant materialize 成：

- `AssistantMessage([TextBlock, ToolUseBlock, ...])`

### **写点 4：工具执行完成后**

把工具结果 materialize 成：

- `UserMessage([ToolResultBlock, ToolResultBlock, ...])`

### **写点 5：reactive compact（`prompt_too_long` 恢复时）**

当 API 返回 `prompt_too_long` 错误时，会触发一次 reactive compact：

```Plaintext
compact_messages(messages)
messages.clear()
messages.extend(compacted)
```

和 auto-compact 类似，但这是被动触发的紧急压缩，而不是预防性检查。它使用 `has_attempted_reactive_compact` 标记防止无限循环。

### **写点 6：错误恢复中的长输出续写**

当 `max_output_tokens` 恢复仍被截断时，会把已产生的部分文本落回 transcript，并追加一条续写请求：

- 一个 `AssistantMessage(TextBlock(accumulated_text))`
- 一个 `UserMessage(“Please continue from where you left off.”)`

这和写点 2 的机制相同，但发生在多次截断恢复的路径上，每次截断都会追加一组，因此 transcript 上可能出现连续多段”部分输出 + 续写请求”。

这 6 个写点，就是这台状态机真正推进全局状态的地方。

## **13. 为什么它能既 streaming，又保持 transcript 一致性**

这里其实有一个典型的“双层状态”设计：

### **UI/外部观察层**

通过 `yield TextDelta` 、 `yield ToolUseStart` 等事件，外部可以实时看到进度。

### **内部 source-of-truth 层**

只有在本轮语义完整后，才真正写回 `messages` 。

这样做的好处是：

- 用户可以实时看到流式输出
- 内部 transcript 不会被半截状态污染

换句话说：

> 事件流是实时观察接口， `messages`  是正式账本。

## **14. 为什么`TurnComplete` 会被 yield 两次语义上不同的场景**

这个文件里要特别注意一点：

- 一次模型调用结束时会收到 provider 层的 `TurnComplete`
- 但 `query_loop()` 自己也会在某些恢复路径或 materialization 后 yield `TurnComplete`

所以这里的 `TurnComplete` 更像“本轮对上层可见的阶段完成通知”，而不是“原始 provider 事件原样透传”。

成熟工程师看这里时，要把它理解成：

> `QueryEvent` 是运行时总线事件，不是 SDK event dump。

## **15. 对算法工程师最有价值的抽象【重要】**

> 如果用更算法化的方式概括， `query_loop()`  可以看成下面 4 个函数串起来：

> **`normalize`**

> 把内部状态映射成合法输入状态。

> **`infer`**

> 调用 planner，得到文本增量、行动意图或错误。

> **`materialize`**

> 把本轮局部结果写回 transcript。

> **`act`**

> 执行工具并把世界反馈重新变成 transcript。

> 这 4 个函数周围，再套上 3 套恢复策略：

- context overflow recovery
- output truncation recovery
- transient API failure recovery

> 理解成这组逻辑之后，你再去想如何 **替换规划器、改动作空间、接记忆系统** ，都会清楚很多。

## **18. 这一篇最适合怎么配合源码读**

我建议直接在编辑器里把 `[cc/core/query_loop.py](../cc/core/query_loop.py)` 分成下面 8 段读，而不是从头硬扫：

1. 常量与局部状态定义
2. while turn loop
3. transcript normalize
4. auto-compact
5. `async for event in call_model(...)`
6. error recovery
7. assistant materialization
8. tool execution and re-entry

每读完一段，就问自己一句：

> 这一段到底是在”观察事件”，还是在”修改 source of truth”？

这个问题一旦想清楚，整个文件就不会乱。