"""Tests for MCP server tool registration and tool handlers."""

from unittest.mock import AsyncMock, patch

import pytest

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


class TestToolHandlers:
    """Test that each tool handler calls the correct client method."""

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.info.return_value = {"version": "1.4"}
        client.retrieve.return_value = {"data": "config"}
        client.return_values.return_value = {"data": ["10.0.0.1/24"]}
        client.exists.return_value = {"data": True}
        client.show.return_value = {"data": "output"}
        client.configure_confirm.return_value = {"success": True}
        client.confirm.return_value = {"success": True}
        client.save.return_value = {"success": True}
        client.generate.return_value = {"data": "key"}
        client.reset.return_value = {"success": True}
        client.load.return_value = {"success": True}
        client.merge.return_value = {"success": True}
        client.reboot.return_value = {"success": True}
        client.poweroff.return_value = {"success": True}
        client.image_add.return_value = {"success": True}
        client.image_delete.return_value = {"success": True}
        return client

    @pytest.fixture
    def mock_docs(self):
        docs = AsyncMock()
        docs.search.return_value = [{"path": "docs/firewall.rst", "title": "firewall"}]
        docs.read_page.return_value = "# Firewall\n\nContent here"
        return docs

    async def test_vyos_info(self, mock_client):
        from vyos_mcp.server import vyos_info

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_info()
        assert result == {"version": "1.4"}
        mock_client.info.assert_called_once()

    async def test_vyos_retrieve(self, mock_client):
        from vyos_mcp.server import vyos_retrieve

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_retrieve(["system", "host-name"])
        mock_client.retrieve.assert_called_once_with(["system", "host-name"])
        assert result == {"data": "config"}

    async def test_vyos_return_values(self, mock_client):
        from vyos_mcp.server import vyos_return_values

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_return_values(["interfaces", "eth0", "address"])
        mock_client.return_values.assert_called_once_with(
            ["interfaces", "eth0", "address"]
        )
        assert result == {"data": ["10.0.0.1/24"]}

    async def test_vyos_exists(self, mock_client):
        from vyos_mcp.server import vyos_exists

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_exists(["service", "https"])
        mock_client.exists.assert_called_once_with(["service", "https"])
        assert result == {"data": True}

    async def test_vyos_show(self, mock_client):
        from vyos_mcp.server import vyos_show

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_show(["interfaces"])
        mock_client.show.assert_called_once_with(["interfaces"])
        assert result == {"data": "output"}

    async def test_vyos_configure(self, mock_client):
        from vyos_mcp.server import vyos_configure

        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_configure(cmds)
        mock_client.configure_confirm.assert_called_once_with(cmds)
        assert result == {"success": True}

    async def test_vyos_confirm(self, mock_client):
        from vyos_mcp.server import vyos_confirm

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_confirm()
        mock_client.confirm.assert_called_once()
        assert result == {"success": True}

    async def test_vyos_save(self, mock_client):
        from vyos_mcp.server import vyos_save

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_save()
        mock_client.save.assert_called_once()
        assert result == {"success": True}

    async def test_vyos_generate(self, mock_client):
        from vyos_mcp.server import vyos_generate

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_generate(["pki", "wireguard", "key-pair"])
        mock_client.generate.assert_called_once_with(["pki", "wireguard", "key-pair"])
        assert result == {"data": "key"}

    async def test_vyos_reset(self, mock_client):
        from vyos_mcp.server import vyos_reset

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_reset(["ip", "bgp", "192.0.2.11"])
        mock_client.reset.assert_called_once_with(["ip", "bgp", "192.0.2.11"])
        assert result == {"success": True}

    async def test_vyos_load(self, mock_client):
        from vyos_mcp.server import vyos_load

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_load("/config/test.config")
        mock_client.load.assert_called_once_with("/config/test.config")
        assert result == {"success": True}

    async def test_vyos_merge(self, mock_client):
        from vyos_mcp.server import vyos_merge

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_merge(file="/config/test.config")
        mock_client.merge.assert_called_once_with(
            file="/config/test.config", string=None
        )
        assert result == {"success": True}

    async def test_vyos_merge_string(self, mock_client):
        from vyos_mcp.server import vyos_merge

        cfg = 'interfaces { ethernet eth1 { description "test" } }'
        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_merge(string=cfg)
        mock_client.merge.assert_called_once_with(file=None, string=cfg)
        assert result == {"success": True}

    async def test_vyos_reboot(self, mock_client):
        from vyos_mcp.server import vyos_reboot

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_reboot()
        mock_client.reboot.assert_called_once()
        assert result == {"success": True}

    async def test_vyos_poweroff(self, mock_client):
        from vyos_mcp.server import vyos_poweroff

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_poweroff()
        mock_client.poweroff.assert_called_once()
        assert result == {"success": True}

    async def test_vyos_image_add(self, mock_client):
        from vyos_mcp.server import vyos_image_add

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_image_add("https://downloads.vyos.io/latest.iso")
        mock_client.image_add.assert_called_once_with(
            "https://downloads.vyos.io/latest.iso"
        )
        assert result == {"success": True}

    async def test_vyos_image_delete(self, mock_client):
        from vyos_mcp.server import vyos_image_delete

        with patch("vyos_mcp.server._get_client", return_value=mock_client):
            result = await vyos_image_delete("1.4-rolling-202102280559")
        mock_client.image_delete.assert_called_once_with("1.4-rolling-202102280559")
        assert result == {"success": True}

    async def test_vyos_docs_search(self, mock_docs):
        from vyos_mcp.server import vyos_docs_search

        with patch("vyos_mcp.server._docs_client", mock_docs):
            result = await vyos_docs_search("firewall", max_results=5)
        mock_docs.search.assert_called_once_with("firewall", 5)
        assert result == [{"path": "docs/firewall.rst", "title": "firewall"}]

    async def test_vyos_docs_read(self, mock_docs):
        from vyos_mcp.server import vyos_docs_read

        with patch("vyos_mcp.server._docs_client", mock_docs):
            result = await vyos_docs_read("docs/firewall.rst")
        mock_docs.read_page.assert_called_once_with("docs/firewall.rst")
        assert result == "# Firewall\n\nContent here"
