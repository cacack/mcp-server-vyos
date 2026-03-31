"""Tests for VyOS API client."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from vyos_mcp.client import VyOSClient

URL = "https://vyos.example.com"
KEY = "test-key"


def make_client(**kwargs) -> VyOSClient:
    return VyOSClient(url=URL, api_key=KEY, **kwargs)


class TestInit:
    def test_requires_url(self):
        with pytest.raises(ValueError, match="VyOS URL required"):
            VyOSClient(api_key="test")

    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="API key required"):
            VyOSClient(url=URL)

    def test_basic_init(self):
        client = make_client()
        assert client.url == URL
        assert client.api_key == KEY
        assert client.verify_ssl is False

    def test_strips_trailing_slash(self):
        client = VyOSClient(url="https://vyos.example.com/", api_key=KEY)
        assert client.url == URL

    def test_verify_ssl(self):
        client = make_client(verify_ssl=True)
        assert client.verify_ssl is True

    def test_env_vars(self, monkeypatch):
        monkeypatch.setenv("VYOS_URL", URL)
        monkeypatch.setenv("VYOS_API_KEY", KEY)
        client = VyOSClient()
        assert client.url == URL
        assert client.api_key == KEY


class TestPayloads:
    """Verify the exact payloads sent to the VyOS API.

    These tests mock _post to capture the endpoint and data arguments,
    ensuring we build the correct payloads (the bugs we found during
    real router testing).
    """

    @pytest.fixture
    def client(self):
        c = make_client()
        c._post = AsyncMock(return_value={"success": True, "data": None, "error": None})
        return c

    async def test_retrieve(self, client):
        await client.retrieve(["system", "host-name"])
        client._post.assert_called_once_with(
            "retrieve", {"op": "showConfig", "path": ["system", "host-name"]}
        )

    async def test_return_values(self, client):
        await client.return_values(["interfaces", "ethernet", "eth0", "address"])
        client._post.assert_called_once_with(
            "retrieve",
            {
                "op": "returnValues",
                "path": ["interfaces", "ethernet", "eth0", "address"],
            },
        )

    async def test_exists(self, client):
        await client.exists(["service", "https", "api"])
        client._post.assert_called_once_with(
            "retrieve", {"op": "exists", "path": ["service", "https", "api"]}
        )

    async def test_configure(self, client):
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        await client.configure(cmds)
        client._post.assert_called_once_with("configure", cmds)

    async def test_configure_confirm_single(self, client):
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        await client.configure_confirm(cmds, confirm_minutes=3)
        client._post.assert_called_once_with(
            "configure",
            [{"op": "set", "path": ["interfaces", "dummy", "dum0"], "confirm_time": 3}],
        )

    async def test_configure_confirm_batch(self, client):
        cmds = [
            {"op": "set", "path": ["interfaces", "dummy", "dum0"]},
            {"op": "set", "path": ["interfaces", "dummy", "dum1"]},
        ]
        await client.configure_confirm(cmds, confirm_minutes=5)
        expected = [
            {"op": "set", "path": ["interfaces", "dummy", "dum0"], "confirm_time": 5},
            {"op": "set", "path": ["interfaces", "dummy", "dum1"]},
        ]
        client._post.assert_called_once_with("configure", expected)

    async def test_confirm(self, client):
        await client.confirm()
        client._post.assert_called_once_with("configure", {"op": "confirm", "path": []})

    async def test_save_default(self, client):
        await client.save()
        client._post.assert_called_once_with("config-file", {"op": "save"})

    async def test_save_to_file(self, client):
        await client.save(file="/config/backup.boot")
        client._post.assert_called_once_with(
            "config-file", {"op": "save", "file": "/config/backup.boot"}
        )

    async def test_load(self, client):
        await client.load("/config/test.config")
        client._post.assert_called_once_with(
            "config-file", {"op": "load", "file": "/config/test.config"}
        )

    async def test_merge_file(self, client):
        await client.merge(file="/config/test.config")
        client._post.assert_called_once_with(
            "config-file", {"op": "merge", "file": "/config/test.config"}
        )

    async def test_merge_string(self, client):
        cfg = 'interfaces { ethernet eth1 { description "test" } }'
        await client.merge(string=cfg)
        client._post.assert_called_once_with(
            "config-file", {"op": "merge", "string": cfg}
        )

    async def test_show(self, client):
        await client.show(["interfaces"])
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["interfaces"]}
        )

    async def test_generate(self, client):
        await client.generate(["pki", "wireguard", "key-pair"])
        client._post.assert_called_once_with(
            "generate", {"op": "generate", "path": ["pki", "wireguard", "key-pair"]}
        )

    async def test_reset(self, client):
        await client.reset(["ip", "bgp", "192.0.2.11"])
        client._post.assert_called_once_with(
            "reset", {"op": "reset", "path": ["ip", "bgp", "192.0.2.11"]}
        )

    async def test_reboot(self, client):
        await client.reboot()
        client._post.assert_called_once_with(
            "reboot", {"op": "reboot", "path": ["now"]}
        )

    async def test_poweroff(self, client):
        await client.poweroff()
        client._post.assert_called_once_with(
            "poweroff", {"op": "poweroff", "path": ["now"]}
        )

    async def test_image_add(self, client):
        await client.image_add("https://downloads.vyos.io/latest.iso")
        client._post.assert_called_once_with(
            "image", {"op": "add", "url": "https://downloads.vyos.io/latest.iso"}
        )

    async def test_image_delete(self, client):
        await client.image_delete("1.4-rolling-202102280559")
        client._post.assert_called_once_with(
            "image", {"op": "delete", "name": "1.4-rolling-202102280559"}
        )


class TestPostEncoding:
    """Verify _post sends correct form-encoded data to httpx."""

    async def test_form_encoding(self):
        client = make_client()
        mock_response = AsyncMock()
        mock_response.json.return_value = {"success": True, "data": {}, "error": None}
        mock_response.raise_for_status = lambda: None

        with patch("vyos_mcp.client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            await client._post("retrieve", {"op": "showConfig", "path": []})

            mock_http.post.assert_called_once_with(
                f"{URL}/retrieve",
                data={
                    "data": json.dumps({"op": "showConfig", "path": []}),
                    "key": KEY,
                },
            )

    async def test_timeout_and_ssl(self):
        client = make_client(verify_ssl=True)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = lambda: None

        with patch("vyos_mcp.client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            await client._post("show", {"op": "show", "path": []})

            mock_cls.assert_called_once_with(verify=True, timeout=30)
