# CLAUDE.md

## Project Overview

Python MCP server wrapping the VyOS HTTPS REST API. Exposes VyOS router management (config, operational commands, system management) and VyOS documentation as MCP tools.

## Commands

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
python -m vyos_mcp  # run server (stdio transport)
```

## Architecture

- `src/vyos_mcp/server.py` — MCP server, tool registration (FastMCP)
- `src/vyos_mcp/client.py` — VyOS REST API client (auth, TLS, form-encoded)
- `src/vyos_mcp/docs.py` — VyOS docs client (GitHub API, TTL cache)

## Key Design Decisions

- `commit-confirm` is the default for config changes (auto-rollback safety)
- VyOS API uses form-encoded POST with `data` (JSON) and `key` fields, NOT JSON body
- Self-signed TLS certs common on VyOS — skip verification by default
- API key via `VYOS_API_KEY`, router URL via `VYOS_URL` env vars
- Docs fetched live from `vyos/vyos-documentation` GitHub repo (branch: `current`)

## VyOS API Quirks (validated against real router)

- All POST endpoints use `application/x-www-form-urlencoded` with `data` and `key` fields
- Configure operations are slow (10-20s) — client uses 30s timeout
- `confirm` requires `{"op": "confirm", "path": []}` — path field is mandatory, even empty
- `commit-confirm` uses `confirm_time` as a field on the command dict, not a separate operation
- `reboot`/`poweroff` require `"path": ["now"]`
- `/retrieve` supports three ops: `showConfig`, `returnValues`, `exists`

## Testing

- Use `.env` file with `VYOS_URL` and `VYOS_API_KEY` for local testing (gitignored)
- Use `commit-confirm` with short timeouts during testing to avoid persisting bad config
