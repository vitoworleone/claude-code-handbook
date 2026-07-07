# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mewcode.teams.progress import TeammateProgress, random_verb

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.conversation import ConversationManager
    from mewcode.teams.models import TeammateInfo

log = logging.getLogger(__name__)


class InProcessTeammateHandle:
    def __init__(
        self,
        agent: Agent,
        task: asyncio.Task[str],
        name: str,
        progress: TeammateProgress | None = None,
    ) -> None:
        self.agent = agent
        self.task = task
        self.name = name
        self.progress = progress


    @property
    def done(self) -> bool:
        return self.task.done()

    @property
    def result(self) -> str | None:
        if self.task.done():
            try:
                return self.task.result()
            except (asyncio.CancelledError, Exception):
                return None
        return None


    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()


def spawn_inprocess_teammate(
    agent: Agent,
    prompt: str,
    name: str,
    conversation: ConversationManager | None = None,
    member: TeammateInfo | None = None,
    team_name: str = "",
) -> InProcessTeammateHandle:

    # Create progress tracker and attach to member if provided
    progress = TeammateProgress(
        name=name,
        team_name=team_name,
        spinner_verb=random_verb(),
    )
    if member is not None:
        member.progress = progress

    def _on_event(event: dict[str, Any]) -> None:
        """Event callback wired into agent.run_to_completion."""
        event_type = event.get("type")
        if event_type == "tool_use":
            tool_name = event.get("toolName", "")
            args = event.get("args", {})
            progress.record_tool_use(tool_name, args)
        elif event_type == "usage":
            usage = event.get("usage", {})
            progress.record_tokens(
                usage.get("inputTokens", 0),
                usage.get("outputTokens", 0),
            )
        elif event_type == "stream_text":
            text = event.get("text")
            if text:
                with progress._lock:
                    progress.last_message = text

    async def _run() -> str:
        try:
            if conversation is not None:
                result = await agent.run_to_completion(
                    "", conversation, event_callback=_on_event,
                )
            else:
                result = await agent.run_to_completion(
                    prompt, event_callback=_on_event,
                )
            progress.status = "completed"
            return result
        except asyncio.CancelledError:
            progress.status = "stopped"
            raise
        except Exception:
            progress.status = "failed"
            raise

    task = asyncio.create_task(_run(), name=f"teammate-{name}")
    log.info("Spawned in-process teammate %s (verb=%s)", name, progress.spinner_verb)
    return InProcessTeammateHandle(agent=agent, task=task, name=name, progress=progress)
