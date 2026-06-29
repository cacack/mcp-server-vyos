"""Tests for VyOS documentation client."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vyos_mcp.docs import CacheEntry, DocsClient


class TestCacheEntry:
    def test_valid_entry(self):
        client = DocsClient()
        entry = CacheEntry(data="test", expires_at=time.monotonic() + 100)
        assert client._is_valid(entry) is True

    def test_expired_entry(self):
        client = DocsClient()
        entry = CacheEntry(data="test", expires_at=time.monotonic() - 1)
        assert client._is_valid(entry) is False

    def test_none_entry(self):
        client = DocsClient()
        assert client._is_valid(None) is False


# Representative page content keyed by doc path. "groups" appears only in
# the firewall groups page and "hairpin" only in nat44, so content-based
# ranking is observable in tests.
PAGE_CONTENT = {
    "docs/configuration/firewall/groups.rst": (
        "Firewall Groups\n===============\n\nFirewall groups let you define "
        "reusable network, port, and interface groups for firewall rules."
    ),
    "docs/configuration/firewall/index.rst": (
        "Firewall\n========\n\nConfigure stateful firewall rules and policies."
    ),
    "docs/configuration/firewall/ipv4.rst": (
        "IPv4 Firewall\n=============\n\nIPv4 firewall ruleset configuration."
    ),
    "docs/configuration/firewall/ipv6.rst": (
        "IPv6 Firewall\n=============\n\nIPv6 firewall ruleset configuration."
    ),
    "docs/configuration/firewall/zone.rst": (
        "Zone-based Firewall\n===================\n\nZone policy firewall config."
    ),
    "docs/configuration/nat/index.rst": (
        "NAT\n===\n\nNetwork address translation overview and configuration."
    ),
    "docs/configuration/nat/nat44.rst": (
        "NAT44\n=====\n\nSource and destination NAT for IPv4. Supports hairpin "
        "NAT so internal hosts can reach internal services via the public address."
    ),
    "docs/configuration/nat/nat66.rst": (
        "NAT66\n=====\n\nIPv6-to-IPv6 network prefix translation configuration."
    ),
    "docs/configuration/interfaces/bonding.rst": (
        "Bonding\n=======\n\nLink aggregation / bonding interface configuration."
    ),
    "docs/configuration/interfaces/ethernet.rst": (
        "Ethernet\n========\n\nEthernet interface configuration."
    ),
    "docs/automation/vyos-api.rst": (
        "VyOS API\n========\n\nThe HTTP API for automation and config management."
    ),
    "docs/configexamples/firewall.rst": (
        "Firewall Example\n================\n\nA worked firewall config example."
    ),
}


class TestSearch:
    @pytest.fixture
    def client_with_tree(self):
        """DocsClient with pre-populated tree and page caches (no network)."""
        client = DocsClient()
        now = time.monotonic()
        client._tree_cache = CacheEntry(
            data=list(PAGE_CONTENT),
            expires_at=now + 3600,
        )
        client._page_cache = {
            path: CacheEntry(data=content, expires_at=now + 3600)
            for path, content in PAGE_CONTENT.items()
        }
        return client

    async def test_single_term(self, client_with_tree):
        results = await client_with_tree.search("firewall")
        assert len(results) > 0
        # New contract: each result matches the query in its path or its
        # body (not necessarily the title), and carries the uniform shape.
        for r in results:
            assert {"path", "title", "snippet"} <= r.keys()
            assert "firewall" in r["path"].lower() or (
                r["snippet"] is not None and "firewall" in r["snippet"].lower()
            )

    async def test_multi_term_ranking(self, client_with_tree):
        results = await client_with_tree.search("firewall groups")
        # "firewall/groups" should rank first (matches both terms)
        assert results[0]["path"] == "docs/configuration/firewall/groups.rst"

    async def test_no_matches(self, client_with_tree):
        results = await client_with_tree.search("nonexistent_topic_xyz")
        assert results == []

    async def test_empty_query(self, client_with_tree):
        assert await client_with_tree.search("") == []
        assert await client_with_tree.search("   ") == []

    async def test_max_results(self, client_with_tree):
        results = await client_with_tree.search("configuration", max_results=3)
        assert len(results) <= 3

    async def test_result_format(self, client_with_tree):
        results = await client_with_tree.search("nat")
        assert len(results) > 0
        for r in results:
            assert "path" in r
            assert "title" in r
            assert r["path"].startswith("docs/")
            assert r["path"].endswith(".rst")
            assert not r["title"].startswith("docs/")
            assert not r["title"].endswith(".rst")

    async def test_snippet_included(self, client_with_tree):
        results = await client_with_tree.search("firewall groups")
        groups = next(r for r in results if r["path"].endswith("firewall/groups.rst"))
        assert "snippet" in groups
        assert "groups" in groups["snippet"].lower()

    async def test_content_reranks(self, client_with_tree):
        # Both nat pages match "nat" by path, but only nat44 mentions
        # "hairpin" in its body, so content scoring lifts it to the top.
        results = await client_with_tree.search("nat hairpin")
        assert results[0]["path"] == "docs/configuration/nat/nat44.rst"

    async def test_no_match_skips_fetch(self, client_with_tree):
        # Zero path matches must return [] without fetching any page.
        with patch.object(
            client_with_tree, "_fetch_page", side_effect=AssertionError("fetched")
        ):
            results = await client_with_tree.search("nonexistent_topic_xyz")
        assert results == []

    async def test_fetch_failure_graceful(self, client_with_tree):
        async def flaky(client, path):
            if path.endswith("firewall/groups.rst"):
                raise RuntimeError("boom")
            return PAGE_CONTENT[path]

        with patch.object(client_with_tree, "_fetch_page", new=flaky):
            results = await client_with_tree.search("firewall")

        by_path = {r["path"]: r for r in results}
        # The failed page is still present as a path-only match, snippet None.
        groups = by_path.get("docs/configuration/firewall/groups.rst")
        assert groups is not None
        assert groups["snippet"] is None
        # A page that fetched cleanly still gets a snippet.
        index = by_path.get("docs/configuration/firewall/index.rst")
        assert index is not None and index["snippet"] is not None


class TestGetTree:
    """Test get_tree fetches and caches the file list from GitHub."""

    async def test_fetches_from_github(self):
        client = DocsClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tree": [
                {"path": "docs/configuration/firewall/groups.rst", "type": "blob"},
                {"path": "docs/configuration/nat/index.rst", "type": "blob"},
                {"path": "docs/Makefile", "type": "blob"},
                {"path": "src/something.py", "type": "blob"},
            ]
        }
        mock_response.raise_for_status = lambda: None

        with patch("vyos_mcp.docs.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            paths = await client.get_tree()

        assert paths == [
            "docs/configuration/firewall/groups.rst",
            "docs/configuration/nat/index.rst",
        ]

    async def test_uses_cache_on_second_call(self):
        client = DocsClient()
        client._tree_cache = CacheEntry(
            data=["docs/cached.rst"],
            expires_at=time.monotonic() + 3600,
        )

        with patch("vyos_mcp.docs.httpx.AsyncClient") as mock_cls:
            paths = await client.get_tree()
            mock_cls.assert_not_called()

        assert paths == ["docs/cached.rst"]


class TestReadPage:
    """Test read_page fetches and caches doc content."""

    async def test_fetches_page(self):
        client = DocsClient()
        mock_response = MagicMock()
        mock_response.text = "Firewall Groups\n===============\n\nContent."
        mock_response.raise_for_status = lambda: None

        with patch("vyos_mcp.docs.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_http
            mock_cls.return_value = mock_ctx

            content = await client.read_page("docs/configuration/firewall/groups.rst")

        assert content == "Firewall Groups\n===============\n\nContent."
        assert "docs/configuration/firewall/groups.rst" in client._page_cache

    async def test_uses_cache_on_second_call(self):
        client = DocsClient()
        path = "docs/cached.rst"
        client._page_cache[path] = CacheEntry(
            data="cached content",
            expires_at=time.monotonic() + 3600,
        )

        with patch("vyos_mcp.docs.httpx.AsyncClient") as mock_cls:
            content = await client.read_page(path)
            mock_cls.assert_not_called()

        assert content == "cached content"
