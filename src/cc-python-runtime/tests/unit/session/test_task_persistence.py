"""Tests for W3: Task state persistence to session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc.models.messages import UserMessage
from cc.session.storage import load_task_snapshot, save_session
from cc.session.task_registry import TaskRegistry, TaskState

if TYPE_CHECKING:
    from pathlib import Path


class TestTaskPersistence:
    def test_save_with_task_snapshot(self, tmp_path: Path) -> None:
        """save_session should write tasks.json alongside transcript."""
        reg = TaskRegistry()
        t1 = reg.register("background_agent", {"desc": "test"})
        reg.update_state(t1, TaskState.COMPLETED)

        save_session(
            "test-sess",
            [UserMessage(content="hello")],
            claude_dir=tmp_path,
            task_snapshot=reg.snapshot(),
        )

        # Verify tasks.json exists
        tasks_path = tmp_path / "sessions" / "test-sess.tasks.json"
        assert tasks_path.exists()

    def test_load_task_snapshot(self, tmp_path: Path) -> None:
        """load_task_snapshot should return saved tasks."""
        reg = TaskRegistry()
        t1 = reg.register("background_agent")
        reg.update_state(t1, TaskState.COMPLETED)

        save_session(
            "test-sess",
            [UserMessage(content="hello")],
            claude_dir=tmp_path,
            task_snapshot=reg.snapshot(),
        )

        snap = load_task_snapshot("test-sess", claude_dir=tmp_path)
        assert snap is not None
        assert len(snap) == 1
        assert snap[0]["state"] == "completed"

    def test_restore_marks_running_as_killed(self, tmp_path: Path) -> None:
        """On restore, RUNNING tasks should become KILLED."""
        reg = TaskRegistry()
        t1 = reg.register("background_agent")  # RUNNING
        t2 = reg.register("background_agent")
        reg.update_state(t2, TaskState.COMPLETED)

        save_session(
            "test-sess",
            [UserMessage(content="hello")],
            claude_dir=tmp_path,
            task_snapshot=reg.snapshot(),
        )

        # Simulate process restart: new registry, restore
        reg2 = TaskRegistry()
        snap = load_task_snapshot("test-sess", claude_dir=tmp_path)
        assert snap is not None
        reg2.restore(snap)

        # RUNNING → KILLED, COMPLETED stays
        assert reg2.get(t1) is not None
        assert reg2.get(t1).state == TaskState.KILLED  # type: ignore[union-attr]
        assert reg2.get(t2).state == TaskState.COMPLETED  # type: ignore[union-attr]

    def test_load_missing_snapshot_returns_none(self, tmp_path: Path) -> None:
        """No tasks.json → None."""
        assert load_task_snapshot("nonexistent", claude_dir=tmp_path) is None

    def test_save_without_snapshot(self, tmp_path: Path) -> None:
        """save_session without task_snapshot should not create tasks.json."""
        save_session(
            "test-sess",
            [UserMessage(content="hello")],
            claude_dir=tmp_path,
        )
        tasks_path = tmp_path / "sessions" / "test-sess.tasks.json"
        assert not tasks_path.exists()
