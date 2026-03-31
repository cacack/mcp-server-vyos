"""Tests for MCP server tool registration."""

from vyos_mcp.server import mcp

EXPECTED_TOOLS = [
    "vyos_info",
    "vyos_retrieve",
    "vyos_return_values",
    "vyos_exists",
    "vyos_show",
    "vyos_configure",
    "vyos_confirm",
    "vyos_save",
    "vyos_load",
    "vyos_merge",
    "vyos_generate",
    "vyos_reset",
    "vyos_reboot",
    "vyos_poweroff",
    "vyos_image_add",
    "vyos_image_delete",
    "vyos_docs_search",
    "vyos_docs_read",
]


def test_all_tools_registered():
    """Verify all expected tools are registered with the MCP server."""
    tool_names = list(mcp._tool_manager._tools.keys())
    for name in EXPECTED_TOOLS:
        assert name in tool_names, f"Tool {name} not registered"


def test_no_unexpected_tools():
    """Verify no extra tools are registered that we don't expect."""
    tool_names = set(mcp._tool_manager._tools.keys())
    expected = set(EXPECTED_TOOLS)
    unexpected = tool_names - expected
    assert not unexpected, f"Unexpected tools registered: {unexpected}"


def test_tool_count():
    """Verify total tool count matches expectations."""
    assert len(mcp._tool_manager._tools) == 18
