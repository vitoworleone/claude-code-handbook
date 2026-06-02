"""QueryEngine — encapsulates a single conversation's runtime dependencies.

Corresponds to TS: QueryEngine.ts (simplified for Python CLI).

Reusable by: main.py REPL, main.py --print, AgentTool sub-agents,
InProcessTeammate (P6d future).

=== 架构角色 ===

QueryEngine 是 main.py（控制面）与 query_loop（状态机内核）之间的「枢纽层」。

它解决的核心问题是：query_loop 是一个纯函数式的 async generator，
不持有任何状态——而一次完整对话需要的状态（messages、client、registry 等）
必须有个地方管理。QueryEngine 就是这个「有状态的容器」。

=== 调用关系 ===

  main.py
    ↓ 创建 QueryEngine（装配所有依赖）
    ↓ 调用 engine.submit() 或 engine.run_turn()
  QueryEngine
    ↓ 管理 messages 列表（追加用户消息）
    ↓ 创建 call_model 闭包（绑定 client + model）
    ↓ 组装 permission_checker
    ↓ 调用 query_loop()（传入所有依赖）
  query_loop
    ↓ 执行状态机（调用模型 → 工具执行 → 循环）
    ↓ yield QueryEvent 给调用方

=== 三个入口方法的区别 ===

  submit():          接收原始用户文本，自动包装为 UserMessage，适用于 --print 一次性模式
  run_turn():        不追加消息，直接在现有 messages 上运行，适用于 REPL 循环
                     （REPL 中 main.py 已经手动 append 了 UserMessage）
  submit_messages(): 接收预构建的 Message 列表，适用于 AgentTool 子 agent 场景

=== 模块关系 ===

  依赖: cc/api/claude.py（stream_response）、cc/core/query_loop.py
  被依赖: cc/main.py、cc/tools/agent/agent_tool.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from cc.api.claude import stream_response
from cc.core.query_loop import query_loop
from cc.models.messages import Message, UserMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    import anthropic

    from cc.core.events import QueryEvent
    from cc.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class CallModelFn(Protocol):
    """Protocol for the call_model callable used by query_loop.

    这是一个结构化类型（Protocol），而非普通基类。
    任何签名匹配 (**kwargs) -> AsyncIterator[QueryEvent] 的 callable 都满足此协议，
    无需显式继承。query_loop 的 call_model 参数就是此类型。
    """

    def __call__(self, **kwargs: Any) -> AsyncIterator[QueryEvent]: ...


class QueryEngine:
    """Encapsulates all runtime dependencies for one conversation.

    Corresponds to TS: QueryEngine.ts — owns messages, drives query_loop,
    provides call_model factory for sub-agents.

    Permission-agnostic: permission_ctx is an opaque optional object.
    When None, all tools execute without gate (pre-P2a behavior).
    """

    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        model: str,
        registry: ToolRegistry,
        system_prompt: str,
        hooks: Sequence[Any] | None = None,
        permission_ctx: Any | None = None,
        max_turns: int = 50,
        context_window: int = 200_000,
    ) -> None:
        # 所有参数使用 keyword-only（*）强制命名传参，防止位置参数错乱
        self._client = client
        self._model = model
        self._registry = registry
        self._system_prompt = system_prompt
        self._hooks = list(hooks) if hooks else None
        self._permission_ctx = permission_ctx  # 预留给 P2a 权限系统，当前为 None 时所有工具无门禁
        self._max_turns = max_turns
        self._context_window = context_window  # 用于 auto-compact 阈值判断（默认 200K 对应 Claude 3.5）
        self._messages: list[Message] = []  # 整个会话的 transcript，贯穿多轮 submit/run_turn
        # token 计数器：跨轮次累计，用于 UI 显示总消耗。
        # 注意：这些字段目前只在 main.py 中通过 property 读取，
        # 实际累加逻辑在 main.py 的事件循环中（通过 TurnComplete.usage 累加），
        # 而非在 QueryEngine 内部自动累加。
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    # -- Properties --

    @property
    def messages(self) -> list[Message]:
        return self._messages

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        return self._client

    # -- Core API --

    def make_call_model(self, model: str | None = None, max_tokens: int = 16384) -> CallModelFn:
        """Create a call_model callable bound to a specific model.

        这是一个工厂方法（Factory Pattern），返回一个闭包。
        闭包捕获了 client 和 model，使得 query_loop 无需直接依赖 Anthropic SDK。

        为什么用闭包而非直接传 client+model？
          1. query_loop 只需要一个 "调用模型" 的能力，不关心具体实现
          2. AgentTool 子 agent 可以用不同的 model 参数创建不同的 call_model
          3. auto_compact 需要一个 max_tokens=4096 的低配版 call_model（省 token）

        kwargs.pop("max_tokens", max_tokens) 的设计：
          允许 query_loop 在运行时覆盖 max_tokens（如错误恢复时 escalate 到 65536），
          同时提供默认值作为兜底。
        """
        effective_model = model or self._model
        client = self._client

        async def call_model(**kwargs: Any) -> AsyncIterator[QueryEvent]:
            # pop 而非 get：避免 max_tokens 同时出现在 kwargs 和显式参数中导致 SDK 报错
            effective_max: Any = kwargs.pop("max_tokens", max_tokens)
            async for event in stream_response(
                client, model=effective_model, max_tokens=effective_max, **kwargs
            ):
                yield event

        return call_model

    async def submit(
        self,
        user_input: str,
        *,
        max_turns: int | None = None,
        auto_compact: bool = True,
    ) -> AsyncIterator[QueryEvent]:
        """Submit a user message and yield events from the query loop.

        This is the main entry point — encapsulates message management
        and query_loop invocation.

        适用场景: --print 一次性模式（单次提问 → 输出 → 退出）。
        与 run_turn() 的区别: submit() 会自动将 user_input 包装为 UserMessage 并追加到 messages。
        """
        self._messages.append(UserMessage(content=user_input))

        # auto_compact_fn 使用 max_tokens=4096 的低配版 call_model，
        # 因为压缩摘要不需要长输出，省 token 且减少延迟
        auto_compact_fn = self.make_call_model(max_tokens=4096) if auto_compact else None

        perm_checker = self._build_permission_checker()

        async for event in query_loop(
            messages=self._messages,  # 注意: messages 是 mutable list，query_loop 会原地修改它
            system_prompt=self._system_prompt,
            tools=self._registry,
            call_model=self.make_call_model(),
            max_turns=max_turns or self._max_turns,
            auto_compact_fn=auto_compact_fn,
            context_window=self._context_window,
            hooks=self._hooks,
            permission_checker=perm_checker,
        ):
            yield event

    async def submit_messages(
        self,
        messages: list[Message],
        *,
        max_turns: int | None = None,
    ) -> AsyncIterator[QueryEvent]:
        """Submit pre-built messages (for AgentTool / resume scenarios).

        Unlike submit(), this doesn't wrap input in UserMessage —
        caller provides the full message list.
        W2: Now passes permission_checker like submit() does.

        注意: 这里接收的 messages 是调用方提供的独立列表（不是 self._messages），
        因为 AgentTool 子 agent 有自己的对话上下文，不应污染父 agent 的 transcript。
        也因此不传 auto_compact_fn——子 agent 的上下文通常较短，不需要压缩。
        """
        perm_checker = self._build_permission_checker()

        async for event in query_loop(
            messages=messages,
            system_prompt=self._system_prompt,
            tools=self._registry,
            call_model=self.make_call_model(),
            max_turns=max_turns or self._max_turns,
            hooks=self._hooks,
            permission_checker=perm_checker,
        ):
            yield event

    async def run_turn(
        self,
        *,
        auto_compact: bool = True,
    ) -> AsyncIterator[QueryEvent]:
        """Run a single turn on existing messages (for REPL use).

        W2: Provides a unified entry point so REPL doesn't need to
        manually assemble query_loop + permission_checker.

        与 submit() 的关键区别:
          - 不追加 UserMessage（REPL 主循环已经 append 过了）
          - 直接在 self._messages 上运行
          - 适用于 REPL 的 while True 循环：每轮 REPL 先 append 用户输入，再调 run_turn()
        """
        auto_compact_fn = self.make_call_model(max_tokens=4096) if auto_compact else None
        perm_checker = self._build_permission_checker()

        async for event in query_loop(
            messages=self._messages,
            system_prompt=self._system_prompt,
            tools=self._registry,
            call_model=self.make_call_model(),
            max_turns=self._max_turns,
            auto_compact_fn=auto_compact_fn,
            context_window=self._context_window,
            hooks=self._hooks,
            permission_checker=perm_checker,
        ):
            yield event

    def _build_permission_checker(self) -> Any:
        """Build a permission checker callback from the engine's permission context.

        Returns None if no context is set (all tools allowed).
        Used by submit(), submit_messages(), and run_turn().

        返回 None 表示「无门禁模式」——所有工具直接执行，不弹确认。
        返回 callable 时，query_loop 在执行每个工具前调用它，
        返回 False 则跳过该工具（向模型报告 permission denied）。
        """
        if self._permission_ctx is None:
            return None
        ctx = self._permission_ctx

        # 将 permission_ctx 的 OOP 接口适配为 query_loop 需要的函数式接口
        async def _check(tool_name: str, tool_input: dict[str, object]) -> bool:
            result: bool = await ctx.check(tool_name, tool_input)
            return result

        return _check

    def make_call_model_factory(self) -> Any:
        """Create a factory that produces call_model callables for different models.

        Used by AgentTool to create call_model for sub-agents with model override.

        这是「工厂的工厂」模式（meta-factory）:
          make_call_model()         → 返回一个 call_model 闭包（绑定特定 model）
          make_call_model_factory() → 返回一个 factory 函数（能创建任意 model 的 call_model）

        为什么需要这一层间接？
        因为 AgentTool 在注册时还不知道要用什么 model，
        需要在运行时根据用户指令动态选择。factory 延迟了 model 绑定时机。
        """
        engine = self

        def factory(model: str | None = None, max_tokens: int = 16384) -> CallModelFn:
            return engine.make_call_model(model=model, max_tokens=max_tokens)

        return factory
