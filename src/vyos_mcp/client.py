"""VyOS HTTPS REST API client."""

from __future__ import annotations

import asyncio
import difflib
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


_DEFAULT_TIMEOUT = 30
# /image add is synchronous: the router downloads and installs a full ISO
# before replying, which takes minutes. A deliberate generous upper bound.
_IMAGE_ADD_READ_TIMEOUT = 1800

# Which httpx.Timeout field governs each timeout phase.
_TIMEOUT_PHASES = {
    httpx.ConnectTimeout: ("connect", "could not connect"),
    httpx.ReadTimeout: ("read", "did not respond"),
    httpx.WriteTimeout: ("write", "could not send the request"),
    httpx.PoolTimeout: ("pool", "had no free connection"),
}


def _timeout_message(
    endpoint: str, timeout: float | httpx.Timeout, exc: httpx.TimeoutException
) -> str:
    """Describe which phase of a request to `endpoint` timed out, and after how long.

    httpx's own timeout exceptions carry an empty message.
    """
    field, what = _TIMEOUT_PHASES.get(type(exc), ("read", "timed out"))
    seconds = getattr(timeout, field) if isinstance(timeout, httpx.Timeout) else timeout
    limit = f" within {seconds:g}s" if seconds is not None else ""
    return f"VyOS API /{endpoint} {what}{limit}"


# Router response markers (matched case-insensitively). An armed commit-confirm
# reports "Initialized commit-confirm; N minutes to confirm before reload".
_ARMED_MARKER = "initialized commit-confirm"
# Changes to `service https` are committed after the router replies, so whether
# commit-confirm armed cannot be observed (VyOS 1.4 commits them permanently).
_BACKGROUND_COMMIT_MARKER = "commit will be called in the background"
# Returned (HTTP 400) when a commit-confirm is already pending. The router has
# already committed the new changes by then, with no rollback timer of their own.
_CONFIRM_PENDING_MARKER = "another confirm is pending"
# `show system commit file N` for a revision that doesn't exist returns
# `success: true` with a Python traceback in `data` ending in this message.
_REVISION_MISSING_MARKER = "revision not available"


def _check_commit_confirm_armed(result: dict) -> dict:
    """Return result if commit-confirm armed; otherwise warn or raise.

    A background (`service https`) commit gets a `warning` key, since arming
    can't be verified. Any other success without the armed marker raises.
    """
    if not result.get("success"):
        return result
    data = str(result.get("data") or "").lower()
    if _ARMED_MARKER in data:
        return result
    if _BACKGROUND_COMMIT_MARKER in data:
        return {
            **result,
            "warning": (
                "Auto-rollback could NOT be verified: the router commits "
                "`service https` changes in the background. Current VyOS arms "
                "commit-confirm for them; older versions (e.g. 1.4) commit "
                "permanently. Verify API access, then vyos_confirm."
            ),
        }
    raise RuntimeError(
        "Changes were committed PERMANENTLY with no auto-rollback armed: the "
        "router did not acknowledge commit-confirm (this VyOS version may not "
        "support commit-confirm via the API). Review the running config and "
        f"revert manually if needed. Router response: {result.get('data')!r}"
    )


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

    async def _post(
        self,
        endpoint: str,
        data: dict | list,
        timeout: float | httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> dict:
        """Send a form-encoded POST request to the VyOS API.

        Raises TimeoutError describing the endpoint and the timeout phase; its
        __cause__ is the original httpx exception.
        """
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=timeout) as client:
            try:
                response = await client.post(
                    f"{self.url}/{endpoint}",
                    data={
                        "data": json.dumps(data),
                        "key": self.api_key,
                    },
                )
            except httpx.TimeoutException as e:
                raise TimeoutError(_timeout_message(endpoint, timeout, e)) from e
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

        `confirm_time` must be top-level alongside `commands`; the router
        silently ignores it on individual commands and commits permanently.

        Raises ValueError for an empty `commands`, and RuntimeError when the
        router committed without arming a rollback for these changes: either
        it doesn't support API commit-confirm, or one is already pending.
        `service https` changes return with a `warning` (see
        `_check_commit_confirm_armed`).
        """
        if not commands:
            raise ValueError("commands must be a non-empty list")
        try:
            result = await self._post(
                "configure", {"commands": commands, "confirm_time": confirm_minutes}
            )
        except httpx.HTTPStatusError as e:
            if _CONFIRM_PENDING_MARKER in e.response.text.lower():
                raise RuntimeError(
                    "Changes were COMMITTED but have no auto-rollback of their "
                    "own: an earlier commit-confirm is still pending. Its timer "
                    "reverts to the pre-pending config if it expires; "
                    "vyos_confirm makes BOTH permanent. Review the running "
                    "config before confirming."
                ) from e
            raise
        return _check_commit_confirm_armed(result)

    async def validate(self, commands: list[dict]) -> dict:
        """Validate configuration commands without persisting.

        Uses commit-confirm with a 1-minute rollback window and does not
        confirm, so the router automatically reverts.  This is not a true
        dry-run — the configuration is temporarily applied.  Raises like
        `configure_confirm` when the changes were committed unprotected.
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

    async def _commit_file(self, rev: int) -> str | None:
        """Fetch the config.boot text archived for commit revision `rev`.

        Returns None when the router reports the revision doesn't exist
        (`success: true` with a traceback in `data`; see
        `_REVISION_MISSING_MARKER`). Raises ValueError carrying the
        router's reason for any other failure, including an HTTP error.
        """
        try:
            result = await self.show(["system", "commit", "file", str(rev)])
        except httpx.HTTPStatusError as e:
            raise ValueError(
                f"Could not fetch config revision {rev}: {e.response.text.strip()}"
            ) from e
        data = result.get("data")
        if not result.get("success") or not isinstance(data, str):
            raise ValueError(
                f"Could not fetch config revision {rev}: {result.get('error')!r}"
            )
        if data.startswith("Traceback"):
            if _REVISION_MISSING_MARKER in data:
                return None
            reason = data.strip().splitlines()[-1]
            raise ValueError(f"Could not fetch config revision {rev}: {reason}")
        return data

    async def config_diff(self, rev: int | None = None) -> dict:
        """Show the changes introduced by commit revision `rev`.

        Diffs revision rev+1 against revision rev (None or 0 = most recent
        commit) client-side: the router's own `show system commit diff`
        returns empty output over the API, and the API exposes no
        running-vs-saved comparison. Raises ValueError for a negative or
        missing revision, for the oldest retained one (nothing to compare
        with), or when the router fails to return a revision.
        """
        if rev is None:
            rev = 0
        if rev < 0:
            raise ValueError(f"rev must be >= 0, got {rev}")
        new = await self._commit_file(rev)
        if new is None:
            raise ValueError(f"Config revision {rev} is not available")
        old = await self._commit_file(rev + 1)
        if old is None:
            raise ValueError(
                f"Config revision {rev} is the oldest retained revision; "
                "there is no earlier revision to compare it with"
            )
        # Normalize the trailing newline so a final line lacking one can't
        # run into the next diff line.
        diff = "".join(
            difflib.unified_diff(
                (old.rstrip("\n") + "\n").splitlines(keepends=True),
                (new.rstrip("\n") + "\n").splitlines(keepends=True),
                fromfile=f"revision {rev + 1}",
                tofile=f"revision {rev}",
            )
        )
        if not diff:
            diff = f"No changes between revisions {rev + 1} and {rev}"
        return {"success": True, "data": diff, "error": None}

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
        """Add a system image from a URL.

        The router downloads and installs the image before responding, so
        this waits up to `_IMAGE_ADD_READ_TIMEOUT` for the reply.
        """
        try:
            return await self._post(
                "image",
                {"op": "add", "url": url},
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT, read=_IMAGE_ADD_READ_TIMEOUT),
            )
        except TimeoutError as e:
            # Only a read timeout means the router got the request and may
            # still be working; connect/write timeouts never reached it.
            if not isinstance(e.__cause__, httpx.ReadTimeout):
                raise
            raise TimeoutError(
                f"{e}. The router may still be downloading or installing the "
                'image: check vyos_show(["system", "image"]) before retrying, '
                "since a retry starts a second download."
            ) from e

    async def image_delete(self, name: str) -> dict:
        """Delete a system image."""
        return await self._post("image", {"op": "delete", "name": name})

    async def info(self) -> dict:
        """Get system info (no auth required)."""
        async with httpx.AsyncClient(
            verify=self.verify_ssl, timeout=_DEFAULT_TIMEOUT
        ) as client:
            response = await client.get(f"{self.url}/info")
            response.raise_for_status()
            return response.json()
