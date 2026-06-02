"""AskUserQuestionTool — pause and ask the user a question.

Corresponds to TS: tools/AskUserQuestionTool/AskUserQuestionTool.tsx.
"""

from __future__ import annotations

from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

ASK_USER_TOOL_NAME = "AskUserQuestion"


class AskUserQuestionTool(Tool):
    """Ask the user a question and return their answer.

    Corresponds to TS: tools/AskUserQuestionTool/AskUserQuestionTool.tsx.
    The input_fn is injected by the REPL to handle actual user input.

    该工具允许模型在需要人工判断或缺少信息时主动暂停并提问。
    通过依赖注入 input_fn 解耦了输入来源，使得交互式和非交互式
    模式可以共用同一个工具类。
    """

    def __init__(self, input_fn: Any = None) -> None:
        # input_fn 由 REPL 层注入，用于获取用户的实际输入；
        # 为 None 时表示当前处于非交互模式（如 --print 管道模式）
        self._input_fn = input_fn

    def get_name(self) -> str:
        return ASK_USER_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=ASK_USER_TOOL_NAME,
            description="Ask the user a question and wait for their response.",
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                },
                "required": ["question"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        question = tool_input.get("question", "")
        if not question:
            return ToolResult(content="Error: question is required", is_error=True)

        if self._input_fn is None:
            # 非交互模式下（如 CI/CD 管道）无法向用户提问，直接返回提示
            return ToolResult(content="(Cannot ask user in non-interactive mode)")

        try:
            # 使用 rich 库美化终端输出，提升用户体验
            from rich.console import Console

            console = Console()
            console.print(f"\n[bold yellow]Question:[/] {question}")
            answer = console.input("[bold blue]Answer: [/]")
            return ToolResult(content=answer)
        except (EOFError, KeyboardInterrupt):
            # 用户按 Ctrl+C 或输入流结束时，优雅处理而非崩溃
            return ToolResult(content="(User did not answer)")
