# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger(__name__)

VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


class SkillParseError(Exception):
    pass


@dataclass
class SkillDef:
    name: str
    description: str
    prompt_body: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    model: str | None = None
    context: Literal["full", "recent", "none"] = "full"
    source_path: Path | None = None
    is_directory: bool = False


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("Missing YAML frontmatter (must start with ---)")

    end = stripped.find("---", 3)
    if end == -1:
        raise SkillParseError("Unclosed YAML frontmatter (missing closing ---)")

    yaml_block = stripped[3:end]
    body = stripped[end + 3:].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillParseError(f"Invalid YAML in frontmatter: {e}") from e

    if not isinstance(meta, dict):
        raise SkillParseError("Frontmatter must be a YAML mapping")

    return meta, body


def _validate_meta(meta: dict, source: str = "") -> None:
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise SkillParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise SkillParseError(f"Missing required field 'description'{ctx}")

    name = meta["name"]
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(
            f"Invalid skill name '{name}'{ctx}: "
            "must be lowercase letters, digits, and hyphens, starting with a letter"
        )

    mode = meta.get("mode", "inline")
    if mode not in VALID_MODES:
        raise SkillParseError(f"Invalid mode '{mode}'{ctx}: must be one of {VALID_MODES}")

    context = meta.get("context", "full")
    if context not in VALID_CONTEXTS:
        raise SkillParseError(f"Invalid context '{context}'{ctx}: must be one of {VALID_CONTEXTS}")


def parse_skill_file(path: Path) -> SkillDef:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillParseError(f"Cannot read skill file {path}: {e}") from e

    meta, body = parse_frontmatter(raw)
    _validate_meta(meta, str(path))

    return SkillDef(
        name=meta["name"],
        description=meta["description"],
        prompt_body=body,
        allowed_tools=meta.get("allowedTools", []),
        mode=meta.get("mode", "inline"),
        model=meta.get("model"),
        context=meta.get("context", "full"),
        source_path=path,
        is_directory=False,
    )


def substitute_arguments(prompt_body: str, args: str) -> str:
    return prompt_body.replace("$ARGUMENTS", args)
