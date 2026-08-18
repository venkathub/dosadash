# 09 — MCP Server Demo: Claude Desktop Orders a Dosa

Phase 6 deliverable: DosaDash exposes an MCP server with three tools —
`get_menu`, `check_inventory`, `place_order` — so any MCP client (Claude
Desktop, Claude Code, …) can browse the menu and place a real order.

## Architecture

The MCP server (`apps/ai/src/dosadash_ai/mcp_server.py`, console script
`dosadash-mcp`) is a **thin stdio adapter** that talks HTTP to the core api:

| Tool | Backend | Auth |
|---|---|---|
| `get_menu` | `GET /api/v1/menu` (public) | none |
| `check_inventory` | `GET /api/v1/internal/mcp/inventory` | `X-Internal-Token` |
| `place_order` | `POST /api/v1/internal/mcp/place` | `X-Internal-Token` |

No business logic lives in the adapter (same rule as the Telegram bot):
`place_order` runs through the real `order_service` — item ids are
DB-validated (Hard Rule 2), the state machine, kitchen-pause and business
hours all apply. Orders are placed as the dedicated demo customer
(`Claude (MCP demo)`, +919000000099) on the WEB channel, so they appear on
the KDS and admin dashboards like any other order.

## Claude Desktop setup

`~/.config/Claude/claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "dosadash": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/dosadash", "dosadash-mcp"],
      "env": {
        "DOSADASH_API_URL": "https://dosadash.venkateshs.dev",
        "DOSADASH_INTERNAL_TOKEN": "<INTERNAL_API_TOKEN from infra/.env>"
      }
    }
  }
}
```

For a local stack use `"DOSADASH_API_URL": "http://localhost:8000"`.

## Demo script (3 turns)

1. *"What dosas does DosaDash have under ₹150?"* → Claude calls `get_menu`,
   filters, quotes real prices.
2. *"Is there enough batter rice in the kitchen?"* → `check_inventory`
   shows stock vs reorder point.
3. *"Order me one Masala Dosa and a filter coffee."* → `place_order` →
   real order id + GST total; it pops up on the KDS (`/kds`) live.

## Notes

- The internal token is a demo-grade shared secret (same trust boundary as
  bot→api). Rotating `INTERNAL_API_TOKEN` in `infra/.env` revokes access.
- The server never runs on the VPS — it runs wherever the MCP client runs
  (zero RAM budget impact, Hard Rule 7).
