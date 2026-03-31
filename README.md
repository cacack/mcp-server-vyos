# mcp-server-vyos

MCP server for VyOS router management via the HTTPS REST API. Provides both router management tools and live VyOS documentation lookup.

## Installation

```bash
pip install mcp-server-vyos
```

## Configuration

Set environment variables:

- `VYOS_URL` — Router API endpoint (e.g., `https://vyos.example.com`)
- `VYOS_API_KEY` — API key for authentication

### VyOS Router Setup

Enable the HTTPS API on your VyOS router:

```bash
configure
set service https api keys id my-mcp-key key <your-api-key>
set service https api rest
commit
save
```

### Claude Code

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "vyos": {
      "command": "mcp-server-vyos",
      "env": {
        "VYOS_URL": "https://vyos.example.com",
        "VYOS_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Tools

### Router Management

| Tool | Description |
|---|---|
| `vyos_info` | System info (no auth required) |
| `vyos_retrieve` | Read configuration at a path |
| `vyos_return_values` | Get multi-valued config node values |
| `vyos_exists` | Check if a config path exists |
| `vyos_show` | Run operational show commands |
| `vyos_configure` | Apply config with commit-confirm (safe default) |
| `vyos_confirm` | Confirm a pending commit-confirm |
| `vyos_save` | Save running config to disk |
| `vyos_load` | Load a configuration file |
| `vyos_merge` | Merge config file or string into running config |
| `vyos_generate` | Generate keys, certificates, etc. |
| `vyos_reset` | Reset operations |
| `vyos_reboot` | Reboot the router |
| `vyos_poweroff` | Power off the router |
| `vyos_image_add` | Add a system image from URL |
| `vyos_image_delete` | Delete a system image |

### Documentation

| Tool | Description |
|---|---|
| `vyos_docs_search` | Search VyOS documentation by topic |
| `vyos_docs_read` | Read a specific documentation page |

Documentation is fetched live from the [vyos-documentation](https://github.com/vyos/vyos-documentation) repository, so it stays in sync with the latest VyOS releases. Results are cached for 1 hour.

## Safety

- Configuration changes use `commit-confirm` by default -- changes auto-revert after 5 minutes unless confirmed with `vyos_confirm`
- Destructive operations (`vyos_reboot`, `vyos_poweroff`, `vyos_image_delete`) include warning descriptions
- API keys are never logged or included in tool outputs
- Self-signed TLS certificates are accepted by default (common on VyOS)

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
