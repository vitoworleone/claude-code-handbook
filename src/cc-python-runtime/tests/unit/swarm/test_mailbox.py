"""Tests for cc.swarm.mailbox — file-backed teammate messaging."""

from __future__ import annotations

import time

import pytest

from cc.swarm.mailbox import TeammateMailbox, TeammateMessage


@pytest.fixture
def mailbox(tmp_path: object) -> TeammateMailbox:
    """Create a mailbox with a temp claude dir."""
    from pathlib import Path

    return TeammateMailbox("test-team", claude_dir=Path(str(tmp_path)))


class TestTeammateMessage:
    def test_roundtrip(self) -> None:
        msg = TeammateMessage(
            from_name="alice",
            text="hello",
            timestamp=1234567890.0,
            read=False,
            summary="greeting",
        )
        d = msg.to_dict()
        restored = TeammateMessage.from_dict(d)
        assert restored.from_name == "alice"
        assert restored.text == "hello"
        assert restored.timestamp == 1234567890.0
        assert restored.read is False
        assert restored.summary == "greeting"

    def test_roundtrip_no_summary(self) -> None:
        msg = TeammateMessage(from_name="bob", text="hi", timestamp=0)
        d = msg.to_dict()
        assert "summary" not in d
        restored = TeammateMessage.from_dict(d)
        assert restored.summary is None


class TestTeammateMailbox:
    def test_send_and_receive(self, mailbox: TeammateMailbox) -> None:
        msg = TeammateMessage(
            from_name="alice", text="task done", timestamp=time.time()
        )
        mailbox.send("bob", msg)
        unread = mailbox.receive("bob")
        assert len(unread) == 1
        assert unread[0].from_name == "alice"
        assert unread[0].text == "task done"
        assert unread[0].read is False

    def test_receive_empty(self, mailbox: TeammateMailbox) -> None:
        assert mailbox.receive("nobody") == []

    def test_mark_all_read(self, mailbox: TeammateMailbox) -> None:
        mailbox.send("bob", TeammateMessage(from_name="a", text="1", timestamp=1.0))
        mailbox.send("bob", TeammateMessage(from_name="b", text="2", timestamp=2.0))
        assert len(mailbox.receive("bob")) == 2

        mailbox.mark_all_read("bob")
        assert len(mailbox.receive("bob")) == 0
        # But receive_all still returns them
        assert len(mailbox.receive_all("bob")) == 2

    def test_multiple_sends(self, mailbox: TeammateMailbox) -> None:
        for i in range(3):
            mailbox.send(
                "charlie",
                TeammateMessage(from_name="alice", text=f"msg-{i}", timestamp=float(i)),
            )
        all_msgs = mailbox.receive_all("charlie")
        assert len(all_msgs) == 3
        assert [m.text for m in all_msgs] == ["msg-0", "msg-1", "msg-2"]

    def test_send_forces_unread(self, mailbox: TeammateMailbox) -> None:
        msg = TeammateMessage(from_name="x", text="y", timestamp=0, read=True)
        mailbox.send("z", msg)
        unread = mailbox.receive("z")
        assert len(unread) == 1
        assert unread[0].read is False

    def test_separate_inboxes(self, mailbox: TeammateMailbox) -> None:
        mailbox.send("alice", TeammateMessage(from_name="x", text="for alice", timestamp=1.0))
        mailbox.send("bob", TeammateMessage(from_name="x", text="for bob", timestamp=2.0))
        assert len(mailbox.receive("alice")) == 1
        assert len(mailbox.receive("bob")) == 1
        assert mailbox.receive("alice")[0].text == "for alice"
        assert mailbox.receive("bob")[0].text == "for bob"
