# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

from pathlib import Path

MAX_INCLUDE_DEPTH = 5
INCLUDE_PREFIX = "@include "


def process_includes(
    content: str,
    base_dir: Path,
    project_root: Path,
    depth: int = 0,
) -> str:
    if depth >= MAX_INCLUDE_DEPTH:
        return content

    resolved_root = project_root.resolve()
    lines = content.split("\n")
    result: list[str] = []


    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(INCLUDE_PREFIX):
            result.append(line)
            continue

        rel_path = stripped[len(INCLUDE_PREFIX) :].strip()
        abs_path = (base_dir / rel_path).resolve()

        try:
            abs_path.relative_to(resolved_root)
        except ValueError:
            result.append("<!-- @include blocked: path outside project -->")
            continue

        if not abs_path.exists() or not abs_path.is_file():
            result.append("<!-- @include skipped: file not found -->")
            continue

        included = abs_path.read_text(encoding="utf-8")
        processed = process_includes(included, abs_path.parent, project_root, depth + 1)
        result.append(processed)

    return "\n".join(result)


def load_instructions(project_root: str) -> str:
    root = Path(project_root)
    home = Path.home()

    paths = [
        root / "MEWCODE.md",
        root / ".mewcode" / "MEWCODE.md",
        home / ".mewcode" / "MEWCODE.md",
    ]

    sections: list[str] = []
    for path in paths:
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8")
            processed = process_includes(content, path.parent, root)
            sections.append(processed)

    return "\n---\n".join(sections)

