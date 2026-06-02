"""Memory extraction — automatically extract and save memories from conversations.

Corresponds to TS: services/extractMemories/extractMemories.ts + prompts.ts.

Runs after each completed turn in the REPL. Analyzes recent messages and
saves any noteworthy information to the project memory directory.

P3b: ExtractionCoordinator provides coalescing — serializes concurrent
extraction requests and reruns if dirty flag set during execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from cc.memory.session_memory import load_memories, save_memory, update_memory_index

if TYPE_CHECKING:

    from cc.models.messages import Message

logger = logging.getLogger(__name__)

# 触发记忆提取的最少新消息数阈值
# 低于此数量说明对话还不够充分，提取意义不大，避免浪费 API 调用
MIN_NEW_MESSAGES = 4

# 记忆提取的系统提示词 —— 对应 TS: services/extractMemories/prompts.ts
# 采用与主系统提示词一致的四类记忆分类体系（user/feedback/project/reference）
# 和 frontmatter 格式，确保提取结果能被后续 prompt 构建流程直接使用
EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction agent. Analyze the conversation below and determine if there is anything worth saving to persistent memory.

## What to save

Save information that would be useful in FUTURE conversations:

- **user**: User's role, preferences, expertise level, goals
- **feedback**: Corrections or confirmations about how to approach work
- **project**: Ongoing work context, decisions, deadlines (convert relative dates to absolute)
- **reference**: Pointers to external resources (Linear projects, Slack channels, dashboards)

## What NOT to save

- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.
- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation context.

These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping.

## Output format

If you find something worth saving, respond with EXACTLY this JSON format (no other text):

```json
{"memories": [{"name": "short_filename", "type": "user|feedback|project|reference", "content": "The memory content in markdown with frontmatter"}]}
```

If there is nothing worth saving, respond with exactly:

```json
{"memories": []}
```

Important:
- Each memory's content MUST include frontmatter:
  ---
  name: {{memory name}}
  description: {{one-line description}}
  type: {{user, feedback, project, reference}}
  ---
- For feedback/project types, structure content as: rule/fact, then **Why:** and **How to apply:** lines.
- Be very selective. Most turns have nothing worth saving.
- Never save API keys, passwords, or credentials.
- Do not duplicate information that already exists in the provided existing memories."""


async def extract_memories(
    messages: list[Message],
    cwd: str,
    call_model: Any,
    new_message_count: int | None = None,
    claude_dir: Any = None,
) -> list[str]:
    """Extract and save memories from recent conversation messages.

    Corresponds to TS: services/extractMemories/extractMemories.ts main flow.

    Args:
        messages: Full conversation messages.
        cwd: Current working directory (determines project).
        call_model: Async generator function for API calls.
        new_message_count: Number of new messages since last extraction.
        claude_dir: Override for ~/.claude directory (for testing).

    Returns:
        List of saved memory names (empty if nothing extracted).
    """
    from cc.models.messages import AssistantMessage, UserMessage

    # 只统计模型可见的消息（用户消息和助手消息），
    # 过滤掉系统消息、压缩边界消息等对提取无意义的内容
    visible = [m for m in messages if isinstance(m, (UserMessage, AssistantMessage))]
    if new_message_count is None:
        new_message_count = len(visible)

    # 新消息不足阈值则跳过，避免在短对话中浪费 API 调用
    if new_message_count < MIN_NEW_MESSAGES:
        return []

    # 加载当前项目已有的记忆，传给提取模型以避免重复保存
    existing = load_memories(cwd, claude_dir=claude_dir)
    existing_text = ""
    if existing:
        # 只取每条记忆内容的前 100 字符作为摘要，控制 prompt 长度
        existing_text = "\n".join(f"- {m['name']}: {m['content'][:100]}" for m in existing)

    # 取最近 N 条可见消息构建提取上下文
    recent: list[Message] = list(visible[-new_message_count:])
    conversation_text = _format_messages_for_extraction(recent)

    # 组装发送给提取模型的用户提示词，包含已有记忆和最近对话两部分
    user_prompt = f"""## Existing memories

{existing_text or "(none)"}

## Recent conversation ({new_message_count} messages)

{conversation_text}

Analyze the above and extract any memories worth saving."""

    # 调用模型执行提取（使用与主循环相同的 call_model 接口）
    from cc.core.events import TextDelta, TurnComplete
    from cc.models.messages import normalize_messages_for_api

    extract_messages: list[Message] = [UserMessage(content=user_prompt)]
    api_messages = normalize_messages_for_api(extract_messages)

    # 逐块收集流式响应文本
    response_parts: list[str] = []
    try:
        async for event in call_model(
            messages=api_messages,
            system=EXTRACTION_SYSTEM_PROMPT,
            tools=None,
        ):
            if isinstance(event, TextDelta):
                response_parts.append(event.text)
            elif isinstance(event, TurnComplete):
                break
    except Exception as e:
        # 提取失败不应影响主对话流程，静默降级
        logger.warning("Memory extraction failed: %s", e)
        return []

    response = "".join(response_parts).strip()

    # 解析模型返回的 JSON，提取记忆条目
    saved_names: list[str] = []
    try:
        # 模型可能把 JSON 包裹在 ```json ``` 代码块中，需要剥离
        json_str = response
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        data = json.loads(json_str.strip())
        memories = data.get("memories", [])

        for mem in memories:
            name = mem.get("name", "")
            content = mem.get("content", "")
            if name and content:
                # 将记忆内容写入独立的 .md 文件
                save_memory(cwd, name, content, claude_dir=claude_dir)
                # P3a: 同步更新 MEMORY.md 索引，使该记忆能出现在后续对话的系统提示中
                description = _extract_description(content) or f"{mem.get('type', 'auto')} memory"
                update_memory_index(cwd, name, description, claude_dir=claude_dir)
                saved_names.append(name)
                logger.info("Saved memory: %s", name)

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        # 解析失败说明模型输出格式不合规，属于正常情况（大多数轮次无可提取记忆）
        logger.debug("Could not parse extraction response: %s", e)

    return saved_names


def _extract_description(content: str) -> str | None:
    """Extract the 'description' field from memory frontmatter.

    P3a: Used to populate the MEMORY.md index entry.
    Memory files have YAML frontmatter like:
    ---
    name: ...
    description: one-line description
    type: ...
    ---
    """
    # 通过 "---" 分隔符定位 YAML frontmatter 区域
    if "---" not in content:
        return None
    parts = content.split("---", 2)
    # 合法的 frontmatter 会产生至少 3 段（前空、frontmatter、正文）
    if len(parts) < 3:
        return None
    frontmatter = parts[1]
    # 逐行扫描 frontmatter，查找 description 字段
    for line in frontmatter.strip().split("\n"):
        line = line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip()
    return None


def _format_messages_for_extraction(messages: list[Message]) -> str:
    """Format messages into text for the extraction prompt.

    将结构化的消息对象转换为纯文本，供提取模型阅读。
    工具调用的具体内容被折叠为 [tool results]，因为提取关注的是对话语义而非工具细节。
    """
    from cc.models.messages import AssistantMessage, UserMessage

    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, str) else "[tool results]"
            parts.append(f"User: {content}")
        elif isinstance(msg, AssistantMessage):
            text = msg.get_text()
            if text:
                parts.append(f"Assistant: {text}")
    return "\n\n".join(parts)


def _count_visible(messages: list[Message]) -> int:
    """Count user + assistant messages (model-visible).

    只计算模型可见的消息，排除系统消息和压缩边界消息，
    用于 ExtractionCoordinator 追踪提取进度。
    """
    from cc.models.messages import AssistantMessage, UserMessage

    return sum(1 for m in messages if isinstance(m, (UserMessage, AssistantMessage)))


class ExtractionCoordinator:
    """Serializes memory extraction with coalescing.

    P3b: 提取协调器，解决并发提取的竞态问题。设计要点：
    - 同一时刻只允许一个提取任务运行（避免并发写 MEMORY.md 导致数据损坏）
    - 如果提取运行期间有新轮次到来，设置 dirty 标记而非启动新提取
    - 当前提取完成后，若 dirty 标记已设置则自动重新提取
    - 保证最后一个轮次一定会被扫描到（不丢失工作）
    """

    def __init__(self) -> None:
        # 标记当前是否有提取任务正在运行
        self._running = False
        # dirty 标记：提取运行期间如果有新轮次到来，置为 True
        self._dirty = False
        # 上次提取时已处理的可见消息总数，用于计算增量
        self._last_extracted_count = 0
        # 互斥锁，确保同一时刻只有一个提取任务进入临界区
        self._lock = asyncio.Lock()

    @property
    def last_extracted_count(self) -> int:
        return self._last_extracted_count

    async def request_extraction(
        self,
        messages: list[Message],
        cwd: str,
        call_model: Any,
        claude_dir: Any = None,
    ) -> list[str]:
        """Request an extraction. Coalesces if one is already running.

        如果已有提取在运行，只设置 dirty 标记然后立即返回（coalescing），
        避免多个提取并发争抢资源。

        Returns list of saved memory names (empty if coalesced/skipped).
        """
        if self._running:
            # 另一个提取正在运行，设置 dirty 标记让它完成后重新提取
            self._dirty = True
            logger.debug("Extraction coalesced — will rerun after current finishes")
            return []

        all_saved: list[str] = []
        async with self._lock:
            self._running = True
            try:
                # 使用 while 循环实现"提取-检查-重提取"模式：
                # 每次提取前先清除 dirty，提取完后若 dirty 再次被设置则继续循环
                while True:
                    self._dirty = False
                    current_visible = _count_visible(messages)
                    # 计算自上次提取以来的增量消息数
                    increment = current_visible - self._last_extracted_count

                    if increment >= MIN_NEW_MESSAGES:
                        saved = await extract_memories(
                            messages, cwd,
                            call_model=call_model,
                            new_message_count=increment,
                            claude_dir=claude_dir,
                        )
                        # 更新水位线，下次只提取新增部分
                        self._last_extracted_count = current_visible
                        all_saved.extend(saved)

                    if not self._dirty:
                        # 提取期间没有新轮次到来，可以安全退出
                        break
                    # dirty 标记被设置，说明提取期间有新消息，需要重新扫描
                    logger.debug("Extraction rerun — dirty flag set")
            finally:
                self._running = False

        return all_saved
