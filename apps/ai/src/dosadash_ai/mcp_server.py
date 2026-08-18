"""DosaDash MCP server (Phase 6): `get_menu`, `check_inventory`, `place_order`.

Demo: Claude Desktop orders a dosa. The server is a THIN adapter over the
core api (Hard Rule 10 spirit — no business logic here): menu comes from
the public endpoint; inventory and ordering use internal-token endpoints,
so every placed order goes through the real order_service (item validation,
state machine, hours/pause enforcement — Hard Rule 2 upheld server-side).

Run (stdio, e.g. from Claude Desktop):

    DOSADASH_API_URL=https://dosadash.example.dev \
    DOSADASH_INTERNAL_TOKEN=... uv run dosadash-mcp

Claude Desktop config (~/.config/Claude/claude_desktop_config.json):

    {"mcpServers": {"dosadash": {
        "command": "uv",
        "args": ["run", "--project", "/path/to/dosadash", "dosadash-mcp"],
        "env": {"DOSADASH_API_URL": "https://…", "DOSADASH_INTERNAL_TOKEN": "…"}}}}
"""

import os
from typing import Any

import httpx
from mcp.server import MCPServer

server = MCPServer(
    name="dosadash",
    instructions=(
        "Order South Indian food from DosaDash (Chennai cloud kitchen). "
        "Look up the menu first to get numeric item_ids; prices are INR. "
        "place_order creates a REAL order in the connected environment."
    ),
)


def _api_url() -> str:
    return os.environ.get("DOSADASH_API_URL", "http://localhost:8000").rstrip("/")


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": os.environ.get("DOSADASH_INTERNAL_TOKEN", "")}


async def _get(path: str, *, internal: bool = False) -> Any:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_api_url()}{path}", headers=_internal_headers() if internal else {}
        )
    resp.raise_for_status()
    return resp.json()


@server.tool()
async def get_menu(category: str | None = None) -> list[dict[str, Any]]:
    """Current DosaDash menu: item_id, name, category, price (INR), veg flag,
    spice level and allergens. Only items returned here can be ordered —
    use their numeric item_id with place_order. Optionally filter by
    category (e.g. "Dosa", "Biryani", "Beverages")."""
    menu = await _get("/api/v1/menu")
    if category:
        menu = [m for m in menu if m["category"].lower() == category.lower()]
    return menu


@server.tool()
async def check_inventory(ingredient: str | None = None) -> list[dict[str, Any]]:
    """Kitchen ingredient stock levels (name, unit, stock_qty, reorder_point,
    low flag). Optionally filter by ingredient name substring."""
    rows = await _get("/api/v1/internal/mcp/inventory", internal=True)
    if ingredient:
        needle = ingredient.lower()
        rows = [r for r in rows if needle in r["name"].lower()]
    return rows


@server.tool()
async def place_order(items: list[dict[str, int]]) -> dict[str, Any]:
    """Place a REAL DosaDash order. `items` is a list of
    {"item_id": <int from get_menu>, "qty": <1-20>}. The api re-validates
    every item against the menu (unknown ids are rejected) and returns the
    created order with totals (5% GST included) and status."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_api_url()}/api/v1/internal/mcp/place",
            json={"items": items},
            headers=_internal_headers(),
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return {"ok": False, "error": detail, "status_code": resp.status_code}
    order = resp.json()
    return {
        "ok": True,
        "order_id": order["id"],
        "status": order["status"],
        "items": [{"name": i["name"], "qty": i["qty"]} for i in order["items"]],
        "subtotal_inr": order["subtotal"],
        "gst_inr": order["gst"],
        "total_inr": order["total"],
    }


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
