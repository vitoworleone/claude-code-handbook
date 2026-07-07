# Python 源码解析：让 AI 开口说话

## 模块概览

LLM 通信的代码集中在两个文件里：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `mewcode/client.py` | 301 | LLM 客户端抽象、Anthropic/OpenAI 实现、流式处理、错误分类 |
| `mewcode/conversation.py` | 189 | Message dataclass、ConversationManager 对话管理、双协议序列化 |

两个文件加起来不到 500 行，但覆盖了 LLM 通信的全部核心逻辑。Python 的表达密度很高：dataclass 自动生成构造函数和比较方法，async generator 天然就是流式事件的载体，Union 类型让事件族的定义简洁到一行。

流式事件类型定义在 `mewcode/tools/base.py` 的后半段，和工具基类放在一起。这个文件同时服务两个模块，是连接 LLM 层和工具层的桥梁。

## 核心类型

### LLMClient：抽象基类

Python 版用 ABC（Abstract Base Class）定义抽象基类，所有 LLM 客户端都必须继承它：

```Python
class LLMClient(ABC):
    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("")
```

注意返回类型是 `AsyncIterator[StreamEvent]` 。Python 的 async generator 天然就是一个异步迭代器，调用方用 `async for event in client.stream(...)` 消费事件就行，不需要手动管理任何并发原语的创建和关闭。

还有个小细节：方法体里有一句 `yield TextDelta("")` 。这不是真的要返回什么，而是为了让 Python 类型检查器认出这是一个 async generator。没有这个 yield，类型推导会把返回值当成普通的 coroutine，子类重写时类型就对不上了。

### StreamEvent：Union 类型

Python 版用 Union 类型来定义事件族：

```Python
StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
```

七种事件，每种都是一个 dataclass。Python 3.10 引入的 `X | Y` 语法让联合类型写起来非常自然。类型检查器看到 `StreamEvent` 就知道它只能是这七种之一，在 match/if 分支里能做穷尽性检查。

每个事件的定义都很紧凑，拿 `ToolCallComplete` 举例：

```Python
@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
```

三行定义，自动生成 `__init__` 、 `__eq__` 、 `__repr__` 。不需要任何额外的标记方法或接口约束，dataclass 本身就携带了完整的类型信息。

### Message dataclass

对话历史的基本单元是 `Message` ，五个字段覆盖所有情况：

```Python
@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
```

一条 Message 可以同时携带文本、工具调用和思考块，因为 Anthropic 的 assistant 消息确实能包含这三者。 `field(default_factory=list)` 是 Python dataclass 的固定写法，确保每个实例拿到独立的空列表，而不是共享同一个对象。

`ToolUseBlock` 、 `ToolResultBlock` 、 `ThinkingBlock` 也都是 dataclass，各自只有两三个字段：

```Python
@dataclass
class ToolUseBlock:
    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any]

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str
```

这些小结构体看着平淡无奇，但它们构成了内部消息模型的全部词汇。上层代码不需要知道 Anthropic 的 `content_block` 长什么样，只认这几个 dataclass。

## 主流程走读

从上层调用到底层 SSE 事件，以 `AnthropicClient.stream()` 为例走一遍完整链路。

### 第一步：构造客户端

`AnthropicClient.__init__` 做三件事：保存配置、解析 API key、创建 SDK 客户端。

```Python
class AnthropicClient(LLMClient):
    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.thinking = config.thinking
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "Anthropic API key not found. "
                "Set it in config.yaml or via ANTHROPIC_API_KEY env var."
            )
        self._client = AsyncAnthropic(api_key=api_key, base_url=config.base_url)
```

注意用的是 `AsyncAnthropic` 而非 `Anthropic` 。整个 MewCode Python 版是纯 async 架构，从入口到底层全是异步调用。API key 找不到就立刻报错，不等到真正发请求才失败，这是 fail-fast 原则。

工厂函数 `create_client` 根据协议分发，决定实例化哪个具体客户端：

```Python
def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config)
    elif config.protocol == "openai":
        return OpenAIClient(config)
    raise ValueError(f"Unknown protocol: {config.protocol}")
```

### 第二步：构建请求参数

`stream()` 方法开头先序列化对话历史，然后用字典组装请求参数：

```Python
messages = conversation.serialize("anthropic")

kwargs: dict[str, Any] = {
    "model": self.model,
    "max_tokens": self.max_output_tokens,
    "messages": messages,
}
if system:
    kwargs["system"] = system
if tools:
    kwargs["tools"] = tools
```

用 kwargs 字典而不是固定参数列表，是因为 Anthropic API 的可选参数很多，有些在特定条件下才需要传。空字段不传比传 None 更安全，有些 API 对 `null` 和字段缺失的处理不一样。

### 第三步：Thinking 配置

Thinking 是 Anthropic 的扩展思维功能，配置逻辑有一个分支：

```Python
if self.thinking:
    if _supports_adaptive_thinking(self.model):
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": 0,
        }
    else:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": max(
                self.max_output_tokens - 1, 1024
            ),
        }
```

`budget_tokens: 0` 表示自适应思考，让模型自己决定要思考多久。但这个功能只有较新的模型才支持。 `_supports_adaptive_thinking` 通过解析模型名里的版本号来判断：

```Python
def _supports_adaptive_thinking(model: str) -> bool:
    for family in ("claude-opus-4-", "claude-sonnet-4-"):
        if model.startswith(family):
            rest = model[len(family):]
            if rest and rest[0].isdigit() and int(rest[0]) >= 6:
                return True
    return False
```

这个函数用字符串前缀匹配来识别模型家族，再检查版本号。版本号 >= 6 的模型支持自适应思考，老版本只能手动指定预算。预算设为 `max_output_tokens - 1` 是因为 Anthropic API 要求 thinking budget 严格小于 max\_tokens，减一就刚好卡在边界内。

### 第四步：SSE 事件循环

参数准备好后，进入流式处理的核心。Python 的 `async with` 和 `async for` 让代码读起来几乎是同步的：

```Python
async with self._client.messages.stream(**kwargs) as stream:
    async for event in stream:
        if event.type == "content_block_start":
            block = event.content_block
            if block.type == "thinking":
                in_thinking = True
                thinking_accum = ""
            elif block.type == "tool_use":
                current_tool_name = block.name
                current_tool_id = block.id
                json_accum = ""
                yield ToolCallStart(
                    tool_name=current_tool_name,
                    tool_id=current_tool_id,
                )
```

`async with` 确保流在退出时自动关闭，不管是正常结束还是异常退出。上下文管理器天然处理了资源释放的所有边界情况，不需要手动 close。

整个事件循环是一个三层 if/elif 结构：外层区分 `content_block_start` 、 `content_block_delta` 、 `content_block_stop` ；中层区分 block 类型（thinking / tool\_use / text）；内层做具体的事件分发。

`yield` 关键字是 async generator 的核心。每次 yield 都暂停当前函数，把事件交给调用方处理。调用方处理完了，执行权返回这里继续读下一个 SSE 事件。这种协作式调度非常轻量，没有缓冲区管理的心智负担，天然就是背压友好的。

### 第五步：delta 处理与 JSON 累积

`content_block_delta` 阶段处理四种增量数据：

```Python
elif event.type == "content_block_delta":
    delta = event.delta
    if delta.type == "text_delta":
        yield TextDelta(text=delta.text)
    elif delta.type == "thinking_delta":
        thinking_accum += delta.thinking
        yield ThinkingDelta(text=delta.thinking)
    elif delta.type == "signature_delta":
        thinking_signature = delta.signature
    elif delta.type == "input_json_delta":
        json_accum += delta.partial_json
        yield ToolCallDelta(text=delta.partial_json)
```

文本和思考内容是收到就 yield，实时推给上层。工具参数的 JSON 片段需要累积，因为 `{"file_path": "/home/user/test.py"}` 可能被拆成 `{"file_` 和 `path": "/hom` 和 `e/user/test.py"}` 三个片段到达，单个片段是不合法的 JSON，必须攒齐了才能解析。

思考签名（ `signature_delta` ）比较特殊，只存不 yield。签名是 Anthropic 用来验证思考内容完整性的密码学签名，UI 不需要展示它，但序列化消息时必须带上。

### 第六步：block 结束与最终消息

`content_block_stop` 是每个内容块的收尾：

思考块结束时，yield 一个 `ThinkingComplete` ，把累积的文本和签名一起交出去：

```Python
elif event.type == "content_block_stop":
    if in_thinking:
        yield ThinkingComplete(
            thinking=thinking_accum,
            signature=thinking_signature,
        )
        in_thinking = False
```

工具调用块结束时，把累积的 JSON 片段一次性解析，然后 yield `ToolCallComplete` ：

```Python
    if current_tool_name:
        try:
            args = json.loads(json_accum) if json_accum else {}
        except json.JSONDecodeError:
            args = {}
        yield ToolCallComplete(
            tool_id=current_tool_id,
            tool_name=current_tool_name,
            arguments=args,
        )
        current_tool_name = ""
```

这里有个防御性设计：JSON 解析用了 try/except。正常情况下 LLM 返回的 JSON 一定是合法的，但网络中断可能导致 JSON 不完整。与其让整个流式处理崩溃，不如给一个空字典，让上层的工具执行阶段去报参数缺失的错误。

整个流结束后，通过 SDK 拿到最终的 usage 信息：

```Python
final = await stream.get_final_message()
yield StreamEnd(
    stop_reason=final.stop_reason or "end_turn",
    input_tokens=final.usage.input_tokens,
    output_tokens=final.usage.output_tokens,
)
```

`stop_reason` 告诉上层 LLM 为什么停了。 `"end_turn"` 表示模型认为说完了， `"tool_use"` 表示模型想调用工具。Agent Loop 根据这个值决定是结束循环还是继续执行工具。

## 两层消息模型

### 内层：ConversationManager

`ConversationManager` 用一个 `list[Message]` 管理整段对话历史，提供一组追加方法：

```Python
@dataclass
class ConversationManager:
    history: list[Message] = field(default_factory=list)
    env_injected: bool = field(default=False, init=False)
    ltm_injected: bool = field(default=False, init=False)
    last_input_tokens: int = field(default=0, init=False)
```

`env_injected` 和 `ltm_injected` 是两个标志位，确保环境信息和长期记忆只注入一次。 `init=False` 意味着这两个字段不出现在构造函数里，外部不能在创建时指定它们的值。

`add_system_reminder` 把系统提醒包装成一条特殊的 user 消息：

```Python
def add_system_reminder(self, content: str) -> None:
    self.history.append(
        Message(
            role="user",
            content=f"<system-reminder>\n{content}\n</system-reminder>",
        )
    )
```

为什么系统提醒是 user 角色？因为 Anthropic API 的 system 参数只有一个，已经被系统提示词占了。后续动态注入的提醒只能塞进 user 消息里，用 XML 标签包裹来告诉模型这是系统级信息。

### 外层：双协议序列化

`serialize()` 方法把内部 Message 列表转成 API 要求的格式，一个方法覆盖两种协议：

```Python
def serialize(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
    if protocol == "openai":
        return self._serialize_openai()
    return self._serialize_anthropic()
```

### Anthropic 序列化：思考块与消息合并

`_serialize_anthropic` 有两个关键细节。第一个是思考块的序列化。当 assistant 消息包含思考块或工具调用时，content 字段从字符串变成列表：

```Python
if m.tool_uses or m.thinking_blocks:
    content: list[dict[str, Any]] = []
    for tb in m.thinking_blocks:
        content.append({
            "type": "thinking",
            "thinking": tb.thinking,
            "signature": tb.signature,
        })
    if m.content:
        content.append({"type": "text", "text": m.content})
```

工具调用紧跟在文本后面，每个 `ToolUseBlock` 变成一个 `tool_use` 类型的 content item：

```Python
    for tu in m.tool_uses:
        content.append({
            "type": "tool_use",
            "id": tu.tool_use_id,
            "name": tu.tool_name,
            "input": tu.arguments,
        })
```

顺序是 thinking → text → tool\_use。这不是随意的，Anthropic API 要求思考块必须出现在文本之前。如果模型先思考再回复再调工具，序列化后的顺序必须和产生顺序一致。

第二个关键细节是 system-reminder 的合并。Anthropic API 要求消息严格按 user/assistant 交替排列，但 MewCode 内部会在用户消息后追加 system-reminder（也是 user 角色），产生连续同角色消息：

```Python
is_reminder = m.content.startswith("<system-reminder>")
if is_reminder and result and result[-1]["role"] == "user":
    prev = result[-1]
    if isinstance(prev["content"], str):
        prev["content"] = prev["content"] + "\n" + m.content
    elif isinstance(prev["content"], list):
        prev["content"].append({"type": "text", "text": m.content})
```

如果前一条也是 user 消息，就把 system-reminder 追加到前一条里，不单独成为一条新消息。这里还区分了 content 是字符串还是列表的情况，因为包含 tool\_result 的 user 消息的 content 是列表格式。

### OpenAI 序列化：扁平结构

OpenAI 的 Responses API 用 input item 列表，结构比 Anthropic 扁平得多：

```Python
def _serialize_openai(self) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in self.history:
        if m.tool_uses:
            if m.content:
                result.append({"role": "assistant", "content": m.content})
            for tu in m.tool_uses:
                result.append({
                    "type": "function_call",
                    "name": tu.tool_name,
                    "call_id": tu.tool_use_id,
                    "arguments": json.dumps(tu.arguments),
                })
```

OpenAI 不需要消息交替的合并逻辑，也不处理思考块（OpenAI 没有 thinking 功能）。工具调用是独立的 item，不嵌在 assistant 消息的 content 里。 `arguments` 是 JSON 字符串而不是字典，这是 OpenAI API 的格式要求。

### 长期记忆注入

`inject_long_term_memory` 把项目指令和自动记忆包装成一条 `<system-reminder>` 消息，插入对话历史的开头：

```Python
def inject_long_term_memory(
    self, instructions: str, memories: str
) -> None:
    if self.ltm_injected:
        return
    sections: list[str] = []
    if instructions:
        sections.append(
            "# mewcodeMd\n"
            "Codebase and user instructions are shown below. "
            "Be sure to adhere to these instructions. ...\n\n" + instructions
        )
    if memories:
        sections.append("# autoMemory\n" + memories)
    if not sections:
        return
    sections.append(f"# currentDate\nToday's date is {date.today().isoformat()}.")
    body = "\n\n".join(sections)
    wrapped = (
        "<system-reminder>\n"
        "As you answer the user's questions, you can use the following context:\n"
        + body + "\n...\n</system-reminder>"
    )
    pos = 1 if self.env_injected else 0
    self.history.insert(pos, Message(role="user", content=wrapped))
    self.ltm_injected = True
```

这个方法对齐了 Claude Code 的注入方式：把所有上下文信息（项目指令、自动记忆、当前日期）合并进一条 `<system-reminder>` 标签包裹的 user message，插到对话最前面。 `pos` 的计算考虑了环境信息是否已注入：如果已注入就插到第二条，否则插到头部。整个注入只生成一条消息，不需要额外的 assistant 确认消息来维持交替结构。

## 流式响应处理

### async generator 的流式语义

Python 的 async generator 是协作式的：yield 暂停生产者，next 恢复生产者，两边在同一个事件循环线程里交替执行。这意味着流式处理天然是背压友好的：如果上层处理事件的速度慢，SSE 的读取也会自动变慢，不会有事件堆积在内存里。不需要额外设置缓冲区来平衡生产和消费的速度差异，async generator 的协作式调度自动完成了这件事。

### 工具调用的状态机

流式处理中的工具调用涉及三个阶段，由三个局部变量跟踪状态：

```Python
current_tool_name = ""
current_tool_id = ""
json_accum = ""
```

`content_block_start` 时设置 tool name 和 tool id， `content_block_delta` 时累积 JSON 片段， `content_block_stop` 时解析 JSON 并 yield `ToolCallComplete` ，然后重置状态。这是一个隐式状态机，状态转移由 SSE 事件驱动。

这个状态机的设计是由 Anthropic API 的事件协议决定的：三种事件类型（start / delta / stop）对应三个状态转移时机，工具的名字、ID、参数分散在不同阶段到达，只能用局部变量做累积。

### Thinking 的累积与签名

思考内容的处理和工具调用类似，也是增量累积：

```Python
in_thinking = False
thinking_accum = ""
thinking_signature = ""
```

思考的增量是实时 yield 给 UI 展示的（ `ThinkingDelta` ），同时在本地累积完整文本。等到 `content_block_stop` 时，yield 一个 `ThinkingComplete` 把完整思考内容和签名一起交出去，供后续序列化使用。

签名的作用是让 Anthropic API 在下一轮请求中验证思考块没有被篡改。如果你修改了思考内容但签名不变，API 会拒绝请求。这是 Anthropic 防止用户注入虚假思考内容的安全机制。

## 错误分类

### 四种异常类型

Python 版定义了四种自定义异常，全部继承自 `LLMError` ：

```Python
class LLMError(Exception):
    pass

class AuthenticationError(LLMError):
    pass

class RateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after

class NetworkError(LLMError):
    pass
```

四种异常覆盖了实际开发中最常见的错误场景。 `RateLimitError` 带了一个 `retry_after` 字段，上层可以据此决定等多久再重试。其他异常不需要额外字段，空类体就够了，Python 的异常继承链本身就携带了分类信息。

### Anthropic SDK 异常映射

`stream()` 方法用 try/except 把 SDK 抛出的异常映射为业务异常：

```Python
except _anthropic.AuthenticationError as e:
    raise AuthenticationError(f"Invalid API key: {e}") from e
except _anthropic.RateLimitError as e:
    retry = e.response.headers.get("retry-after") if e.response else None
    raise RateLimitError(
        f"Rate limited. {f'Retry after {retry}s.' if retry else 'Please wait.'}",
        retry_after=float(retry) if retry else None,
    ) from e
except _anthropic.APIConnectionError as e:
    raise NetworkError(f"Network error: {e}") from e
except _anthropic.APIStatusError as e:
    raise LLMError(f"API error ({e.status_code}): {e.message}") from e
```

每个 `raise ... from e` 都保留了原始异常链。调试时打印 traceback 能看到完整的因果关系：先是 SDK 报了什么错，然后被映射成了什么业务异常。

`RateLimitError` 的处理最细致：从响应头里提取 `retry-after` 值，如果有就告诉上层具体等多久。 `APIConnectionError` 是 DNS 解析失败、连接超时这类网络层问题，统一归为 `NetworkError` 。 `APIStatusError` 是兜底，覆盖所有其他 HTTP 错误码。

OpenAI 那边的异常映射结构完全一样，只是 SDK 的异常类名不同（ `_openai.AuthenticationError` vs `_anthropic.AuthenticationError` ）。两套映射的输出统一到同一组自定义异常，上层不需要区分来源。

## Python 特有的设计模式

### Pydantic BaseModel 做工具参数校验

工具的参数定义用的是 Pydantic 的 `BaseModel` ，而不是普通 dataclass：

```Python
class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
```

`params_model` 是一个 Pydantic model 类（不是实例）。工具注册时用 `model_json_schema()` 自动生成 JSON Schema 发给 LLM，LLM 返回的参数用 `model_validate()` 自动校验和类型转换。一个类同时充当 schema 生成器和参数验证器，不需要手写 JSON Schema 再手动解析，开发新工具时只需定义一个 Params 类就够了。

### Literal 类型做枚举

工具分类用的是 `Literal` 类型而不是 enum：

```Python
ToolCategory = Literal["read", "write", "command"]
```

`Literal` 比 enum 更轻量，不需要单独定义一个类。类型检查器能静态验证赋值是否合法，运行时就是普通字符串，序列化零成本。

### async with 做资源管理

Python 靠 `async with` 自动管理流的生命周期：

```Python
async with self._client.messages.stream(**kwargs) as stream:
    async for event in stream:
        # ...
```

`async with` 保证即使中途抛异常， `__aexit__` 也会被调用来关闭底层连接。不需要手动 close，资源泄漏的可能性极低。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 供应商抽象 | ABC 抽象基类 + `create_client` 工厂函数 |
| 流式响应 | async generator，yield 驱动，天然背压 |
| 事件类型 | 7 个 dataclass + Union 类型别名 |
| 消息模型 | Message dataclass，5 字段覆盖所有情况 |
| 双协议序列化 | `serialize("anthropic"/"openai")` ，内部分发 |
| 消息交替 | system-reminder 合并到前一条 user 消息 |
| 错误分类 | 4 种自定义异常，SDK 异常映射 + 异常链保留 |
| 工具参数 | Pydantic BaseModel 同时做 schema 生成和参数校验 |
| 资源管理 | async with 上下文管理器，自动关闭流 |