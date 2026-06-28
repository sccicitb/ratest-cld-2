"""Tests for MCP config schema + loader (Stage 7, Task 7.1)."""
from __future__ import annotations

from app.mcp.config import MCPConfig, load_mcp_config


def test_valid_yaml_is_parsed(tmp_path):
    """A valid YAML file is parsed into MCPConfig with defaults applied."""
    cfg_file = tmp_path / "mcp.yaml"
    cfg_file.write_text(
        "mcp_servers:\n"
        "  - name: test-server\n"
        "    url: http://test:8800/mcp\n"
        "    auth: {type: none}\n"
        "    enabled: true\n"
        "    allowed_tools: []\n"
    )
    cfg = load_mcp_config(str(cfg_file))
    assert isinstance(cfg, MCPConfig)
    assert len(cfg.mcp_servers) == 1
    srv = cfg.mcp_servers[0]
    assert srv.name == "test-server"
    assert srv.url == "http://test:8800/mcp"
    assert srv.transport == "streamable-http"  # default
    assert srv.enabled is True
    assert srv.allowed_tools == []
    assert srv.auth.type == "none"


def test_missing_file_returns_empty_config(tmp_path):
    """A missing YAML file returns an empty MCPConfig (MCP is just off)."""
    cfg = load_mcp_config(str(tmp_path / "nonexistent.yaml"))
    assert isinstance(cfg, MCPConfig)
    assert cfg.mcp_servers == []


def test_allowed_tools_roundtrip(tmp_path):
    """allowed_tools list is preserved exactly."""
    cfg_file = tmp_path / "mcp.yaml"
    cfg_file.write_text(
        "mcp_servers:\n"
        "  - name: srv\n"
        "    url: http://srv/mcp\n"
        "    allowed_tools: [tool_a, tool_b]\n"
    )
    cfg = load_mcp_config(str(cfg_file))
    assert cfg.mcp_servers[0].allowed_tools == ["tool_a", "tool_b"]


def test_enabled_false_roundtrip(tmp_path):
    """enabled: false is preserved."""
    cfg_file = tmp_path / "mcp.yaml"
    cfg_file.write_text(
        "mcp_servers:\n"
        "  - name: srv\n"
        "    url: http://srv/mcp\n"
        "    enabled: false\n"
    )
    cfg = load_mcp_config(str(cfg_file))
    assert cfg.mcp_servers[0].enabled is False


def test_bearer_auth_roundtrip(tmp_path):
    """Bearer auth with token_env is preserved."""
    cfg_file = tmp_path / "mcp.yaml"
    cfg_file.write_text(
        "mcp_servers:\n"
        "  - name: srv\n"
        "    url: http://srv/mcp\n"
        "    auth:\n"
        "      type: bearer\n"
        "      token_env: MY_TOKEN\n"
    )
    cfg = load_mcp_config(str(cfg_file))
    auth = cfg.mcp_servers[0].auth
    assert auth.type == "bearer"
    assert auth.token_env == "MY_TOKEN"


def test_multiple_servers(tmp_path):
    """Multiple servers in one YAML file are all parsed."""
    cfg_file = tmp_path / "mcp.yaml"
    cfg_file.write_text(
        "mcp_servers:\n"
        "  - name: a\n"
        "    url: http://a/mcp\n"
        "  - name: b\n"
        "    url: http://b/mcp\n"
    )
    cfg = load_mcp_config(str(cfg_file))
    assert [s.name for s in cfg.mcp_servers] == ["a", "b"]
