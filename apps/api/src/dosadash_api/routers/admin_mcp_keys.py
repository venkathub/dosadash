"""Admin MCP API keys (Phase 16): GUI-issued credentials for /mcp.

LLM-provider key UX: POST returns the plaintext key exactly once; the DB
keeps only its SHA-256 hash. Revocation stamps `revoked_at` (rows are never
deleted) and takes effect at the ai-side verify cache TTL (~60s). All
mutations are audited (Phase 2 audit trail).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import McpApiKey, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import (
    MCP_KEY_DISPLAY_CHARS,
    McpKeyCreatedOut,
    McpKeyIn,
    McpKeyOut,
    Role,
    generate_mcp_key,
    hash_mcp_key,
)

router = APIRouter(prefix="/api/v1/admin/mcp-keys", tags=["admin:mcp-keys"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


@router.get("", response_model=list[McpKeyOut])
async def list_keys(session: SessionDep, admin: User = AdminUser) -> list[McpKeyOut]:
    rows = (await session.scalars(select(McpApiKey).order_by(McpApiKey.created_at.desc()))).all()
    return [McpKeyOut.model_validate(r) for r in rows]


@router.post("", response_model=McpKeyCreatedOut, status_code=201)
async def create_key(
    body: McpKeyIn, session: SessionDep, admin: User = AdminUser
) -> McpKeyCreatedOut:
    key = generate_mcp_key()
    row = McpApiKey(
        name=body.name.strip(),
        key_prefix=key[:MCP_KEY_DISPLAY_CHARS],
        key_hash=hash_mcp_key(key),
        created_by=admin.id,
    )
    session.add(row)
    audit.record(
        session,
        actor=admin,
        action="mcp_key.create",
        entity="mcp_key",
        detail={"name": row.name, "key_prefix": row.key_prefix},
    )
    await session.commit()
    out = McpKeyOut.model_validate(row)
    # The ONE time the plaintext leaves the server (Rule 9 spirit: it is a
    # secret from birth — never logged, never re-readable).
    return McpKeyCreatedOut(**out.model_dump(), key=key)


@router.post("/{key_id}/revoke", response_model=McpKeyOut)
async def revoke_key(key_id: int, session: SessionDep, admin: User = AdminUser) -> McpKeyOut:
    row = await session.get(McpApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Key already revoked")
    row.revoked_at = func.now()
    audit.record(
        session,
        actor=admin,
        action="mcp_key.revoke",
        entity=f"mcp_key:{key_id}",
        detail={"name": row.name, "key_prefix": row.key_prefix},
    )
    await session.commit()
    await session.refresh(row)
    return McpKeyOut.model_validate(row)
