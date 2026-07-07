# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import logging

from mewcode.config import MCPServerConfig
from mewcode.mcp.client import MCPClient
from mewcode.mcp.tool_wrapper import MCPToolWrapper
from mewcode.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:


    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}


    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        for cfg in configs:
            self._configs[cfg.name] = cfg


    async def register_all_tools(self, registry: ToolRegistry) -> list[str]:
        errors: list[str] = []
        for name, config in self._configs.items():
            try:
                client = MCPClient(config)
                await client.connect()
                self._clients[name] = client

                tools = await client.list_tools()
                for tool_def in tools:
                    wrapper = MCPToolWrapper(name, tool_def, client)
                    registry.register(wrapper)
                    logger.info("Registered MCP tool: %s", wrapper.name)

            except Exception as e:
                msg = f"MCP server '{name}': {e}"
                logger.warning(msg)
                errors.append(msg)

        return errors


    async def get_client(self, name: str) -> MCPClient | None:
        client = self._clients.get(name)
        if client is None:
            config = self._configs.get(name)
            if config is None:
                return None
            client = MCPClient(config)
            await client.connect()
            self._clients[name] = client
            return client

        if not client.is_alive:
            logger.info("Reconnecting MCP server '%s'", name)
            await client.close()
            client = MCPClient(self._configs[name])
            await client.connect()
            self._clients[name] = client

        return client


    async def shutdown(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info("MCP server '%s' closed", name)
            except Exception:
                logger.debug("Error closing MCP server '%s'", name, exc_info=True)
        self._clients.clear()
