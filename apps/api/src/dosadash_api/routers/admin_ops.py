"""Admin backoffice ops (Phase 2): settings, staff RBAC, audit log.

/api/v1/admin/settings               — business hours, delivery pincodes
/api/v1/admin/settings/kitchen-pause — pause/resume checkout (503 while paused)
/api/v1/admin/users                  — list users, change roles (owner-gated)
/api/v1/admin/audit                  — StaffAction audit trail (read-only)

Mutations write StaffAction rows and publish to `pubsub:settings` (Hard
Rule 4) so agents stop taking orders the moment the kitchen pauses.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Settings, StaffAction, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import (
    AdminUserOut,
    KitchenPauseIn,
    Role,
    RoleUpdateIn,
    SettingsOut,
    SettingsUpdateIn,
    StaffActionOut,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin:ops"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

PRIVILEGED = {Role.ADMIN, Role.OWNER}


async def _settings_row(session: AsyncSession) -> Settings:
    row = await session.get(Settings, 1)
    if row is None:
        row = Settings(id=1, delivery_pincodes=[])
        session.add(row)
        await session.flush()
    return row


# ------------------------------------------------------------------- settings


@router.get("/settings", response_model=SettingsOut)
async def get_settings_row(session: SessionDep, admin: User = AdminUser) -> SettingsOut:
    return SettingsOut.model_validate(await _settings_row(session))


@router.put("/settings", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdateIn, session: SessionDep, admin: User = AdminUser
) -> SettingsOut:
    row = await _settings_row(session)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    if "business_hours" in changes:
        row.business_hours = changes["business_hours"]
    if "delivery_pincodes" in changes:
        if changes["delivery_pincodes"] is None:
            raise HTTPException(status_code=422, detail="delivery_pincodes cannot be null")
        row.delivery_pincodes = changes["delivery_pincodes"]
    audit.record(
        session,
        actor=admin,
        action="settings.update",
        entity="settings",
        detail={"fields": sorted(changes)},
    )
    await session.commit()
    await events.publish_settings_event("settings.updated", detail={"fields": sorted(changes)})
    return SettingsOut.model_validate(row)


@router.post("/settings/kitchen-pause", response_model=SettingsOut)
async def kitchen_pause(
    body: KitchenPauseIn, session: SessionDep, admin: User = AdminUser
) -> SettingsOut:
    """Pause/resume the kitchen. Checkout returns 503 while paused."""
    row = await _settings_row(session)
    row.kitchen_paused = body.paused
    audit.record(
        session,
        actor=admin,
        action="settings.kitchen_pause",
        entity="settings",
        detail={"paused": body.paused, "reason": body.reason},
    )
    await session.commit()
    await events.publish_settings_event(
        "settings.kitchen_pause", detail={"paused": body.paused, "reason": body.reason}
    )
    return SettingsOut.model_validate(row)


# ---------------------------------------------------------------- staff RBAC


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    session: SessionDep,
    admin: User = AdminUser,
    role: Role | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminUserOut]:
    stmt = select(User).order_by(User.id).limit(limit).offset(offset)
    if role is not None:
        stmt = stmt.where(User.role == role)
    users = (await session.scalars(stmt)).all()
    return [AdminUserOut.model_validate(u) for u in users]


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
async def set_role(
    user_id: int, body: RoleUpdateIn, session: SessionDep, admin: User = AdminUser
) -> AdminUserOut:
    """Change a user's role. Granting/revoking admin|owner is owner-only;
    nobody may change their own role (no self-lockout, no self-promotion)."""
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=403, detail="You cannot change your own role")
    touches_privileged = body.role in PRIVILEGED or target.role in PRIVILEGED
    if touches_privileged and admin.role != Role.OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can manage admin/owner roles")
    previous = target.role
    target.role = body.role
    audit.record(
        session,
        actor=admin,
        action="user.role",
        entity=f"user:{target.id}",
        detail={"from": previous.value, "to": body.role.value},
    )
    await session.commit()
    return AdminUserOut.model_validate(target)


# ------------------------------------------------------------------ audit log


@router.get("/audit", response_model=list[StaffActionOut])
async def list_audit(
    session: SessionDep,
    admin: User = AdminUser,
    action: str | None = None,
    entity: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[StaffActionOut]:
    """Newest-first audit trail; filter by exact action and/or entity."""
    stmt = select(StaffAction).order_by(StaffAction.at.desc(), StaffAction.id.desc())
    if action:
        stmt = stmt.where(StaffAction.action == action)
    if entity:
        stmt = stmt.where(StaffAction.entity == entity)
    rows = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return [StaffActionOut.model_validate(r) for r in rows]
