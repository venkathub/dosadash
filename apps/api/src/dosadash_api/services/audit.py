"""Audit-log helper: every admin/staff mutation appends a StaffAction row.

The row is queued on the caller's session so it commits atomically with the
mutation it describes (no audit entry for rolled-back changes).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import StaffAction, User


def record(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    entity: str,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(StaffAction(user_id=actor.id, action=action, entity=entity, detail=detail))
