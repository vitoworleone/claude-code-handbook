"""Tests for BashTool.

Verifies T4.3: Command execution, timeout, truncation, concurrency safety.
"""


from cc.tools.bash.bash_tool import BashTool


class TestBashTool:
    async def test_echo_hello(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "echo hello"})
        assert result.content.strip() == "hello"
        assert result.is_error is False

    async def test_exit_code(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "exit 1"})
        assert result.is_error is True
        assert "Exit code: 1" in result.content

    async def test_utf8(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "echo '你好'"})
        assert "你好" in result.content

    async def test_timeout(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": 'python -c "import time; time.sleep(100)"', "timeout": 1000})
        assert result.is_error is True
        assert "timed out" in result.content.lower()

    async def test_empty_command(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": ""})
        assert result.is_error is True

    async def test_stderr_captured(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "echo error >&2"})
        assert "error" in result.content

    def test_concurrency_safe_ls(self) -> None:
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "ls"}) is True

    def test_concurrency_safe_windows_read_commands(self) -> None:
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "dir /s /b"}) is True
        assert tool.is_concurrency_safe({"command": "tree /f"}) is True
        assert tool.is_concurrency_safe({"command": "where python"}) is True

    def test_concurrency_safe_git_status(self) -> None:
        """FIX check.md #2: git status must be detected as read-only."""
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "git status"}) is True
        assert tool.is_concurrency_safe({"command": "git log --oneline"}) is True
        assert tool.is_concurrency_safe({"command": "git diff HEAD"}) is True

    def test_concurrency_unsafe_git_push(self) -> None:
        """git push is not read-only."""
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "git push origin main"}) is False
        assert tool.is_concurrency_safe({"command": "git commit -m 'x'"}) is False

    def test_concurrency_unsafe_rm(self) -> None:
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "rm -rf /"}) is False

    async def test_cwd(self, tmp_path: object) -> None:
        tool = BashTool(cwd=str(tmp_path))
        result = await tool.execute({"command": 'python -c "import os; print(os.getcwd())"'})
        assert str(tmp_path) in result.content
