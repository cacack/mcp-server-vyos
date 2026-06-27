"""VyOS HTTPS REST API client."""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

# Hosts reach the router's traceroute utility as a command argument; restrict
# to characters valid in hostnames and IP addresses (incl. IPv6 colons).
_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _validate_host(host: str) -> str:
    """Return host if it is a plausible hostname/IP, else raise ValueError."""
    if not host or not _HOST_RE.match(host):
        raise ValueError(f"Invalid host: {host!r}")
    return host


_ROUTE_FAMILIES = frozenset({"ip", "ipv6"})
_ROUTE_PROTOCOLS = frozenset(
    {"bgp", "ospf", "ospfv3", "static", "connected", "kernel", "rip", "isis"}
)


def _validate_route_family(family: str) -> str:
    """Return family if it is 'ip' or 'ipv6', else raise ValueError."""
    if family not in _ROUTE_FAMILIES:
        raise ValueError(f"Invalid route family: {family!r} (expected 'ip' or 'ipv6')")
    return family


def _validate_route_protocol(protocol: str) -> str:
    """Return protocol if it is a known routing source, else raise ValueError."""
    if protocol not in _ROUTE_PROTOCOLS:
        raise ValueError(
            f"Invalid route protocol: {protocol!r} "
            f"(expected one of {', '.join(sorted(_ROUTE_PROTOCOLS))})"
        )
    return protocol


_COMMIT_RE = re.compile(
    r"^\s*(\d+)\s+"  # revision number
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"  # timestamp
    r"by\s+(\S+)\s+"  # user
    r"via\s+(\S+)"  # via
    r"(?:\s+(.*\S))?\s*$"  # optional comment
)


def _parse_commit_history(raw: str) -> list[dict]:
    """Parse `show system commit` output into structured revisions.

    Each line looks like:
        ` 0  2026-05-04 02:02:02  by root  via cli  some comment`

    Returns a list of dicts with revision (int), timestamp, user, via,
    and comment (None when absent). Unparseable lines are skipped.
    """
    revisions = []
    for line in raw.splitlines():
        match = _COMMIT_RE.match(line)
        if not match:
            continue
        rev, timestamp, user, via, comment = match.groups()
        revisions.append(
            {
                "revision": int(rev),
                "timestamp": timestamp,
                "user": user,
                "via": via,
                "comment": comment,
            }
        )
    return revisions


class VyOSClient:
    """Client for the VyOS HTTPS REST API.

    All endpoints use form-encoded POST with `data` (JSON string) and `key` fields.
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool = False,
    ) -> None:
        self.url = (url or os.environ.get("VYOS_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("VYOS_API_KEY", "")
        self.verify_ssl = verify_ssl

        if not self.url:
            raise ValueError("VyOS URL required (pass url= or set VYOS_URL)")
        if not self.api_key:
            raise ValueError("API key required (pass api_key= or set VYOS_API_KEY)")

    async def _post(self, endpoint: str, data: dict | list) -> dict:
        """Send a form-encoded POST request to the VyOS API."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=30) as client:
            response = await client.post(
                f"{self.url}/{endpoint}",
                data={
                    "data": json.dumps(data),
                    "key": self.api_key,
                },
            )
            response.raise_for_status()
            return response.json()

    async def retrieve(self, path: list[str]) -> dict:
        """Read configuration at a given path."""
        return await self._post("retrieve", {"op": "showConfig", "path": path})

    async def return_values(self, path: list[str]) -> dict:
        """Get values of a multi-valued config node."""
        return await self._post("retrieve", {"op": "returnValues", "path": path})

    async def exists(self, path: list[str]) -> dict:
        """Check if a configuration path exists."""
        return await self._post("retrieve", {"op": "exists", "path": path})

    async def configure(self, commands: list[dict]) -> dict:
        """Apply configuration commands.

        Each command is a dict with 'op' ('set' or 'delete')
        and 'path' (list of strings).
        """
        return await self._post("configure", commands)

    async def configure_confirm(
        self, commands: list[dict], confirm_minutes: int = 5
    ) -> dict:
        """Apply configuration with commit-confirm (auto-rollback safety).

        Adds confirm_time to the first command, triggering commit-confirm
        on the whole batch.
        """
        payload = [
            {**commands[0], "confirm_time": confirm_minutes},
            *commands[1:],
        ]
        return await self._post("configure", payload)

    async def validate(self, commands: list[dict]) -> dict:
        """Validate configuration commands without persisting.

        Uses commit-confirm with a 1-minute rollback window and does not
        confirm, so the router automatically reverts.  This is not a true
        dry-run — the configuration is temporarily applied.
        """
        return await self.configure_confirm(commands, confirm_minutes=1)

    async def confirm(self) -> dict:
        """Confirm a pending commit-confirm."""
        return await self._post("configure", {"op": "confirm", "path": []})

    async def save(self, file: str | None = None) -> dict:
        """Save running config to disk."""
        payload: dict = {"op": "save"}
        if file:
            payload["file"] = file
        return await self._post("config-file", payload)

    async def load(self, file: str) -> dict:
        """Load a configuration file."""
        return await self._post("config-file", {"op": "load", "file": file})

    async def merge(self, file: str | None = None, string: str | None = None) -> dict:
        """Merge a configuration file or string into running config."""
        payload: dict = {"op": "merge"}
        if file:
            payload["file"] = file
        if string:
            payload["string"] = string
        return await self._post("config-file", payload)

    async def config_diff(self, rev: int | None = None) -> dict:
        """Show configuration differences.

        Compares running config against saved config, or against a
        specific revision number.
        """
        path = ["configuration", "compare"]
        if rev is not None:
            path.append(str(rev))
        return await self.show(path)

    async def config_history(self) -> list[dict]:
        """List configuration commit revisions, newest first.

        Runs `show system commit` and parses the result into structured
        revisions (revision, timestamp, user, via, comment). An empty
        list means no revisions matched the expected format (no history,
        or the router returned a non-text/error response).
        """
        result = await self.show(["system", "commit"])
        data = result.get("data")
        return _parse_commit_history(data if isinstance(data, str) else "")

    async def show(self, path: list[str]) -> dict:
        """Run an operational show command."""
        return await self._post("show", {"op": "show", "path": path})

    async def traceroute(self, host: str) -> dict:
        """Traceroute to a host from the router.

        Uses the dedicated /traceroute endpoint. The returned API response
        carries an mtr report (per-hop loss and latency) in its data field.
        Raises ValueError if host is not a plausible hostname or IP address.
        """
        payload = {"op": "traceroute", "host": _validate_host(host)}
        return await self._post("traceroute", payload)

    async def interface_stats(self, interface: list[str] | None = None) -> dict:
        """Show interface statistics (counters, errors, link state).

        With no argument, returns the summary table for all interfaces.
        Pass an interface spec as path elements (e.g. ["ethernet", "eth0"])
        to get detailed RX/TX byte/packet/error counters for one interface.
        """
        return await self.show(["interfaces"] + (interface or []))

    async def system_resources(self) -> dict:
        """Get CPU, memory, storage, and uptime in one call.

        Runs the four `show system ...` operational commands concurrently and
        returns their responses keyed by resource. Each value is the full show
        response dict (raw text in its data field). If a single command fails,
        its value is an error dict instead, so a partial failure still returns
        the resources that succeeded.
        """
        resources = ["cpu", "memory", "storage", "uptime"]
        results = await asyncio.gather(
            *(self.show(["system", name]) for name in resources),
            return_exceptions=True,
        )
        return {
            name: (
                {
                    "success": False,
                    "data": None,
                    "error": f"{type(result).__name__}: {result}",
                }
                if isinstance(result, Exception)
                else result
            )
            for name, result in zip(resources, results)
        }

    async def route_table(
        self, family: str = "ip", protocol: str | None = None
    ) -> dict:
        """Show the routing table (RIB).

        family is 'ip' (IPv4) or 'ipv6'. An optional protocol filters to a
        single source (bgp, ospf, static, connected, ...). Maps to
        `show ip route [protocol]` / `show ipv6 route [protocol]`. Raises
        ValueError on an unknown family or protocol.
        """
        path = [_validate_route_family(family), "route"]
        if protocol is not None:
            path.append(_validate_route_protocol(protocol))
        return await self.show(path)

    async def firewall_stats(self) -> dict:
        """Show firewall and NAT rule statistics in one call.

        Runs `show firewall`, `show nat source statistics`, and `show nat
        destination statistics` concurrently, keyed by 'firewall',
        'nat_source', and 'nat_destination'. If a single command fails its
        value is an error dict, so a partial failure still returns the
        parts that succeeded.
        """
        commands = {
            "firewall": ["firewall"],
            "nat_source": ["nat", "source", "statistics"],
            "nat_destination": ["nat", "destination", "statistics"],
        }
        results = await asyncio.gather(
            *(self.show(path) for path in commands.values()),
            return_exceptions=True,
        )
        return {
            name: (
                {
                    "success": False,
                    "data": None,
                    "error": f"{type(result).__name__}: {result}",
                }
                if isinstance(result, Exception)
                else result
            )
            for name, result in zip(commands, results)
        }

    async def bgp_summary(self) -> dict:
        """Show the BGP neighbor summary (`show bgp summary`).

        FRR's unified summary across address families: neighbor state,
        uptime, and prefixes received per peer.
        """
        return await self.show(["bgp", "summary"])

    async def generate(self, path: list[str]) -> dict:
        """Run a generate command."""
        return await self._post("generate", {"op": "generate", "path": path})

    async def reset(self, path: list[str]) -> dict:
        """Run a reset command."""
        return await self._post("reset", {"op": "reset", "path": path})

    async def reboot(self) -> dict:
        """Reboot the router."""
        return await self._post("reboot", {"op": "reboot", "path": ["now"]})

    async def poweroff(self) -> dict:
        """Power off the router."""
        return await self._post("poweroff", {"op": "poweroff", "path": ["now"]})

    async def image_add(self, url: str) -> dict:
        """Add a system image from a URL."""
        return await self._post("image", {"op": "add", "url": url})

    async def image_delete(self, name: str) -> dict:
        """Delete a system image."""
        return await self._post("image", {"op": "delete", "name": name})

    async def info(self) -> dict:
        """Get system info (no auth required)."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=30) as client:
            response = await client.get(f"{self.url}/info")
            response.raise_for_status()
            return response.json()
