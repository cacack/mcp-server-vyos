"""Tests for VyOS API client."""

import json
from unittest.mock import AsyncMock, call, patch

import httpx
import pytest

from vyos_mcp.client import VyOSClient, _parse_commit_history

# Minimal response configure_confirm accepts as an armed commit-confirm.
ARMED = {
    "success": True,
    "data": "Initialized commit-confirm; 5 minutes to confirm before reload",
    "error": None,
}

URL = "https://vyos.example.com"
KEY = "test-key"


def make_client(**kwargs) -> VyOSClient:
    return VyOSClient(url=URL, api_key=KEY, **kwargs)


# What `show system commit file N` returns for a revision that doesn't exist.
MISSING_REVISION = {
    "success": True,
    "data": "Traceback (most recent call last):\n  ...\n"
    "vyos.config_mgmt.ConfigMgmtError: revision not available\n",
    "error": None,
}


def commit_files(files: dict[int, str | dict]):
    """_post side effect serving `show system commit file N` from `files`.

    A str value is served as that revision's text and a dict as the raw
    response. Revisions not in `files` get MISSING_REVISION.
    """

    def side_effect(endpoint, data):
        served = files.get(int(data["path"][-1]), MISSING_REVISION)
        if isinstance(served, str):
            return {"success": True, "data": served, "error": None}
        return served

    return side_effect


def commit_file_call(rev: int):
    """The _post call that fetches commit revision `rev`."""
    return call("show", {"op": "show", "path": ["system", "commit", "file", str(rev)]})


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

    async def test_validate(self, client):
        client._post.return_value = ARMED
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        await client.validate(cmds)
        client._post.assert_called_once_with(
            "configure", {"commands": cmds, "confirm_time": 1}
        )

    async def test_configure_confirm_single(self, client):
        client._post.return_value = ARMED
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        await client.configure_confirm(cmds, confirm_minutes=3)
        client._post.assert_called_once_with(
            "configure", {"commands": cmds, "confirm_time": 3}
        )

    async def test_configure_confirm_batch(self, client):
        client._post.return_value = ARMED
        cmds = [
            {"op": "set", "path": ["interfaces", "dummy", "dum0"]},
            {"op": "set", "path": ["interfaces", "dummy", "dum1"]},
        ]
        await client.configure_confirm(cmds, confirm_minutes=5)
        # confirm_time is top-level; the router ignores it on commands
        client._post.assert_called_once_with(
            "configure", {"commands": cmds, "confirm_time": 5}
        )

    async def test_configure_confirm_armed_returns_result(self, client):
        client._post.return_value = ARMED
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        assert await client.configure_confirm(cmds) == ARMED

    @pytest.mark.parametrize("data", ["", None, "Configuration committed"])
    async def test_configure_confirm_not_armed_raises(self, client, data):
        client._post.return_value = {"success": True, "data": data, "error": None}
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        with pytest.raises(RuntimeError, match="PERMANENTLY"):
            await client.configure_confirm(cmds)

    async def test_configure_confirm_mention_is_not_armed(self, client):
        client._post.return_value = {
            "success": True,
            "data": "commit-confirm not supported, committing normally",
            "error": None,
        }
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        with pytest.raises(RuntimeError, match="PERMANENTLY"):
            await client.configure_confirm(cmds)

    async def test_configure_confirm_background_commit_warns(self, client):
        result = {
            "success": True,
            "data": "Requested HTTP API server configuration change; "
            "commit will be called in the background",
            "error": None,
        }
        client._post.return_value = result
        cmds = [{"op": "set", "path": ["service", "https", "port", "8443"]}]
        out = await client.configure_confirm(cmds)
        assert out["data"] == result["data"]
        assert "could NOT be verified" in out["warning"]

    async def test_configure_confirm_pending_raises(self, client):
        request = httpx.Request("POST", f"{URL}/configure")
        response = httpx.Response(
            400,
            json={"success": False, "error": "Another confirm is pending\n"},
            request=request,
        )
        client._post.side_effect = httpx.HTTPStatusError(
            "400", request=request, response=response
        )
        cmds = [{"op": "set", "path": ["interfaces", "dummy", "dum0"]}]
        with pytest.raises(RuntimeError, match="COMMITTED"):
            await client.configure_confirm(cmds)

    async def test_configure_confirm_other_http_error_propagates(self, client):
        request = httpx.Request("POST", f"{URL}/configure")
        response = httpx.Response(
            400, json={"success": False, "error": "invalid path"}, request=request
        )
        client._post.side_effect = httpx.HTTPStatusError(
            "400", request=request, response=response
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.configure_confirm([{"op": "set", "path": ["bogus"]}])

    async def test_configure_confirm_empty_raises(self, client):
        with pytest.raises(ValueError, match="non-empty"):
            await client.configure_confirm([])
        client._post.assert_not_called()

    async def test_configure_confirm_error_passes_through(self, client):
        result = {"success": False, "data": None, "error": "invalid path"}
        client._post.return_value = result
        cmds = [{"op": "set", "path": ["bogus"]}]
        assert await client.configure_confirm(cmds) == result

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

    async def test_config_diff_default(self, client):
        client._post.side_effect = commit_files({0: "a\nb-new\n", 1: "a\nb-old\n"})
        result = await client.config_diff()
        assert client._post.call_args_list == [commit_file_call(0), commit_file_call(1)]
        assert result["success"] is True
        assert "--- revision 1" in result["data"]
        assert "+++ revision 0" in result["data"]
        assert "-b-old\n" in result["data"]
        assert "+b-new\n" in result["data"]

    async def test_config_diff_none_means_latest(self, client):
        client._post.side_effect = commit_files({0: "x\n", 1: "y\n"})
        await client.config_diff(None)
        assert client._post.call_args_list == [commit_file_call(0), commit_file_call(1)]

    async def test_config_diff_with_rev(self, client):
        client._post.side_effect = commit_files({5: "x\n", 6: "y\n"})
        await client.config_diff(rev=5)
        assert client._post.call_args_list == [commit_file_call(5), commit_file_call(6)]

    async def test_config_diff_no_changes(self, client):
        client._post.side_effect = commit_files({0: "same\n", 1: "same\n"})
        result = await client.config_diff()
        assert result["data"] == "No changes between revisions 1 and 0"

    async def test_config_diff_missing_final_newline(self, client):
        # A last line without "\n" must not run into the next diff line.
        client._post.side_effect = commit_files({0: "a\nnew", 1: "a\nold"})
        result = await client.config_diff()
        assert "-old\n+new\n" in result["data"]

    async def test_config_diff_unavailable_rev(self, client):
        client._post.side_effect = commit_files({})
        with pytest.raises(ValueError, match="revision 999 is not available"):
            await client.config_diff(rev=999)

    async def test_config_diff_oldest_rev(self, client):
        client._post.side_effect = commit_files({0: "a\n"})
        with pytest.raises(ValueError, match="oldest retained revision"):
            await client.config_diff()

    async def test_config_diff_other_failure_is_not_oldest(self, client):
        # Only the router's "revision not available" means rev is the oldest.
        failed = {"success": False, "data": None, "error": "boom"}
        client._post.side_effect = commit_files({0: "a\n", 1: failed})
        with pytest.raises(ValueError, match="revision 1: 'boom'") as exc:
            await client.config_diff()
        assert "oldest" not in str(exc.value)

    async def test_config_diff_non_string_data(self, client):
        weird = {"success": True, "data": None, "error": None}
        client._post.side_effect = commit_files({0: weird})
        with pytest.raises(ValueError, match="Could not fetch config revision 0"):
            await client.config_diff()

    async def test_config_diff_unexpected_traceback(self, client):
        crash = {
            "success": True,
            "data": "Traceback (most recent call last):\n  ...\nOSError: disk\n",
            "error": None,
        }
        client._post.side_effect = commit_files({0: "a\n", 1: crash})
        with pytest.raises(ValueError, match="revision 1: OSError: disk"):
            await client.config_diff()

    async def test_config_diff_http_error(self, client):
        request = httpx.Request("POST", f"{URL}/show")
        client._post.side_effect = httpx.HTTPStatusError(
            "400",
            request=request,
            response=httpx.Response(400, text='{"error": "bad"}', request=request),
        )
        with pytest.raises(ValueError, match='revision 0: {"error": "bad"}'):
            await client.config_diff()

    async def test_config_diff_rejects_negative_rev(self, client):
        with pytest.raises(ValueError, match="rev must be >= 0"):
            await client.config_diff(rev=-1)
        client._post.assert_not_called()

    async def test_config_history(self, client):
        await client.config_history()
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["system", "commit"]}
        )

    async def test_config_history_parses_data(self, client):
        client._post.return_value = {
            "success": True,
            "data": " 0  2026-05-04 02:02:02  by root  via cli\n",
            "error": None,
        }
        assert await client.config_history() == [
            {
                "revision": 0,
                "timestamp": "2026-05-04 02:02:02",
                "user": "root",
                "via": "cli",
                "comment": None,
            }
        ]

    async def test_config_history_non_string_data_returns_empty(self, client):
        # An error response (data is None, or any non-string) yields [].
        client._post.return_value = {"success": False, "data": None, "error": "boom"}
        assert await client.config_history() == []

    async def test_show(self, client):
        await client.show(["interfaces"])
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["interfaces"]}
        )

    async def test_traceroute(self, client):
        await client.traceroute("8.8.8.8")
        client._post.assert_called_once_with(
            "traceroute", {"op": "traceroute", "host": "8.8.8.8"}
        )

    async def test_traceroute_rejects_bad_host(self, client):
        with pytest.raises(ValueError, match="Invalid host"):
            await client.traceroute("8.8.8.8; rm -rf /")
        client._post.assert_not_called()

    async def test_interface_stats_all(self, client):
        await client.interface_stats()
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["interfaces"]}
        )

    async def test_interface_stats_one(self, client):
        await client.interface_stats(["ethernet", "eth0"])
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["interfaces", "ethernet", "eth0"]}
        )

    async def test_system_resources(self, client):
        client._post.return_value = {"success": True, "data": "x", "error": None}
        result = await client.system_resources()
        assert set(result) == {"cpu", "memory", "storage", "uptime"}
        assert result["cpu"] == {"success": True, "data": "x", "error": None}
        client._post.assert_has_calls(
            [
                call("show", {"op": "show", "path": ["system", "cpu"]}),
                call("show", {"op": "show", "path": ["system", "memory"]}),
                call("show", {"op": "show", "path": ["system", "storage"]}),
                call("show", {"op": "show", "path": ["system", "uptime"]}),
            ],
            any_order=True,
        )

    async def test_system_resources_partial_failure(self, client):
        def side_effect(endpoint, data):
            if data["path"] == ["system", "memory"]:
                raise RuntimeError("boom")
            return {"success": True, "data": "ok", "error": None}

        client._post.side_effect = side_effect
        result = await client.system_resources()
        assert result["cpu"] == {"success": True, "data": "ok", "error": None}
        assert result["memory"] == {
            "success": False,
            "data": None,
            "error": "RuntimeError: boom",
        }

    async def test_route_table_default(self, client):
        await client.route_table()
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["ip", "route"]}
        )

    async def test_route_table_ipv6(self, client):
        await client.route_table("ipv6")
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["ipv6", "route"]}
        )

    async def test_route_table_with_protocol(self, client):
        await client.route_table("ip", "bgp")
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["ip", "route", "bgp"]}
        )

    async def test_route_table_protocol_uses_default_family(self, client):
        await client.route_table(protocol="bgp")
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["ip", "route", "bgp"]}
        )

    async def test_route_table_rejects_bad_family(self, client):
        with pytest.raises(ValueError, match="Invalid route family"):
            await client.route_table("ipx")
        client._post.assert_not_called()

    async def test_route_table_rejects_bad_protocol(self, client):
        with pytest.raises(ValueError, match="Invalid route protocol"):
            await client.route_table("ip", "haxx")
        client._post.assert_not_called()

    async def test_firewall_stats(self, client):
        client._post.return_value = {"success": True, "data": "x", "error": None}
        result = await client.firewall_stats()
        assert set(result) == {"firewall", "nat_source", "nat_destination"}
        assert result["firewall"] == {"success": True, "data": "x", "error": None}
        client._post.assert_has_calls(
            [
                call("show", {"op": "show", "path": ["firewall"]}),
                call("show", {"op": "show", "path": ["nat", "source", "statistics"]}),
                call(
                    "show",
                    {"op": "show", "path": ["nat", "destination", "statistics"]},
                ),
            ],
            any_order=True,
        )

    async def test_firewall_stats_partial_failure(self, client):
        def side_effect(endpoint, data):
            if data["path"] == ["firewall"]:
                raise RuntimeError("boom")
            return {"success": True, "data": "ok", "error": None}

        client._post.side_effect = side_effect
        result = await client.firewall_stats()
        assert result["firewall"] == {
            "success": False,
            "data": None,
            "error": "RuntimeError: boom",
        }
        assert result["nat_source"] == {"success": True, "data": "ok", "error": None}

    async def test_bgp_summary(self, client):
        await client.bgp_summary()
        client._post.assert_called_once_with(
            "show", {"op": "show", "path": ["bgp", "summary"]}
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
        # The router replies only after download + install: long read timeout
        client._post.assert_called_once_with(
            "image",
            {"op": "add", "url": "https://downloads.vyos.io/latest.iso"},
            timeout=httpx.Timeout(30, read=1800),
        )

    async def test_image_add_read_timeout_hints_to_check_images(self, client):
        err = TimeoutError("VyOS API /image did not respond within 1800s")
        err.__cause__ = httpx.ReadTimeout("")
        client._post.side_effect = err
        with pytest.raises(TimeoutError, match="1800s.*system.*image"):
            await client.image_add("https://downloads.vyos.io/latest.iso")

    async def test_image_add_connect_timeout_has_no_install_hint(self, client):
        # The router never got the request, so nothing can be installing
        err = TimeoutError("VyOS API /image could not connect within 30s")
        err.__cause__ = httpx.ConnectTimeout("")
        client._post.side_effect = err
        with pytest.raises(TimeoutError) as exc_info:
            await client.image_add("https://downloads.vyos.io/latest.iso")
        assert exc_info.value is err

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

    async def test_custom_timeout_passed_through(self):
        client = make_client()
        mock_response = AsyncMock()
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = lambda: None
        timeout = httpx.Timeout(30, read=1800)

        with patch("vyos_mcp.client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            await client._post("image", {"op": "add"}, timeout=timeout)

            mock_cls.assert_called_once_with(verify=False, timeout=timeout)

    @pytest.mark.parametrize(
        ("timeout", "exc", "message"),
        [
            (30, httpx.ReadTimeout, "/image did not respond within 30s"),
            (
                httpx.Timeout(30, read=1800),
                httpx.ReadTimeout,
                "/image did not respond within 1800s",
            ),
            # A connect timeout reports the connect limit, not the read limit
            (
                httpx.Timeout(30, read=1800),
                httpx.ConnectTimeout,
                "/image could not connect within 30s",
            ),
            # An unlimited phase omits the duration instead of crashing
            (
                httpx.Timeout(30, read=None),
                httpx.ReadTimeout,
                "/image did not respond$",
            ),
        ],
    )
    async def test_timeout_raises_descriptive_error(self, timeout, exc, message):
        client = make_client()

        with patch("vyos_mcp.client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            # httpx timeout exceptions carry an empty message
            mock_http.post.side_effect = exc("")
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            with pytest.raises(TimeoutError, match=message) as exc_info:
                await client._post("image", {"op": "add"}, timeout=timeout)
            assert isinstance(exc_info.value.__cause__, exc)

    async def test_info_uses_get(self):
        from unittest.mock import MagicMock

        client = make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {"version": "1.4.0"}
        mock_response.raise_for_status = lambda: None

        with patch("vyos_mcp.client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            result = await client.info()

            mock_http.get.assert_called_once_with(f"{URL}/info")
            assert result == {"version": "1.4.0"}


class TestParseCommitHistory:
    """Verify parsing of `show system commit` output."""

    def test_basic_lines(self):
        raw = (
            " 0  2026-05-04 02:02:02  by root  via vyos-boot-config-loader\n"
            "10  2026-03-30 22:16:12  by vyos  via cli\n"
        )
        assert _parse_commit_history(raw) == [
            {
                "revision": 0,
                "timestamp": "2026-05-04 02:02:02",
                "user": "root",
                "via": "vyos-boot-config-loader",
                "comment": None,
            },
            {
                "revision": 10,
                "timestamp": "2026-03-30 22:16:12",
                "user": "vyos",
                "via": "cli",
                "comment": None,
            },
        ]

    def test_with_comment(self):
        raw = " 3  2026-04-21 01:48:42  by vyos  via cli  added firewall rule"
        assert _parse_commit_history(raw) == [
            {
                "revision": 3,
                "timestamp": "2026-04-21 01:48:42",
                "user": "vyos",
                "via": "cli",
                "comment": "added firewall rule",
            }
        ]

    def test_empty_and_garbage_skipped(self):
        assert _parse_commit_history("") == []
        assert _parse_commit_history("not a revision line\n\n") == []
