# 05 消息定义

> 这一篇解决两个问题：

1. transcript（Message） 到底长什么样
2. agent 的“行动能力”到底是怎么接进来的

> 如果上一章讲的是状态机，这一章讲的就是状态机的“状态表示”和“动作接口”。

# `0.cc/models/` 数据层

`cc/models/` 是整个系统的数据层——定义了所有模块共用的数据结构。3 个文件各管一件事：

| 文件              | 定义了什么                                                   | 谁在用                                      |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------- |
| content_blocks.py | 7 种内容块：TextBlock、ToolUseBlock、ToolResultBlock、ThinkingBlock 等 | API 层序列化、query_loop 构建消息、工具执行 |
| messages.py       | 4 种消息：UserMessage、AssistantMessage、SystemMessage、CompactBoundaryMessage，以及 normalize_messages_for_api() | query_loop 的核心状态、会话持久化、API 调用 |
| state.py          | 配置/状态容器：AppConfig、QueryState、ThinkingConfig、AutoCompactTracking | 主要是类型定义，被其他模块引用              |

**关键特征：** 这个文件夹不依赖任何其他 `cc/` 子包（零 import），但被几乎所有其他模块依赖—— `core/` 、 `api/` 、 `tools/` 、 `compact/` 、 `session/` 都 import 它。所以它是依赖关系图的最底层。

> 如果整个系统是一条流水线， `models/`  就是流水线上流转的零件规格图纸——它不参与加工，但每个工位都按它的规格来造零件、检查零件。

![img](../assets/images/05 消息定义.PNG)

![img](../assets/images/05 消息定义1.PNG)

## **1. 先从`Message` 和 `ContentBlock` 分层开始**

这个项目不是“消息里放字符串”，而是两层结构：

```Plaintext
Message
  -> role / metadata
  -> content

content
  -> 一个字符串
  -> 或一组结构化 content blocks
```

对应代码：

- `[cc/models/messages.py](../cc/models/messages.py)`
- `[cc/models/content_blocks.py](../cc/models/content_blocks.py)`

## **2.`Message` 层：谁在说话**

**`UserMessage`**

表示用户侧输入，可能是：

- 普通文本
- 工具结果 block
- 图片

**`AssistantMessage`**

表示模型侧输出，可能包含：

- `TextBlock`
- `ToolUseBlock`
- `ThinkingBlock`
- `RedactedThinkingBlock`

**`SystemMessage`**

系统内部信息，不会直接原样发给 API。

**`CompactBoundaryMessage`**

表示“更早的历史已经被压缩成摘要”。

这不是 UI 概念，而是 transcript 的结构边界。

## **3.`ContentBlock` 层：说了什么 / 要做什么**

这一层最重要的 block 有 4 类：

**`TextBlock`**

普通文本。

**`ToolUseBlock**

模型请求调用工具。

关键字段：

- `id`
- `name`
- `input`

**`ToolResultBlock`**

runtime 执行完工具后返回给模型的结果。

关键字段：

- `tool_use_id`
- `content`
- `is_error`

**`ToolResultContent`**

工具结果内部的细粒度结构，支持：

- 文本
- 图片

这个设计意味着工具结果并不一定只能是平面字符串。

```Python
ToolResultBlock
├── tool_use_id: "tool_abc"
├── is_error: False
└── content:
      情况一（简单）: "命令执行成功"              ← 直接是字符串
      情况二（复杂）: [                           ← ToolResultContent 列表
            ToolResultContent(type="text",  text="执行结果如下"),
            ToolResultContent(type="image", source={...}),  ← 附带截图
      ]
```

### 什么时候用哪种

| 场景                              | content 的类型          |
| --------------------------------- | ----------------------- |
| 工具只返回文本                    | 直接是 str              |
| 工具返回文本 + 图片（如截图工具） | list[ToolResultContent] |

`ToolResultContent` 本身不会单独出现在 transcript 里，它只作为 `ToolResultBlock.content` 的列表元素存在。

## **4. 为什么要用 block，而不是直接拼字符串**

因为 agent runtime 需要保留协议级语义。

比如下面两句话在字符串层看起来都像文本：

```Plaintext
我想调用 Bash
Bash 返回的结果是 42
```

但在协议层，它们完全不是一回事：

- 前者是 `tool_use`
- 后者是 `tool_result`

如果全都压平成字符串：

- 模型没法稳定理解
- transcript pairing 没法做
- 后续恢复和校验会变弱

所以 block 设计的意义是：

> 把“对话内容”和“运行时动作协议”同时保留在 transcript 里。

## **5.`normalize_messages_for_api()` 是整个协议层的守门员**

定义在：

- `[cc/models/messages.py](../cc/models/messages.py)`

它不是简单 serializer，而是“协议修复器”。

### **它做什么**

#### **1. 过滤系统内部消息**

- `SystemMessage` 不直接发给 API
- `CompactBoundaryMessage` 被转成一条 user 摘要消息

#### **2. 保证角色交替**

如果连续两个 user：

- 合并成一个 user

如果连续两个 assistant：

- 中间插一个 `"Continue."`

#### **3. 工具配对修复（三步双向）**

`_ensure_tool_result_pairing()` 做的不是单向清理，而是三步双向修复：

**Pass 1：收集所有 ID**

扫描全部消息，分别收集所有 `tool_use` 的 `id` 和所有 `tool_result` 的 `tool_use_id` 。

**Pass 2：删除 orphan tool_result**

如果某个 `tool_result` 的 `tool_use_id` 在 `tool_use_ids` 中找不到对应项，直接删掉。如果删除后用户消息变成空 content，会填充占位文本 `"(no content)"` 。

**Pass 3：为 orphan tool_use 注入合成结果**

如果某个 `tool_use` 的 `id` 在 `tool_result_ids` 中找不到匹配项，就在下一条用户消息里注入一个合成的错误结果：

```Python
{
    "type": "tool_result",
    "tool_use_id": tid,
    "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,  # "[Tool result missing due to internal error]"
    "is_error": True,
}
```

如果后面没有用户消息，就追加一条新的用户消息。

这是整个 transcript 修复逻辑中最关键的部分，保证了 API 调用前 tool_use 和 tool_result 始终成对。

## **6. 为什么 “工具协议正确性” 比 “输出文本正确性” 更重要**

> 对 agent 来说，如果普通文本少一句话，通常问题不大。

> 但如果下面任一条件坏掉，整个回合都可能失效：

- `tool_use_id` 对不上
- 连续角色顺序不合法
- orphan `tool_result` 没被清掉
- assistant/tool_result 落历史顺序错了

> 所以成熟工程师读这类项目时要优先看：

> transcript invariant 是否稳定，而不是 prompt 文案是否漂亮。

## **7. 工具系统的最小抽象**

位置：

- `[cc/tools/base.py](../cc/tools/base.py)`

**`ToolSchema`**

给模型看的说明书：

- 名字
- 描述
- 输入 JSON schema

**`Tool`**

每个工具都必须实现：

- `get_name()`
- `get_schema()`
- `async execute(tool_input)` -- 注意这是 `async` 方法

可选地实现：

- `is_concurrency_safe(tool_input)`

**`ToolResult`**

工具返回统一格式：

- `content: str | list[dict[str, Any]]` -- 支持纯文本，也支持结构化内容（图片、MCP 多段结果等）
- `is_error: bool`

此外还有一个 `text` 属性（ `@property` ），它的作用是：无论 `content` 是字符串还是 block 列表，都能提取出纯文本表示。当 `content` 是列表时，它会遍历每个 block 取 `text` 字段拼接。

这就是整个 runtime 的动作接口。

## **8.`ToolRegistry` 为什么是核心对象**

`ToolRegistry` 不是单纯 dict，它同时承担两种角色：

### **运行时查找表**

根据名字找到真实工具实现。

### **API schema 生成器**

把所有工具转换成给模型看的 schema 列表。

也就是说：

> 同一套 registry 同时连接了“模型侧决策空间”和“本地执行侧能力空间”。

## **9. 模型和工具的关系不是“调用函数”，而是“协商协议”**

流程是这样的：

1. runtime 把工具 schema 发给模型
2. 模型选择是否使用某个工具
3. 模型产出 `tool_use`
4. runtime 根据 `tool_use.name` 查表执行
5. runtime 生成 `tool_result`
6. 模型在下一轮基于 `tool_result` 继续推理

所以：

- 模型不是直接执行工具
- runtime 也不是替模型做决策

这是一种比较标准的 planner/executor 协议分层。