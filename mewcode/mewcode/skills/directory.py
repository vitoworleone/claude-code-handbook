# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)


def parse_tool_json(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to parse tool.json at %s: %s", path, e)
        return []

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        log.warning("tool.json at %s must be a JSON array or object", path)
        return []

    return raw


def load_tool_implementation(
    references_dir: Path, tool_name: str
) -> Callable[..., Any] | None:
    script = references_dir / f"{tool_name}.py"
    if not script.is_file():
        return None

    module_name = f"mewcode_skill_tool_{tool_name}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        log.warning("Cannot create module spec for %s", script)
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        log.warning("Failed to load tool implementation %s: %s", script, e)
        return None

    execute_fn = getattr(module, "execute", None)
    if execute_fn is None:
        log.warning("Tool implementation %s has no 'execute' function", script)
        return None

    return execute_fn


class _DynamicParams(BaseModel):
    model_config = {"extra": "allow"}


class SkillCustomTool(Tool):


    def __init__(
        self,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        impl: Callable[..., Any] | None,
    ) -> None:
        self.name = tool_name
        self.description = description
        self.params_model = _DynamicParams
        self.category = "command"
        self.is_concurrency_safe = False
        self._schema = schema
        self._impl = impl


    def get_schema(self) -> dict[str, Any]:
        input_schema = self._schema.get("parameters", self._schema.get("input_schema", {}))
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        if self._impl is None:
            return ToolResult(
                output=f"Error: no implementation found for tool '{self.name}'",
                is_error=True,
            )
        try:
            kwargs = params.model_dump()
            import asyncio
            if asyncio.iscoroutinefunction(self._impl):
                result = await self._impl(**kwargs)
            else:
                result = self._impl(**kwargs)
            return ToolResult(output=str(result))
        except Exception as e:
            return ToolResult(output=f"Tool execution error: {e}", is_error=True)


def register_skill_tools(skill_dir: Path, registry: ToolRegistry) -> int:
    tool_json_path = skill_dir / "tool.json"
    if not tool_json_path.is_file():
        return 0

    schemas = parse_tool_json(tool_json_path)
    references_dir = skill_dir / "references"
    count = 0

    for schema in schemas:
        tool_name = schema.get("name", "")
        if not tool_name:
            log.warning("Skipping tool with no name in %s", tool_json_path)
            continue

        if registry.get(tool_name) is not None:
            log.debug("Tool '%s' already registered, skipping", tool_name)
            continue

        description = schema.get("description", "")
        impl = load_tool_implementation(references_dir, tool_name) if references_dir.is_dir() else None

        if impl is None:
            log.warning("No implementation for tool '%s' in %s", tool_name, references_dir)

        tool = SkillCustomTool(tool_name, description, schema, impl)
        registry.register(tool)
        count += 1

    return count
