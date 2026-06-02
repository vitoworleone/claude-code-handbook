"""GrepTool implementation.

Corresponds to TS: tools/GrepTool/GrepTool.ts.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

GREP_TOOL_NAME = "Grep"
# 默认最多返回 250 行结果，与 TS 原版对齐
DEFAULT_HEAD_LIMIT = 250


class GrepTool(Tool):
    """Search file contents using ripgrep or Python re.

    Corresponds to TS: tools/GrepTool/GrepTool.ts.
    采用双后端策略：优先使用 ripgrep（性能更好），
    当 ripgrep 不可用时回退到 Python re 模块（保证基线可用性）。
    """

    def get_name(self) -> str:
        return GREP_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=GREP_TOOL_NAME,
            description="Search file contents using regex patterns.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default: cwd)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File glob filter (e.g. '*.py')",
                    },
                    "output_mode": {
                        "type": "string",
                        "description": "Output mode: content, files_with_matches, count",
                    },
                },
                "required": ["pattern"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 搜索操作只读文件内容，不做修改，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        pattern = tool_input.get("pattern", "")
        search_path = tool_input.get("path", ".")
        file_glob = tool_input.get("glob")
        output_mode = tool_input.get("output_mode", "files_with_matches")
        head_limit = int(tool_input.get("head_limit", DEFAULT_HEAD_LIMIT))

        if not pattern:
            return ToolResult(content="Error: pattern is required", is_error=True)

        # 优先使用 ripgrep：它比 Python 遍历文件快 10-100 倍，
        # 且自动尊重 .gitignore，跳过二进制文件
        rg_path = shutil.which("rg")
        if rg_path:
            return await self._run_ripgrep(rg_path, pattern, search_path, file_glob, output_mode, head_limit)
        # ripgrep 不可用时回退到纯 Python 实现，确保在任何环境下都能工作
        return self._run_python_grep(pattern, search_path, file_glob, output_mode, head_limit)

    async def _run_ripgrep(
        self,
        rg_path: str,
        pattern: str,
        search_path: str,
        file_glob: str | None,
        output_mode: str,
        head_limit: int,
    ) -> ToolResult:
        """通过子进程调用 ripgrep 执行搜索。"""
        # --no-heading: 不按文件分组输出，方便后续按行截断
        # -n: 显示行号，帮助模型定位代码位置
        args = [rg_path, "--no-heading", "-n"]

        # 根据输出模式添加对应的 ripgrep 参数
        if output_mode == "files_with_matches":
            args.append("-l")  # 只输出匹配的文件名
        elif output_mode == "count":
            args.append("-c")  # 输出每个文件的匹配计数

        if file_glob:
            args.extend(["--glob", file_glob])

        args.extend([pattern, search_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # 30 秒超时防止在超大仓库中搜索时长期阻塞
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout.decode("utf-8", errors="replace")

            if not output.strip():
                return ToolResult(content="No matches found")

            # 按行截断，避免超大搜索结果撑爆上下文窗口
            lines = output.strip().split("\n")
            if len(lines) > head_limit:
                lines = lines[:head_limit]
                output = "\n".join(lines) + f"\n\n(... truncated at {head_limit} results)"
            else:
                output = "\n".join(lines)

            return ToolResult(content=output)

        except TimeoutError:
            return ToolResult(content="Error: Search timed out", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Error running ripgrep: {e}", is_error=True)

    def _run_python_grep(
        self,
        pattern: str,
        search_path: str,
        file_glob: str | None,
        output_mode: str,
        head_limit: int,
    ) -> ToolResult:
        """纯 Python 回退实现：当系统未安装 ripgrep 时使用。"""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(content=f"Error: Invalid regex: {e}", is_error=True)

        base = Path(search_path).resolve()
        if not base.exists():
            return ToolResult(content=f"Error: Path not found: {search_path}", is_error=True)

        results: list[str] = []

        # FIX (check.md #6): Use rglob for recursive patterns, glob for simple ones.
        # Don't try to manipulate the glob string — just use the right method.
        # 根据 glob 模式选择合适的遍历方法：
        # 含 ** 的模式需要特殊处理以避免重复递归
        if file_glob:
            if "**" in file_glob:
                # rglob 内部已包含递归语义，需要去掉 glob 中的 **/ 前缀
                rglob_pattern = file_glob.lstrip("*").lstrip("/") or "*"
                files = base.rglob(rglob_pattern)
            else:
                # 对于简单模式（如 *.py），使用 rglob 以递归搜索子目录
                files = base.rglob(file_glob)
        else:
            # 未指定文件过滤器时，搜索所有文件
            files = base.rglob("*")

        for filepath in files:
            if not filepath.is_file():
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                # 跳过无法读取的文件（如权限不足、编码问题等）
                continue

            # 根据输出模式生成不同格式的结果
            if output_mode == "files_with_matches":
                # 只要文件中有匹配就记录文件路径
                if regex.search(text):
                    results.append(str(filepath))
            elif output_mode == "count":
                # 统计每个文件中的匹配次数
                count = len(regex.findall(text))
                if count > 0:
                    results.append(f"{filepath}:{count}")
            else:
                # content 模式：输出每一行的匹配内容，格式为 文件:行号:内容
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{filepath}:{i}:{line}")

            # 提前退出：达到行数上限后停止遍历，节省不必要的 IO
            if len(results) >= head_limit:
                break

        if not results:
            return ToolResult(content="No matches found")

        output = "\n".join(results[:head_limit])
        if len(results) > head_limit:
            output += f"\n\n(... truncated at {head_limit} results)"

        return ToolResult(content=output)
