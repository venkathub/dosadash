"""WS auth rejections must be readable by browsers (regression: KDS stuck on
"Reconnecting…" once its 30-min access token expired).

A close *before* accept makes the server answer the handshake with HTTP 403,
which browsers report as code 1006 — the KDS only returns to its login form on
4401/4403. These tests assert the application code actually reaches the client.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dosadash_api.main import app


@pytest.mark.parametrize("path", ["/ws/kds", "/ws/fixer", "/ws/orders/1"])
@pytest.mark.parametrize("token", ["", "garbage"])
def test_bad_token_closes_with_4401_after_accept(path: str, token: str) -> None:
    client = TestClient(app)
    with client.websocket_connect(f"{path}?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4401


def _signed(payload: dict) -> str:
    import jwt

    from dosadash_api.config import get_settings

    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


@pytest.mark.parametrize("path", ["/ws/kds", "/ws/fixer"])
@pytest.mark.parametrize("claims", [{}, {"sub": "not-a-number"}])
def test_validly_signed_token_with_bad_sub_is_4401_not_a_crash(path: str, claims: dict) -> None:
    """A signed token whose `sub` is missing/non-numeric must be rejected
    cleanly (no KeyError/ValueError → 500/1011), before any DB access."""
    import time

    token = _signed({**claims, "exp": int(time.time()) + 600})
    client = TestClient(app)
    with client.websocket_connect(f"{path}?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4401


@pytest.mark.parametrize("path", ["/ws/kds", "/ws/fixer"])
def test_wrong_role_closes_with_4403_after_accept(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from dosadash_api.routers import ws as ws_router
    from dosadash_shared import Role

    async def fake_auth(_token: str):
        return SimpleNamespace(id=1, role=Role.CUSTOMER)

    monkeypatch.setattr(ws_router, "_authenticate", fake_auth)
    client = TestClient(app)
    with client.websocket_connect(f"{path}?token=x") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4403
