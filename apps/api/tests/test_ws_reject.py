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
