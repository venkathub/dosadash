"""Local transactional fixtures. LLMs are real; no business mutation endpoints are invoked."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_order(
    session: AsyncSession, *, foreign: bool = False, status: str = "PLACED"
) -> tuple[int, int]:
    phone = "+919999900044" if foreign else "+919999900043"
    uid = await session.scalar(
        text(
            "INSERT INTO users (phone, name, role, loyalty_points) "
            "VALUES (:phone, 'Evaluation Customer', 'customer', 0) "
            "ON CONFLICT (phone) DO UPDATE SET name = 'Evaluation Customer' RETURNING id"
        ),
        {"phone": phone},
    )
    item = (
        await session.execute(
            text("SELECT id, brand_id, price FROM menu_items ORDER BY id LIMIT 1")
        )
    ).one()
    oid = await session.scalar(
        text(
            "INSERT INTO orders (user_id, brand_id, channel, status, subtotal, gst, total, "
            "placed_at) VALUES (:uid, :brand, 'WEB', CAST(:status AS order_state), "
            "100, 5, 105, now()) RETURNING id"
        ),
        {"uid": uid, "brand": item.brand_id, "status": status},
    )
    await session.execute(
        text("INSERT INTO order_items (order_id, item_id, qty, unit_price) VALUES (:o, :i, 1, :p)"),
        {"o": oid, "i": item.id, "p": item.price},
    )
    return uid, oid
