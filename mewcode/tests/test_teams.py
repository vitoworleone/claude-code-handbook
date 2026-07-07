# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""Agent Team（智能体团队）系统的测试（第 14 章）。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mewcode.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    resolve_team_dir,
    unique_team_name,
)
from mewcode.teams.shared_task import SharedTask, SharedTaskStore
from mewcode.teams.mailbox import Mailbox, MailboxMessage, create_message
from mewcode.teams.registry import AgentNameRegistry
from mewcode.teams.backend_detect import BackendDetectionError, detect_backend
from mewcode.teams.coordinator import (
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    is_coordinator_mode,
    match_session_mode,
)
from mewcode.agents.tool_filter import (
    COORDINATOR_MODE_ALLOWED_TOOLS,
    IN_PROCESS_TEAMMATE_ALLOWED_TOOLS,
    TEAMMATE_COORDINATION_TOOLS,
    build_teammate_tools,
    apply_coordinator_filter,
)
from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult

# =====================================================================
# 辅助工具
# =====================================================================

class DummyTool(Tool):
    params_model = MagicMock

    def __init__(self, name: str, category: str = "read"):
        self.name = name
        self.description = f"Dummy {name}"
        self.category = category
        self.is_concurrency_safe = True
        self.is_system_tool = False

    def get_schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {}}

    async def execute(self, params):
        return ToolResult(output=f"{self.name} executed")

def make_registry(*tool_names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in tool_names:
        reg.register(DummyTool(name))
    return reg

@pytest.fixture(autouse=True)
def _reset_registry():
    AgentNameRegistry.reset()
    yield
    AgentNameRegistry.reset()

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)

# =====================================================================
# 1. AgentTeam / TeammateInfo
# =====================================================================

class TestModels:
    def test_teammate_info_roundtrip(self):
        info = TeammateInfo(
            name="alice",
            agent_id="abc123",
            agent_type="worker",
            model="sonnet",
            worktree_path="/tmp/wt",
            backend_type="tmux",
            is_active=True,
        )
        d = info.to_dict()
        restored = TeammateInfo.from_dict(d)
        assert restored.name == "alice"
        assert restored.agent_id == "abc123"
        assert restored.is_active is True

    def test_agent_team_save_load(self, tmp_dir):
        config_path = str(Path(tmp_dir) / "team" / "config.json")
        team = AgentTeam(
            name="test-team",
            lead_agent_id="lead-001",
            config_path=config_path,
            description="Test team",
        )
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="worker",
            model="sonnet", worktree_path="/tmp/wt1", backend_type="tmux",
        ))
        team.save()

        loaded = AgentTeam.load(config_path)
        assert loaded.name == "test-team"
        assert loaded.lead_agent_id == "lead-001"
        assert len(loaded.members) == 1
        assert loaded.members[0].name == "alice"

    def test_get_member(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
        ))
        assert team.get_member("bob") is not None
        assert team.get_member("b1") is not None
        assert team.get_member("nonexistent") is None

    def test_remove_member(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
        ))
        assert team.remove_member("bob") is True
        assert len(team.members) == 0
        assert team.remove_member("bob") is False

    def test_set_member_active(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=True,
        ))
        team.set_member_active("alice", False)
        assert team.members[0].is_active is False
        assert team.all_idle() is True

    def test_all_idle(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=False,
        ))
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=True,
        ))
        assert team.all_idle() is False

    def test_unique_team_name(self, tmp_dir):
        with patch("mewcode.teams.models.Path.home", return_value=Path(tmp_dir)):
            name1 = unique_team_name("my-team")
            assert name1 == "my-team"
            (Path(tmp_dir) / ".mewcode" / "teams" / "my-team").mkdir(parents=True)
            name2 = unique_team_name("my-team")
            assert name2 == "my-team-2"

# =====================================================================
# 2. SharedTaskStore
# =====================================================================

class TestSharedTaskStore:
    def test_create_and_get(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        task = store.create(title="Do something", description="Details", assignee="alice")
        assert task.id == "1"
        assert task.title == "Do something"

        fetched = store.get("1")
        assert fetched is not None
        assert fetched.assignee == "alice"

    def test_auto_increment_id(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        t1 = store.create(title="First")
        t2 = store.create(title="Second")
        assert t1.id == "1"
        assert t2.id == "2"

    def test_list_with_filters(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        store.create(title="A", assignee="alice")
        t2 = store.create(title="B", assignee="bob")
        store.update(t2.id, status="in_progress")

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 2

        pending = store.list_tasks(status="pending")
        assert len(pending) == 1
        assert pending[0].title == "A"

        bobs = store.list_tasks(assignee="bob")
        assert len(bobs) == 1

    def test_update_with_dependencies(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        store.create(title="Task A")
        store.create(title="Task B")

        updated = store.update("2", add_blocked_by=["1"])
        assert updated is not None
        assert "1" in updated.blocked_by

        updated = store.update("1", add_blocks=["2"])
        assert "2" in updated.blocks

    def test_update_nonexistent_returns_none(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        assert store.update("999") is None

    def test_persistence(self, tmp_dir):
        path = Path(tmp_dir) / "tasks.json"
        store1 = SharedTaskStore(path)
        store1.init_empty()
        store1.create(title="Persisted task")

        store2 = SharedTaskStore(path)
        tasks = store2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "Persisted task"

# =====================================================================
# 3. Mailbox
# =====================================================================

class TestMailbox:
    def test_write_and_consume(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "bob", "Hello bob", summary="greeting")
        mailbox.write("bob-agent-id", msg)

        messages = mailbox.consume("bob-agent-id")
        assert len(messages) == 1
        assert messages[0].content == "Hello bob"
        assert messages[0].from_agent == "alice"

        # 已被消费 —— 此时应该为空
        messages2 = mailbox.consume("bob-agent-id")
        assert len(messages2) == 0

    def test_read_without_consume(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "bob", "Peek")
        mailbox.write("bob-id", msg)

        messages = mailbox.read("bob-id")
        assert len(messages) == 1

        # 仍然存在
        messages2 = mailbox.read("bob-id")
        assert len(messages2) == 1

    def test_broadcast(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "*", "Team update", summary="update")
        mailbox.broadcast(["bob-id", "charlie-id", "alice-id"], msg, exclude="alice-id")

        bob_msgs = mailbox.consume("bob-id")
        charlie_msgs = mailbox.consume("charlie-id")
        alice_msgs = mailbox.consume("alice-id")

        assert len(bob_msgs) == 1
        assert len(charlie_msgs) == 1
        assert len(alice_msgs) == 0

    def test_cleanup(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("a", "b", "test")
        mailbox.write("agent-1", msg)
        mailbox.cleanup("agent-1")
        assert len(mailbox.read("agent-1")) == 0

    def test_empty_mailbox(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        assert mailbox.consume("nonexistent") == []
        assert mailbox.read("nonexistent") == []

# =====================================================================
# 4. AgentNameRegistry
# =====================================================================

class TestAgentNameRegistry:

    def test_register_and_resolve(self):
        reg = AgentNameRegistry.instance()
        reg.register("alice", "agent-abc")
        assert reg.resolve("alice") == "agent-abc"
        assert reg.resolve("agent-abc") == "agent-abc"  # 直接按 ID 查找
        assert reg.resolve("nonexistent") is None

    def test_unregister(self):
        reg = AgentNameRegistry.instance()
        reg.register("bob", "agent-xyz")
        reg.unregister("bob")
        assert reg.resolve("bob") is None

    def test_list_all(self):
        reg = AgentNameRegistry.instance()
        reg.register("alice", "a1")
        reg.register("bob", "b1")
        all_names = reg.list_all()
        assert all_names == {"alice": "a1", "bob": "b1"}

    def test_singleton(self):
        r1 = AgentNameRegistry.instance()
        r2 = AgentNameRegistry.instance()
        assert r1 is r2

# =====================================================================
# 5. Backend Detection（后端探测）
# =====================================================================

class TestBackendDetect:
    def test_in_process_mode(self):
        result = detect_backend(teammate_mode="in-process")
        assert result == BackendType.IN_PROCESS

    def test_non_interactive(self):
        result = detect_backend(is_interactive=False)
        assert result == BackendType.IN_PROCESS

    def test_tmux_session(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1234/default,12345,0"}):
            result = detect_backend()
            assert result == BackendType.TMUX

    def test_iterm2_with_it2(self):
        env = {"TERM_PROGRAM": "iTerm.app"}
        with patch.dict(os.environ, env, clear=False):
            with patch("mewcode.teams.backend_detect.shutil.which") as mock_which:
                def which_side_effect(cmd):
                    if cmd == "it2":
                        return "/usr/local/bin/it2"
                    if cmd == "tmux":
                        return None
                    return None
                mock_which.side_effect = which_side_effect
                with patch.dict(os.environ, {"TMUX": ""}, clear=False):
                    os.environ.pop("TMUX", None)
                    result = detect_backend()
                    assert result == BackendType.ITERM2

    def test_tmux_installed_not_in_session(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMUX", None)
            os.environ.pop("TERM_PROGRAM", None)
            with patch("mewcode.teams.backend_detect.shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/tmux"
                result = detect_backend()
                assert result == BackendType.TMUX

    def test_no_backend_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMUX", None)
            os.environ.pop("TERM_PROGRAM", None)
            with patch("mewcode.teams.backend_detect.shutil.which", return_value=None):
                with pytest.raises(BackendDetectionError):
                    detect_backend()

# =====================================================================
# 6. Tool Filtering（工具过滤）
# =====================================================================

class TestToolFilter:
    def test_teammate_coordination_tools_in_allowed(self):
        for tool_name in TEAMMATE_COORDINATION_TOOLS:
            assert tool_name in IN_PROCESS_TEAMMATE_ALLOWED_TOOLS

    def test_coordinator_mode_tools(self):
        assert "Agent" in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "SendMessage" in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "TaskStop" in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "SyntheticOutput" in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "ReadFile" not in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "WriteFile" not in COORDINATOR_MODE_ALLOWED_TOOLS
        assert "Bash" not in COORDINATOR_MODE_ALLOWED_TOOLS

    def test_apply_coordinator_filter(self):
        reg = make_registry(
            "Agent", "ReadFile", "WriteFile", "Bash", "SendMessage",
            "TaskStop", "SyntheticOutput", "TeamCreate", "TeamDelete",
        )
        filtered = apply_coordinator_filter(reg)
        names = {t.name for t in filtered.list_tools()}
        assert "Agent" in names
        assert "SendMessage" in names
        assert "SyntheticOutput" in names
        assert "ReadFile" not in names
        assert "Bash" not in names

# =====================================================================
# 7. Coordinator Mode（协调者模式）
# =====================================================================

class TestCoordinatorMode:
    def test_disabled_by_default(self):
        assert is_coordinator_mode(enable_flag=False) is False

    def test_enabled_with_flag_and_env(self):
        with patch.dict(os.environ, {"MEWCODE_COORDINATOR_MODE": "1"}):
            assert is_coordinator_mode(enable_flag=True) is True

    def test_flag_without_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEWCODE_COORDINATOR_MODE", None)
            assert is_coordinator_mode(enable_flag=True) is False

    def test_env_without_flag(self):
        with patch.dict(os.environ, {"MEWCODE_COORDINATOR_MODE": "1"}):
            assert is_coordinator_mode(enable_flag=False) is False

    def test_system_prompt_contains_phases(self):
        prompt = get_coordinator_system_prompt()
        assert "Research" in prompt
        assert "Synthesis" in prompt
        assert "Implementation" in prompt
        assert "Verification" in prompt

    def test_system_prompt_anti_pattern(self):
        prompt = get_coordinator_system_prompt()
        assert "based on your findings" in prompt.lower()
        assert "Anti-pattern" in prompt or "BAD" in prompt

    def test_system_prompt_continue_vs_spawn(self):
        prompt = get_coordinator_system_prompt()
        assert "Continue" in prompt
        assert "Spawn fresh" in prompt

    def test_system_prompt_task_notification(self):
        prompt = get_coordinator_system_prompt()
        assert "<task-notification>" in prompt
        assert "<task-id>" in prompt

    def test_match_session_mode_no_switch(self):
        with patch.dict(os.environ, {"MEWCODE_COORDINATOR_MODE": "1"}):
            result = match_session_mode("coordinator", enable_flag=True)
            assert result is None

    def test_match_session_mode_switch_to_coordinator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEWCODE_COORDINATOR_MODE", None)
            result = match_session_mode("coordinator", enable_flag=True)
            assert result is not None
            assert "Entered" in result
            assert os.environ.get("MEWCODE_COORDINATOR_MODE") == "1"

    def test_match_session_mode_none(self):
        result = match_session_mode(None)
        assert result is None

    def test_coordinator_user_context(self):
        ctx = get_coordinator_user_context()
        assert "workerToolsContext" in ctx
        assert "Workers" in ctx["workerToolsContext"]

# =====================================================================
# 8. Config Extensions（配置项扩展）
# =====================================================================

class TestConfigExtensions:
    def test_teammate_mode_defaults(self):
        from mewcode.config import AppConfig
        cfg = AppConfig(providers=[])
        assert cfg.teammate_mode == ""
        assert cfg.enable_coordinator_mode is False

    def test_load_config_with_team_fields(self, tmp_dir):
        from mewcode.config import load_config
        config_path = Path(tmp_dir) / "config.yaml"
        config_path.write_text(
            "providers:\n"
            "  - name: test\n"
            "    protocol: anthropic\n"
            "    base_url: http://localhost\n"
            "    model: test-model\n"
            "teammate_mode: 'in-process'\n"
            "enable_coordinator_mode: true\n"
        )
        cfg = load_config(config_path)
        assert cfg.teammate_mode == "in-process"
        assert cfg.enable_coordinator_mode is True

    def test_invalid_teammate_mode(self, tmp_dir):
        from mewcode.config import ConfigError, load_config
        config_path = Path(tmp_dir) / "config.yaml"
        config_path.write_text(
            "providers:\n"
            "  - name: test\n"
            "    protocol: anthropic\n"
            "    base_url: http://localhost\n"
            "    model: test-model\n"
            "teammate_mode: 'invalid'\n"
        )
        with pytest.raises(ConfigError):
            load_config(config_path)

# =====================================================================
# 9. Transcript Persistence（会话记录持久化）
# =====================================================================

class TestTranscript:

    def test_save_and_load(self, tmp_dir):
        from mewcode.conversation import ConversationManager
        from mewcode.teams.transcript import load_transcript, save_transcript

        conv = ConversationManager()
        conv.add_user_message("Hello agent")
        conv.add_assistant_message("Hello user")

        with patch("mewcode.teams.models.Path.home", return_value=Path(tmp_dir)):
            save_transcript("test-team", "agent-001", conv)
            restored = load_transcript("test-team", "agent-001")

        assert restored is not None
        assert len(restored.history) == 2
        assert restored.history[0].role == "user"
        assert restored.history[0].content == "Hello agent"
        assert restored.history[1].role == "assistant"

    def test_load_nonexistent(self, tmp_dir):
        from mewcode.teams.transcript import load_transcript
        with patch("mewcode.teams.models.Path.home", return_value=Path(tmp_dir)):
            result = load_transcript("no-team", "no-agent")
        assert result is None

# =====================================================================
# 10. Agent build_system_prompt 集成测试
# =====================================================================

class TestAgentCoordinatorIntegration:
    def test_normal_prompt(self):
        from mewcode.prompts import build_system_prompt, BASE_PERSONA
        prompt = build_system_prompt()
        assert BASE_PERSONA in prompt

    def test_coordinator_prompt(self):
        from mewcode.prompts import build_system_prompt
        prompt = build_system_prompt(coordinator_mode=True)
        assert "coordinator" in prompt.lower()
        assert "Research" in prompt
        assert "Synthesis" in prompt

    def test_coordinator_overrides_plan(self):
        from mewcode.prompts import build_system_prompt, PLAN_MODE_INSTRUCTIONS
        prompt = build_system_prompt(plan_mode=True, coordinator_mode=True)
        assert PLAN_MODE_INSTRUCTIONS not in prompt
        assert "coordinator" in prompt.lower()
