"""VyOS documentation client fetching RST docs from GitHub."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

REPO = "vyos/vyos-documentation"
BRANCH = "current"
DOCS_PREFIX = "docs/"
GITHUB_API = "https://api.github.com"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

DEFAULT_TTL = 3600  # 1 hour


@dataclass
class CacheEntry:
    data: object
    expires_at: float


@dataclass
class DocsClient:
    """Fetches and caches VyOS documentation from GitHub."""

    ttl: int = DEFAULT_TTL
    _tree_cache: CacheEntry | None = field(default=None, repr=False)
    _page_cache: dict[str, CacheEntry] = field(default_factory=dict, repr=False)

    def _is_valid(self, entry: CacheEntry | None) -> bool:
        return entry is not None and time.monotonic() < entry.expires_at

    async def get_tree(self) -> list[str]:
        """Get the list of all RST doc paths, cached."""
        if self._is_valid(self._tree_cache):
            return self._tree_cache.data

        url = f"{GITHUB_API}/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()

        paths = [
            item["path"]
            for item in resp.json()["tree"]
            if item["path"].startswith(DOCS_PREFIX) and item["path"].endswith(".rst")
        ]

        self._tree_cache = CacheEntry(
            data=paths,
            expires_at=time.monotonic() + self.ttl,
        )
        return paths

    async def read_page(self, path: str) -> str:
        """Fetch a single RST page by path, cached."""
        if self._is_valid(self._page_cache.get(path)):
            return self._page_cache[path].data

        # Raw content endpoint is simpler and doesn't need base64 decoding
        url = f"{RAW_BASE}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content = resp.text
        self._page_cache[path] = CacheEntry(
            data=content,
            expires_at=time.monotonic() + self.ttl,
        )
        return content

    async def search(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        """Search doc paths for a query string.

        Returns matching paths ranked by relevance. Splits query into
        terms and scores each path by how many terms match.
        """
        paths = await self.get_tree()
        terms = query.lower().split()

        scored: list[tuple[int, str]] = []
        for path in paths:
            # Score against the path without the docs/ prefix and .rst suffix
            searchable = path.removeprefix(DOCS_PREFIX).removesuffix(".rst")
            searchable_lower = searchable.lower()

            score = sum(1 for term in terms if term in searchable_lower)
            if score > 0:
                scored.append((score, path))

        scored.sort(key=lambda x: (-x[0], x[1]))

        return [
            {
                "path": path,
                "title": path.removeprefix(DOCS_PREFIX).removesuffix(".rst"),
            }
            for _, path in scored[:max_results]
        ]
