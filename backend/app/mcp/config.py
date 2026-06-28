"""MCP config schema + YAML loader (§12.2, Stage 7 Task 7.1).

Missing config file → empty MCPConfig so MCP is simply off.
"""
from __future__ import annotations

import logging
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from app.config import settings

log = logging.getLogger(__name__)


class MCPAuth(BaseModel):
    type: Literal["none", "bearer"] = "none"
    token_env: str | None = None

    @model_validator(mode="after")
    def _require_token_env_for_bearer(self) -> "MCPAuth":
        if self.type == "bearer" and not self.token_env:
            raise ValueError("auth.token_env is required when auth.type is 'bearer'")
        return self


class MCPServerConfig(BaseModel):
    name: str
    transport: Literal["streamable-http"] = "streamable-http"
    url: str
    auth: MCPAuth = MCPAuth()
    enabled: bool = True
    allowed_tools: list[str] = []


class MCPConfig(BaseModel):
    mcp_servers: list[MCPServerConfig] = []


def load_mcp_config(path: str | None = None) -> MCPConfig:
    """Load MCPConfig from *path* (or settings.mcp_config_path).

    Returns an empty MCPConfig if the file is missing — MCP is just off.
    """
    resolved = path if path is not None else settings.mcp_config_path
    try:
        with open(resolved) as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        log.debug("MCP config not found at %r — MCP disabled.", resolved)
        return MCPConfig()
    return MCPConfig.model_validate(data)
