"""Agent worktree isolation — create/cleanup git worktrees for sub-agents.

P4c: Provides isolated working copies via `git worktree` so sub-agents
can make changes without affecting the main working tree.
"""

# 本模块利用 git worktree 机制为子 agent 提供隔离的工作目录。
#
# 为什么需要 worktree 隔离？
# 当多个后台 agent 同时修改同一个仓库时，它们的文件修改会互相冲突。
# git worktree 创建的是同一仓库的独立工作副本，共享 .git 数据库
# 但拥有独立的工作目录和索引，从而实现并行修改互不干扰。
#
# 生命周期：
# 1. create_agent_worktree() —— 在临时目录下创建 detached worktree
# 2. 子 agent 在 worktree 目录中执行所有操作
# 3. cleanup_agent_worktree() —— agent 完成后检查并清理 worktree
#    - 如果有未提交的修改，保留 worktree 并警告（防止丢失工作）
#    - 如果没有修改，安全删除 worktree

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


async def create_agent_worktree(cwd: str, agent_id: str) -> str:
    """Create an isolated git worktree for a sub-agent.

    Uses `git worktree add` to create a detached worktree in a temp directory.

    Args:
        cwd: The current working directory (must be inside a git repo).
        agent_id: Unique identifier for the agent (used in path).

    Returns:
        Absolute path to the new worktree directory.

    Raises:
        RuntimeError: If git worktree creation fails.
    """
    # 将 worktree 创建在系统临时目录下，避免污染用户的项目目录。
    # 使用 agent_id 作为目录名后缀，确保每个 agent 的 worktree 路径唯一。
    worktree_dir = os.path.join(
        tempfile.gettempdir(),
        f"cc-agent-worktree-{agent_id}",
    )

    # --detach 表示创建一个 detached HEAD 的 worktree，不关联任何分支。
    # 这样 agent 在 worktree 中的操作不会影响任何分支的指针。
    # 使用 asyncio.create_subprocess_exec 而非 os.system，
    # 遵循项目规范：禁止使用 os.system，统一使用异步子进程。
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "--detach", worktree_dir,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "unknown error"
        raise RuntimeError(f"Failed to create worktree: {error_msg}")

    logger.info("Created agent worktree: %s", worktree_dir)
    return worktree_dir


async def cleanup_agent_worktree(worktree_path: str, cwd: str) -> None:
    """Remove an agent worktree if it has no uncommitted changes.

    Checks for uncommitted changes first. If changes exist, logs a warning
    and leaves the worktree intact.

    Args:
        worktree_path: Path to the worktree to remove.
        cwd: The original working directory (git repo root).
    """
    # 清理前先检查 worktree 中是否有未提交的修改。
    # --porcelain 输出格式是机器可读的，有输出表示有修改。
    # 这一步是安全保障：如果 agent 修改了文件但没有提交，
    # 直接删除 worktree 会导致这些修改永久丢失。
    # Check for uncommitted changes in the worktree
    status_proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await status_proc.communicate()

    if stdout and stdout.strip():
        # 有未提交修改 → 保留 worktree，只记录警告。
        # 用户可以稍后手动查看这些修改并决定是否保留。
        logger.warning(
            "Worktree %s has uncommitted changes, not removing",
            worktree_path,
        )
        return

    # Remove the worktree
    # 使用 `git worktree remove` 而非直接删除目录，
    # 因为 git 需要更新 .git/worktrees 中的注册信息。
    # 直接删除目录会留下悬空的 worktree 记录。
    remove_proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", worktree_path,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await remove_proc.communicate()

    if remove_proc.returncode != 0:
        # 删除失败不抛异常，只记录警告。可能的原因：
        # worktree 路径已不存在（被其他进程清理了），或权限不足等。
        error_msg = stderr.decode().strip() if stderr else "unknown error"
        logger.warning("Failed to remove worktree: %s", error_msg)
    else:
        logger.info("Cleaned up agent worktree: %s", worktree_path)
