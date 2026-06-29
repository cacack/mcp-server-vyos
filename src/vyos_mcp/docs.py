"""VyOS documentation client fetching RST docs from GitHub."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

REPO = "vyos/vyos-documentation"
BRANCH = "current"
DOCS_PREFIX = "docs/"
GITHUB_API = "https://api.github.com"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

DEFAULT_TTL = 3600  # 1 hour
DEFAULT_FETCH_LIMIT = 30  # max candidate pages fetched (and so results) per search
SNIPPET_RADIUS = 80  # chars of context on each side of the matched term
MAX_QUERY_TERMS = 20  # cap query terms to bound scoring work on huge queries


@dataclass
class CacheEntry:
    data: object
    expires_at: float


def _make_snippet(content: str, content_lower: str, terms: list[str]) -> str | None:
    """Return a one-line context window around the first matching term.

    `content_lower` must be `content.lower()` (passed in so the caller's
    existing copy is reused rather than re-lowered). Finds the earliest
    case-insensitive term occurrence and returns the match plus
    ±SNIPPET_RADIUS characters of surrounding context, whitespace
    collapsed, with `…` where the content was truncated. Returns None when
    no term occurs in the content.

    Indices from `content_lower` are applied to `content` directly, which
    assumes lowering preserves length — true for the ASCII/English VyOS
    docs this serves.
    """
    best_pos = -1
    best_term = ""
    for term in terms:
        pos = content_lower.find(term)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos, best_term = pos, term
    if best_pos < 0:
        return None

    start = max(0, best_pos - SNIPPET_RADIUS)
    end = best_pos + len(best_term) + SNIPPET_RADIUS
    window = " ".join(content[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{window}{suffix}"


@dataclass
class DocsClient:
    """Fetches and caches VyOS documentation from GitHub."""

    ttl: int = DEFAULT_TTL
    # Caps both the per-search page fetches and, since only fetched pages can
    # rank, the maximum number of results returned.
    fetch_limit: int = DEFAULT_FETCH_LIMIT
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
        async with httpx.AsyncClient() as client:
            return await self._fetch_page(client, path)

    async def _fetch_page(self, client: httpx.AsyncClient, path: str) -> str:
        """Fetch one page via the given client, honoring/populating the cache.

        Takes a shared client so concurrent candidate fetches in `search`
        reuse a single connection pool instead of one per request.
        """
        if self._is_valid(self._page_cache.get(path)):
            return self._page_cache[path].data

        # Raw content endpoint is simpler and doesn't need base64 decoding
        url = f"{RAW_BASE}/{path}"
        resp = await client.get(url)
        resp.raise_for_status()

        content = resp.text
        self._page_cache[path] = CacheEntry(
            data=content,
            expires_at=time.monotonic() + self.ttl,
        )
        return content

    async def search(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, str | None]]:
        """Search VyOS docs by path and page content.

        Two-phase, cost-bounded full-text search:
        1. Score every doc path by how many query terms it contains
           (cheap, no network), and take the top `self.fetch_limit`
           candidates.
        2. Fetch those candidates' content concurrently through one shared
           HTTP client (reusing the TTL page cache) and re-rank by how many
           distinct terms match across path and content, then by content
           term frequency.

        Every result has `path`, `title`, and `snippet` keys; `snippet` is
        a context window when the query matches the page body, otherwise
        None (path-only match or a failed fetch).

        Bounds: at most `self.fetch_limit` pages are fetched, so no more
        than that many results are returned even if `max_results` is larger;
        queries are also truncated to MAX_QUERY_TERMS terms. Pages whose
        path matches no query term are never fetched, so a topic appearing
        only in the body of an otherwise-unrelated page may be missed — the
        tradeoff that keeps a cold search to a bounded number of fetches.
        """
        terms = query.lower().split()[:MAX_QUERY_TERMS]
        if not terms:
            return []

        paths = await self.get_tree()

        # Phase 1: cheap path scoring picks the fetch candidates.
        path_scored: list[tuple[int, str]] = []
        for path in paths:
            searchable = path.removeprefix(DOCS_PREFIX).removesuffix(".rst").lower()
            score = sum(1 for term in terms if term in searchable)
            if score > 0:
                path_scored.append((score, path))

        if not path_scored:
            return []

        path_scored.sort(key=lambda x: (-x[0], x[1]))
        candidates = [path for _, path in path_scored[: self.fetch_limit]]

        # Phase 2: fetch candidate content concurrently (shared client) and
        # re-rank by it.
        async with httpx.AsyncClient() as client:
            contents = await asyncio.gather(
                *(self._fetch_page(client, path) for path in candidates),
                return_exceptions=True,
            )

        ranked: list[tuple[int, int, str, str | None]] = []
        for path, content in zip(candidates, contents):
            path_lower = path.removeprefix(DOCS_PREFIX).removesuffix(".rst").lower()
            # gather(return_exceptions=True) also surfaces BaseException
            # subclasses such as CancelledError, so guard on BaseException.
            if isinstance(content, BaseException):
                # Fetch failed — fall back to a path-only match, no snippet.
                content_lower = ""
                snippet = None
            else:
                content_lower = content.lower()
                snippet = _make_snippet(content, content_lower, terms)

            matched = sum(
                1 for term in terms if term in path_lower or term in content_lower
            )
            frequency = sum(content_lower.count(term) for term in terms)
            ranked.append((matched, frequency, path, snippet))

        ranked.sort(key=lambda r: (-r[0], -r[1], r[2]))

        return [
            {
                "path": path,
                "title": path.removeprefix(DOCS_PREFIX).removesuffix(".rst"),
                "snippet": snippet,
            }
            for _matched, _freq, path, snippet in ranked[:max_results]
        ]
