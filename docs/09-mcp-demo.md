# 09 — MCP Everywhere: Order a Dosa from Any AI Tool

DosaDash exposes an MCP server with three tools — `get_menu`,
`check_inventory`, `place_order` — reachable from **every MCP client**:

| Client | Transport | Setup |
|---|---|---|
| ChatGPT (developer-mode connector — Plus/Pro+, **not Free/Go**) | remote Streamable HTTP | tokenized URL |
| OpenAI API / Playground (**any plan** — Responses API MCP tool) | remote Streamable HTTP | URL + Bearer header |
| Claude Code — **Web & CLI** | remote Streamable HTTP | `.mcp.json` / one command |
| Cursor | remote Streamable HTTP | one-click deeplink / `.cursor/mcp.json` |
| Claude Desktop | remote connector **or** local stdio | URL / `claude_desktop_config.json` |

## Architecture

One server, two transports (Phase 6 + Phase 16):

- **stdio** (`dosadash-mcp` console script) — runs wherever the client runs.
- **Streamable HTTP** — the SAME server hosted by `apps/ai` at
  `https://dosadash.venkateshs.dev/mcp` (stateless, JSON-response mode;
  Caddy routes `/mcp*` → ai; zero extra RAM — Hard Rule 7).

The server is a **thin adapter** over the core api (same rule as the
Telegram bot — no business logic in adapters): `place_order` runs through
the real `order_service` — item ids are DB-validated (Hard Rule 2), the
state machine, kitchen-pause, serving windows and business hours all
apply. Orders are placed as the dedicated demo customer
(`Claude (MCP demo)`, +919000000099) and appear on the KDS live.

### Auth: admin-issued API keys

Keys are generated in the **admin GUI → AI Studio → MCP tab** (LLM-provider
key UX: plaintext `ddk_…` shown exactly once, only the SHA-256 hash is
stored, revoke any time — clients disconnect within ~60s). Two accepted
shapes, because not every client can send headers:

- `Authorization: Bearer ddk_…` — Cursor, Claude Code, Claude Desktop
- tokenized URL `https://…/mcp/<ddk_…>` — ChatGPT (its connector UI has
  no header field)

Key verification fails **closed**: if the ai service cannot reach the api,
`/mcp` denies — it places real orders.

## Setup per client

Generate a key first (admin → MCP tab → “🔑 Generate key”). The tab also
renders all of the snippets below with your fresh key pre-filled.

### ChatGPT

> **Plan requirement**: custom MCP connectors need Developer mode, which
> is **not available on Free or Go**. Individual **Plus/Pro** plans get
> Developer mode with limits (per OpenAI's current docs, custom
> connectors on individual plans may be restricted to **read-only**
> tools — `get_menu`/`check_inventory` work, `place_order` may be
> blocked or prompt-gated); full read+write MCP is guaranteed on
> **Business/Enterprise/Edu**. If Settings → Apps & Connectors →
> Advanced shows no "Developer mode" toggle, your plan can't add custom
> servers — use the [any-plan fallback](#no-chatgpt-plan-openai-api--playground)
> below instead.

1. Settings → **Apps & Connectors** → Advanced → enable **Developer mode**.
2. Create connector → MCP Server URL:
   `https://dosadash.venkateshs.dev/mcp/<your ddk_… key>`
   → Authentication: **None** (the key travels in the URL).
3. In a chat, enable the connector under the tools menu and ask:
   *“Order me a Masala Dosa from DosaDash.”*

### No ChatGPT plan? OpenAI API / Playground

The **Responses API is a full MCP client independent of any ChatGPT
subscription** — a pay-per-use API key from platform.openai.com is
enough, and write tools work. Zero-code: Playground → Tools → **MCP
server** → paste the `/mcp` URL + `Authorization: Bearer ddk_…` header
(or just the tokenized URL). In code:

```python
from openai import OpenAI

client = OpenAI()  # OPENAI_API_KEY
resp = client.responses.create(
    model="gpt-4o-mini",
    tools=[{
        "type": "mcp",
        "server_label": "dosadash",
        "server_url": "https://dosadash.venkateshs.dev/mcp",
        "headers": {"Authorization": "Bearer ddk_…"},
        "require_approval": "never",
    }],
    input="What dosas are under ₹150? Order me one Masala Dosa.",
)
print(resp.output_text)
```

### Claude Code (Web & CLI)

The repo commits a project-scoped **`.mcp.json`** that reads the key from
the environment — set `DOSADASH_MCP_KEY` and the server is available in
any checkout (Claude Code Web included):

```bash
export DOSADASH_MCP_KEY=ddk_…
```

Or add it user-wide with one command:

```bash
claude mcp add --transport http dosadash https://dosadash.venkateshs.dev/mcp \
  --header "Authorization: Bearer ddk_…"
```

### Cursor

Click the **one-click install deeplink** from the admin MCP tab
(`cursor://anysphere.cursor-deeplink/mcp/install?…`), or set
`DOSADASH_MCP_KEY` in your environment — the repo commits
**`.cursor/mcp.json`** which references `${env:DOSADASH_MCP_KEY}`.

### Claude Desktop

Remote (no local process): Settings → Connectors → **Add custom
connector** → URL `https://dosadash.venkateshs.dev/mcp/<ddk_… key>`.

Local stdio (the Phase 6 path — useful against a local stack):

```json
{
  "mcpServers": {
    "dosadash": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/dosadash", "dosadash-mcp"],
      "env": {
        "DOSADASH_API_URL": "http://localhost:8000",
        "DOSADASH_INTERNAL_TOKEN": "<INTERNAL_API_TOKEN from infra/.env>"
      }
    }
  }
}
```

## Demo script (3 turns, any client)

1. *"What dosas does DosaDash have under ₹150?"* → `get_menu`, real prices.
2. *"Is there enough batter rice in the kitchen?"* → `check_inventory`
   shows stock vs reorder point.
3. *"Order me one Masala Dosa and a filter coffee."* → `place_order` →
   real order id + GST total; it pops up on the KDS (`/kds`) live.

## Notes

- **Never commit a key** — `.mcp.json` / `.cursor/mcp.json` only reference
  the `DOSADASH_MCP_KEY` env var; plaintext keys exist in the admin GUI
  for one render.
- Revocation (admin MCP tab) takes effect within the ai-side verify-cache
  TTL (~60s). Rotating `INTERNAL_API_TOKEN` still revokes the stdio path.
- The hosted endpoint is stateless JSON (no session affinity) — safe
  behind Caddy/the front proxy, friendly to every client's HTTP stack.
