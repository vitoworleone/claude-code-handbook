# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""解析简历文件并提取结构化信息。"""
from __future__ import annotations

import re
from pathlib import Path


SECTION_PATTERNS = {
    "name": re.compile(r"^#\s*(.+)", re.MULTILINE),
    "tech_stack": re.compile(
        r"(?:技术栈|技术|skills?|tech)\s*[：:]\s*(.+)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience_years": re.compile(
        r"(\d+)\s*[年years]+",
        re.IGNORECASE,
    ),
}

SECTION_HEADERS = [
    "工作经历", "工作经验", "work experience", "experience",
    "项目经历", "项目经验", "projects",
    "教育", "教育经历", "education",
    "技能", "技术栈", "skills", "tech stack",
]


def _extract_sections(text: str) -> dict[str, str]:
    lines = text.split("\n")
    sections: dict[str, str] = {}
    current_header = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip().lstrip("#").strip().lower()
        matched_header = ""
        for header in SECTION_HEADERS:
            if header in stripped:
                matched_header = header
                break

        if matched_header:
            if current_header:
                sections[current_header] = "\n".join(current_lines).strip()
            current_header = matched_header
            current_lines = []
        else:
            current_lines.append(line)

    if current_header:
        sections[current_header] = "\n".join(current_lines).strip()

    return sections


def _extract_tech_keywords(text: str) -> list[str]:
    known_techs = [
        "Python", "Java", "Go", "Golang", "Rust", "C++", "C#", "TypeScript",
        "JavaScript", "Ruby", "PHP", "Kotlin", "Swift", "Scala",
        "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
        "Kafka", "RabbitMQ", "gRPC", "REST",
        "Docker", "Kubernetes", "K8s", "AWS", "GCP", "Azure",
        "Linux", "Nginx", "Git",
        "Spring", "Django", "Flask", "FastAPI", "Express", "Gin",
        "React", "Vue", "Angular", "Next.js",
        "TensorFlow", "PyTorch",
        "Microservices", "CI/CD", "DevOps",
    ]
    found = []
    text_lower = text.lower()
    for tech in known_techs:
        if tech.lower() in text_lower:
            found.append(tech)
    return found


async def execute(file_path: str = "", **kwargs) -> str:
    if not file_path:
        return "Error: file_path is required"

    path = Path(file_path)
    if not path.is_file():
        return f"Error: file not found: {file_path}"

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    if not content.strip():
        return "Error: resume file is empty"

    name_match = SECTION_PATTERNS["name"].search(content)
    name = name_match.group(1).strip() if name_match else "Unknown"

    years_match = SECTION_PATTERNS["experience_years"].search(content)
    years = years_match.group(1) if years_match else "Unknown"

    tech_match = SECTION_PATTERNS["tech_stack"].search(content)
    explicit_tech = tech_match.group(1).strip() if tech_match else ""

    detected_tech = _extract_tech_keywords(content)

    sections = _extract_sections(content)

    lines = [
        f"## Resume Analysis",
        f"",
        f"**Name**: {name}",
        f"**Experience**: {years} years",
        f"",
        f"### Tech Stack",
    ]

    if explicit_tech:
        lines.append(f"Declared: {explicit_tech}")
    if detected_tech:
        lines.append(f"Detected: {', '.join(detected_tech)}")
    if not explicit_tech and not detected_tech:
        lines.append("No tech stack detected")

    if sections:
        lines.append("")
        lines.append("### Sections Found")
        for header, body in sections.items():
            preview = body[:300]
            if len(body) > 300:
                preview += "..."
            lines.append(f"\n**{header}**:")
            lines.append(preview)

    return "\n".join(lines)

