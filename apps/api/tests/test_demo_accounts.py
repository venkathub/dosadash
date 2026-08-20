"""Demo-account seeding (Phase 9 /demo page): idempotent create + re-promote."""

from sqlalchemy import select

from dosadash_api.db.models import User
from dosadash_api.seed import DEMO_ACCOUNTS, apply_demo_accounts
from dosadash_shared import Role


async def test_demo_accounts_created(db_session) -> None:
    await apply_demo_accounts(db_session)
    for phone, role, name in DEMO_ACCOUNTS:
        user = await db_session.scalar(select(User).where(User.phone == phone))
        assert user is not None
        assert user.role == role
        assert user.name == name


async def test_demo_accounts_idempotent_and_repromotes(db_session) -> None:
    await apply_demo_accounts(db_session)
    staff_phone = DEMO_ACCOUNTS[0][0]
    user = await db_session.scalar(select(User).where(User.phone == staff_phone))
    user.role = Role.CUSTOMER  # simulate a demo admin demoting the account
    await db_session.commit()

    await apply_demo_accounts(db_session)  # re-run = repair, never duplicate
    rows = (await db_session.scalars(select(User).where(User.phone == staff_phone))).all()
    assert len(rows) == 1
    assert rows[0].role == DEMO_ACCOUNTS[0][1]


def test_no_owner_demo_credential() -> None:
    """OWNER stays private by design — the demo page must never grow one."""
    assert all(role != Role.OWNER for _, role, _ in DEMO_ACCOUNTS)
