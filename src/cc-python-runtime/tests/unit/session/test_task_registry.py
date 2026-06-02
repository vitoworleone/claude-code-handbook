"""Tests for P0.5b: TaskRegistry lifecycle."""

from __future__ import annotations

import asyncio

import pytest

from cc.session.task_registry import TaskRecord, TaskRegistry, TaskState


class TestTaskRegistryBasics:
    def test_register_returns_id(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("background_agent", {"agent_id": "foo"})
        assert tid.startswith("a-")
        assert len(tid) == 10  # "a-" + 8 hex chars

    def test_register_teammate_prefix(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("in_process_teammate")
        assert tid.startswith("t-")

    def test_get_returns_record(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("background_agent")
        record = reg.get(tid)
        assert record is not None
        assert record.task_type == "background_agent"
        assert record.state == TaskState.RUNNING

    def test_get_unknown_returns_none(self) -> None:
        reg = TaskRegistry()
        assert reg.get("nonexistent") is None

    def test_update_state(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("background_agent")
        reg.update_state(tid, TaskState.COMPLETED)
        assert reg.get(tid).state == TaskState.COMPLETED  # type: ignore[union-attr]

    def test_list_active(self) -> None:
        reg = TaskRegistry()
        t1 = reg.register("background_agent")
        t2 = reg.register("background_agent")
        reg.update_state(t1, TaskState.COMPLETED)
        active = reg.list_active()
        assert len(active) == 1
        assert active[0].task_id == t2

    def test_list_all(self) -> None:
        reg = TaskRegistry()
        reg.register("background_agent")
        reg.register("extraction")
        assert len(reg.list_all()) == 2


class TestTaskRegistryStop:
    def test_stop_running_task(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("background_agent")
        assert reg.stop(tid) is True
        assert reg.get(tid).state == TaskState.KILLED  # type: ignore[union-attr]

    def test_stop_completed_task_returns_false(self) -> None:
        reg = TaskRegistry()
        tid = reg.register("background_agent")
        reg.update_state(tid, TaskState.COMPLETED)
        assert reg.stop(tid) is False

    def test_stop_unknown_returns_false(self) -> None:
        reg = TaskRegistry()
        assert reg.stop("nonexistent") is False

    @pytest.mark.asyncio
    async def test_stop_cancels_asyncio_task(self) -> None:
        reg = TaskRegistry()

        async def long_task() -> None:
            await asyncio.sleep(999)

        atask = asyncio.create_task(long_task())
        tid = reg.register("background_agent", asyncio_task=atask)
        reg.stop(tid)
        # Task.cancel() requests cancellation; it becomes cancelled after await
        assert atask.cancelling() > 0 or atask.cancelled()


class TestTaskRegistrySnapshotRestore:
    def test_snapshot_roundtrip(self) -> None:
        reg = TaskRegistry()
        t1 = reg.register("background_agent", {"agent_id": "a1"})
        t2 = reg.register("extraction")
        reg.update_state(t1, TaskState.COMPLETED)

        snap = reg.snapshot()
        assert len(snap) == 2

        # Restore into fresh registry
        reg2 = TaskRegistry()
        reg2.restore(snap)
        assert reg2.get(t1).state == TaskState.COMPLETED  # type: ignore[union-attr]
        # t2 was RUNNING → should become KILLED on restore
        assert reg2.get(t2).state == TaskState.KILLED  # type: ignore[union-attr]

    def test_restore_marks_running_as_killed(self) -> None:
        data = [
            {"task_id": "a-test1", "task_type": "background_agent",
             "state": "running", "created_at": 1.0, "metadata": {}},
            {"task_id": "a-test2", "task_type": "background_agent",
             "state": "completed", "created_at": 1.0, "metadata": {}},
        ]
        reg = TaskRegistry()
        reg.restore(data)
        assert reg.get("a-test1").state == TaskState.KILLED  # type: ignore[union-attr]
        assert reg.get("a-test2").state == TaskState.COMPLETED  # type: ignore[union-attr]


class TestTaskRecordSerialization:
    def test_to_dict(self) -> None:
        record = TaskRecord(
            task_id="a-12345678",
            task_type="background_agent",
            state=TaskState.RUNNING,
            created_at=100.0,
            metadata={"key": "val"},
        )
        d = record.to_dict()
        assert d["task_id"] == "a-12345678"
        assert d["state"] == "running"
        assert d["metadata"] == {"key": "val"}

    def test_from_dict(self) -> None:
        d = {"task_id": "t-abc", "task_type": "in_process_teammate",
             "state": "completed", "created_at": 50.0}
        record = TaskRecord.from_dict(d)
        assert record.task_id == "t-abc"
        assert record.state == TaskState.COMPLETED
        assert record.is_terminal is True
