"""MCP server for VyOS router management."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from vyos_mcp.client import VyOSClient
from vyos_mcp.docs import DocsClient

mcp = FastMCP("mcp-server-vyos")

_docs_client = DocsClient()


def _get_client() -> VyOSClient:
    return VyOSClient()


@mcp.tool()
async def vyos_info() -> dict:
    """Get VyOS system info (no authentication required)."""
    client = _get_client()
    return await client.info()


@mcp.tool()
async def vyos_retrieve(path: list[str]) -> dict:
    """Read VyOS configuration at a given path.

    Args:
        path: Configuration path as list of strings, e.g. ["firewall", "group"]
    """
    client = _get_client()
    return await client.retrieve(path)


@mcp.tool()
async def vyos_return_values(path: list[str]) -> dict:
    """Get values of a multi-valued VyOS config node as a list.

    Use this instead of vyos_retrieve when a node has multiple
    values (e.g. addresses on an interface).

    Args:
        path: Configuration path, e.g.
            ["interfaces", "dummy", "dum0", "address"]
    """
    client = _get_client()
    return await client.return_values(path)


@mcp.tool()
async def vyos_exists(path: list[str]) -> dict:
    """Check if a VyOS configuration path exists.

    Returns true/false in the data field.

    Args:
        path: Configuration path to check,
            e.g. ["service", "https", "api"]
    """
    client = _get_client()
    return await client.exists(path)


@mcp.tool()
async def vyos_show(path: list[str]) -> dict:
    """Run a VyOS operational show command.

    Args:
        path: Command path as list of strings, e.g. ["interfaces", "ethernet"]
    """
    client = _get_client()
    return await client.show(path)


@mcp.tool()
async def vyos_configure(commands: list[dict]) -> dict:
    """Apply VyOS configuration with commit-confirm (auto-rollback after 5 min).

    This is the safe default — changes auto-revert unless confirmed with vyos_confirm.

    Args:
        commands: List of config operations, each with 'op'
            ('set'/'delete') and 'path' (list of strings).
            Example: [{"op": "set", "path": ["firewall",
            "group", "network-group", "MY_GROUP"]}]
    """
    client = _get_client()
    return await client.configure_confirm(commands)


@mcp.tool()
async def vyos_confirm() -> dict:
    """Confirm a pending commit-confirm, making changes permanent."""
    client = _get_client()
    return await client.confirm()


@mcp.tool()
async def vyos_save() -> dict:
    """Save running VyOS configuration to disk."""
    client = _get_client()
    return await client.save()


@mcp.tool()
async def vyos_generate(path: list[str]) -> dict:
    """Run a VyOS generate command (keys, certificates, etc.).

    Args:
        path: Command path, e.g. ["pki", "wireguard", "key-pair"]
    """
    client = _get_client()
    return await client.generate(path)


@mcp.tool()
async def vyos_reset(path: list[str]) -> dict:
    """Run a VyOS reset command.

    Args:
        path: Command path, e.g. ["ip", "bgp", "192.0.2.11"]
    """
    client = _get_client()
    return await client.reset(path)


@mcp.tool()
async def vyos_load(file: str) -> dict:
    """Load a VyOS configuration file.

    Args:
        file: Path to config file on the router,
            e.g. "/config/test.config"
    """
    client = _get_client()
    return await client.load(file)


@mcp.tool()
async def vyos_merge(file: str | None = None, string: str | None = None) -> dict:
    """Merge a configuration into the running config.

    Provide either a file path on the router or an inline
    config string (VyOS curly-brace format).

    Args:
        file: Path to config file on the router
        string: Inline config in VyOS format, e.g.
            'interfaces { ethernet eth1 { description "test" } }'
    """
    client = _get_client()
    return await client.merge(file=file, string=string)


@mcp.tool()
async def vyos_reboot() -> dict:
    """Reboot the VyOS router immediately.

    WARNING: This will reboot the router. All active sessions
    and traffic will be interrupted.
    """
    client = _get_client()
    return await client.reboot()


@mcp.tool()
async def vyos_poweroff() -> dict:
    """Power off the VyOS router immediately.

    WARNING: This will shut down the router. The router will
    need physical or out-of-band access to power back on.
    """
    client = _get_client()
    return await client.poweroff()


@mcp.tool()
async def vyos_image_add(url: str) -> dict:
    """Add a VyOS system image from a URL.

    Downloads and installs a new system image. This does not
    reboot — the new image will be used on next boot.

    Args:
        url: URL to the VyOS ISO image
    """
    client = _get_client()
    return await client.image_add(url)


@mcp.tool()
async def vyos_image_delete(name: str) -> dict:
    """Delete a VyOS system image.

    WARNING: Cannot delete the currently running image.

    Args:
        name: Image name, e.g. "1.4-rolling-202102280559"
    """
    client = _get_client()
    return await client.image_delete(name)


@mcp.tool()
async def vyos_docs_search(query: str, max_results: int = 10) -> list[dict]:
    """Search VyOS documentation for a topic.

    Returns matching doc pages ranked by relevance. Use
    vyos_docs_read to fetch the full content of a result.

    Args:
        query: Search terms, e.g. "firewall group" or "nat hairpin"
        max_results: Maximum number of results to return (default 10)
    """
    return await _docs_client.search(query, max_results)


@mcp.tool()
async def vyos_docs_read(path: str) -> str:
    """Read a VyOS documentation page.

    Fetches the raw RST content from the VyOS docs repository.
    Use vyos_docs_search to find the path for a topic.

    Args:
        path: Doc path, e.g. "docs/configuration/firewall/groups.rst"
    """
    return await _docs_client.read_page(path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
