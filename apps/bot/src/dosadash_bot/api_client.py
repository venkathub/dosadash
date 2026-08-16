"""Thin API client for bot→api internal calls (Hard Rule 10: no business
logic here — the bot just forwards and renders)."""

import httpx


class LinkResult:
    def __init__(self, ok: bool, name: str | None = None, detail: str | None = None) -> None:
        self.ok = ok
        self.name = name
        self.detail = detail


async def link_account(
    *,
    api_base_url: str,
    internal_token: str,
    code: str,
    tg_user_id: int,
    tg_name: str | None,
    client: httpx.AsyncClient | None = None,
) -> LinkResult:
    url = f"{api_base_url}/api/v1/auth/telegram/link"
    payload = {"code": code, "tg_user_id": tg_user_id, "tg_name": tg_name}
    headers = {"X-Internal-Token": internal_token}
    try:
        if client is not None:
            resp = await client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post(url, json=payload, headers=headers)
    except httpx.HTTPError:
        return LinkResult(ok=False, detail="API unreachable")
    if resp.status_code == 200:
        return LinkResult(ok=True, name=resp.json().get("name"))
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    return LinkResult(ok=False, detail=detail)
