"""Remote MCP access schemas (Phase 16): admin-issued API keys.

The hosted MCP endpoint (`/mcp`, served by apps/ai) authenticates clients
with keys generated from the admin GUI — the LLM-provider "API key" UX:
the plaintext key is returned exactly ONCE at creation (`McpKeyCreatedOut`),
only its SHA-256 hash is stored, and the list view shows a display prefix.
Revocation is immediate (modulo the ai-side ~60s verify cache).

Key format: `ddk_<urlsafe random>` — path-segment safe on purpose, because
ChatGPT's connector UI has no header field and connects via the tokenized
URL `/mcp/<key>` instead of `Authorization: Bearer`.
"""

import hashlib
import secrets
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MCP_KEY_PREFIX = "ddk_"
# First N chars shown in list views — enough to tell keys apart, useless
# to an attacker (the random part alone is 43 urlsafe chars ≈ 256 bits).
MCP_KEY_DISPLAY_CHARS = 12


def generate_mcp_key() -> str:
    """A fresh plaintext key. Caller stores only `hash_mcp_key(key)`."""
    return f"{MCP_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_mcp_key(key: str) -> str:
    """SHA-256 hex digest — the only form of a key that is ever persisted."""
    return hashlib.sha256(key.encode()).hexdigest()


class McpKeyIn(BaseModel):
    """Create request — a human label ("Cursor — Venkatesh laptop")."""

    name: str = Field(min_length=1, max_length=80)


class McpKeyOut(BaseModel):
    """List/detail view — never contains the plaintext key."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class McpKeyCreatedOut(McpKeyOut):
    """Create response — the ONE time the plaintext key leaves the server."""

    key: str


class McpKeyVerifyIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)


class McpKeyVerifyOut(BaseModel):
    """Always 200 from verify-key; `ok=False` covers unknown AND revoked so
    the response never leaks whether a guessed key ever existed."""

    ok: bool
    name: str | None = None
