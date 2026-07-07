# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import os
import shutil

from mewcode.teams.models import BackendType


class BackendDetectionError(Exception):
    pass


def _in_tmux_session() -> bool:
    return bool(os.environ.get("TMUX"))


def _in_iterm2() -> bool:
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def _it2_available() -> bool:
    return shutil.which("it2") is not None


def _tmux_installed() -> bool:
    return shutil.which("tmux") is not None


def detect_backend(
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> BackendType:
    """Default to in-process for real-time progress tracking."""
    return BackendType.IN_PROCESS


def detect_pane_backend(
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> BackendType:
    """Detect pane backend when user explicitly requests tmux."""
    if teammate_mode == "in-process" or not is_interactive:
        return BackendType.IN_PROCESS

    if _in_tmux_session():
        return BackendType.TMUX

    if _in_iterm2() and _it2_available():
        return BackendType.ITERM2

    if _tmux_installed():
        return BackendType.TMUX

    raise BackendDetectionError(
        "No suitable terminal backend found for Agent Team.\n"
        "Install one of the following:\n"
        "  - tmux: brew install tmux\n"
        "  - iTerm2 + it2 CLI: https://iterm2.com/utilities/it2check\n"
        "Or set 'teammate_mode: \"in-process\"' in config.yaml to use in-process backend."
    )
