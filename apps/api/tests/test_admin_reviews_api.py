"""Phase 8 admin reviews inbox — AI service mocked via dependency override."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Order, Review, StaffAction, User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import (
    RATING_ONLY_MODEL,
    AspectLabel,
    OrderState,
    ReviewReplyResponse,
    ReviewScoreDraft,
    ReviewScoreResponse,
    Role,
    rating_only_sentiment,
)

ADMIN_REVIEWS = "/api/v1/admin/reviews"


class FakeAIClient:
    """Scores every review NEGATIVE-delivery (text) or rating-only, and
    drafts a fixed reply — shaped like the real ai-side sanitizer output."""

    def __init__(self, fail: bool = False, reply_fallback: bool = False) -> None:
        self.score_requests = []
        self.reply_requests = []
        self.fail = fail
        self.reply_fallback = reply_fallback

    async def score_reviews(self, request) -> ReviewScoreResponse:
        self.score_requests.append(request)
        if self.fail:
            raise AIServiceError("AI service call failed: boom")
        scores, rating_only = [], []
        for r in request.reviews:
            if not r.text.strip():
                rating_only.append(r.review_id)
                scores.append(
                    ReviewScoreDraft(
                        review_id=r.review_id,
                        sentiment=rating_only_sentiment(r.rating),
                        aspects=[],
                    )
                )
            else:
                scores.append(
                    ReviewScoreDraft(
                        review_id=r.review_id,
                        sentiment="NEGATIVE",
                        aspects=[AspectLabel(aspect="delivery", sentiment="NEGATIVE")],
                    )
                )
        return ReviewScoreResponse(
            scores=scores,
            rating_only_ids=rating_only,
            rejected=[],
            model="gpt-4o-mini",
        )

    async def draft_review_reply(self, request) -> ReviewReplyResponse:
        self.reply_requests.append(request)
        if self.fail:
            raise AIServiceError("AI service call failed: boom")
        return ReviewReplyResponse(
            reply="We're so sorry — the kitchen is on it. — Team DosaDash",
            model=None if self.reply_fallback else "gpt-4o-mini",
            fallback=self.reply_fallback,
        )


@pytest.fixture
def fake_ai(client):
    from dosadash_api.main import app

    fake = FakeAIClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


async def _login_as(db_session, phone: str, role: Role) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _customer(client, phone="9111188001") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _delivered_order_with_review(
    client, db_session, customer, *, rating=2, text="Delivery was very late.", created_at=None
) -> tuple[int, int]:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
    )
    order_id = resp.json()["id"]
    order = await db_session.scalar(select(Order).where(Order.id == order_id))
    order.status = OrderState.DELIVERED
    order.delivered_at = datetime.now(UTC)
    review = Review(order_id=order_id, user_id=order.user_id, rating=rating, text=text)
    if created_at is not None:
        review.created_at = created_at
    db_session.add(review)
    await db_session.commit()
    return order_id, review.id


# --------------------------------------------------------------------- inbox


async def test_inbox_lists_reviews_with_dishes(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581001", Role.ADMIN)
    customer = await _customer(client)
    await _delivered_order_with_review(client, db_session, customer)
    resp = await client.get(ADMIN_REVIEWS, headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["unscored"] == 1
    assert body["reviews"][0]["dishes"] == ["Masala Dosa"]
    assert body["reviews"][0]["sentiment"] is None


async def test_inbox_requires_admin(client, db_session):
    customer = await _customer(client, "9111188002")
    resp = await client.get(ADMIN_REVIEWS, headers=customer)
    assert resp.status_code == 403


async def test_inbox_filters(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581002", Role.ADMIN)
    customer = await _customer(client, "9111188003")
    await _delivered_order_with_review(client, db_session, customer)
    unscored = await client.get(f"{ADMIN_REVIEWS}?filter=unscored", headers=admin)
    assert unscored.json()["total"] == 1
    negative = await client.get(f"{ADMIN_REVIEWS}?filter=negative", headers=admin)
    assert negative.json()["total"] == 0  # not scored yet


# ------------------------------------------------------------------- scoring


async def test_score_pending_persists_scores_and_provenance(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581003", Role.ADMIN)
    customer = await _customer(client, "9111188004")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    resp = await client.post(f"{ADMIN_REVIEWS}/score-pending", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scored"] == 1
    assert body["model"] == "gpt-4o-mini"

    review = await db_session.scalar(select(Review).where(Review.id == review_id))
    await db_session.refresh(review)
    assert review.sentiment == "NEGATIVE"
    assert review.aspects == [{"aspect": "delivery", "sentiment": "NEGATIVE"}]
    assert review.scored_model == "gpt-4o-mini"
    assert review.scored_prompt_version == "review_sentiment_v1"
    assert review.scored_at is not None

    audit = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "reviews.score")
    )
    assert audit is not None


async def test_score_pending_rating_only_marked_deterministic(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581004", Role.ADMIN)
    customer = await _customer(client, "9111188005")
    _, review_id = await _delivered_order_with_review(
        client, db_session, customer, rating=5, text=""
    )
    resp = await client.post(f"{ADMIN_REVIEWS}/score-pending", headers=admin)
    assert resp.json()["rating_only"] == 1
    review = await db_session.scalar(select(Review).where(Review.id == review_id))
    await db_session.refresh(review)
    assert review.sentiment == "POSITIVE"
    assert review.scored_model == RATING_ONLY_MODEL


async def test_score_pending_ai_down_is_502_and_nothing_written(client, db_session, fake_ai):
    fake_ai.fail = True
    admin = await _login_as(db_session, "+919555581005", Role.ADMIN)
    customer = await _customer(client, "9111188006")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    resp = await client.post(f"{ADMIN_REVIEWS}/score-pending", headers=admin)
    assert resp.status_code == 502
    review = await db_session.scalar(select(Review).where(Review.id == review_id))
    assert review.sentiment is None


async def test_score_pending_empty_queue_is_noop(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581006", Role.ADMIN)
    resp = await client.post(f"{ADMIN_REVIEWS}/score-pending", headers=admin)
    assert resp.status_code == 200
    assert resp.json() == {"scored": 0, "rating_only": 0, "failed": 0, "model": None}
    assert fake_ai.score_requests == []


# ------------------------------------------------------------------- replies


async def test_draft_reply_stores_backoffice_draft(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581007", Role.ADMIN)
    customer = await _customer(client, "9111188007")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    resp = await client.post(f"{ADMIN_REVIEWS}/{review_id}/draft-reply", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply_draft"].startswith("We're so sorry")
    assert body["reply_draft_model"] == "gpt-4o-mini"
    assert body["owner_reply"] is None  # drafting never publishes
    # the ai request carried the order's dishes as context
    assert fake_ai.reply_requests[0].dishes == ["Masala Dosa"]


async def test_draft_reply_fallback_is_labeled(client, db_session, fake_ai):
    fake_ai.reply_fallback = True
    admin = await _login_as(db_session, "+919555581008", Role.ADMIN)
    customer = await _customer(client, "9111188008")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    resp = await client.post(f"{ADMIN_REVIEWS}/{review_id}/draft-reply", headers=admin)
    assert resp.json()["reply_draft_model"] == "fallback:template"


async def test_publish_reply_source_ai_draft_vs_manual(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581009", Role.ADMIN)
    customer = await _customer(client, "9111188009")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    draft = await client.post(f"{ADMIN_REVIEWS}/{review_id}/draft-reply", headers=admin)
    draft_text = draft.json()["reply_draft"]

    published = await client.post(
        f"{ADMIN_REVIEWS}/{review_id}/reply", headers=admin, json={"reply": draft_text}
    )
    assert published.status_code == 200
    assert published.json()["reply_source"] == "AI_DRAFT"
    assert published.json()["owner_reply"] == draft_text

    # customer now sees the reply on their order review
    order_id = published.json()["order_id"]
    mine = await client.get(f"/api/v1/orders/{order_id}/review", headers=customer)
    assert mine.json()["owner_reply"] == draft_text


async def test_publish_edited_reply_is_manual(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581010", Role.ADMIN)
    customer = await _customer(client, "9111188010")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    await client.post(f"{ADMIN_REVIEWS}/{review_id}/draft-reply", headers=admin)
    resp = await client.post(
        f"{ADMIN_REVIEWS}/{review_id}/reply",
        headers=admin,
        json={"reply": "Our own words entirely. — Team DosaDash"},
    )
    assert resp.json()["reply_source"] == "MANUAL"


async def test_publish_twice_is_409(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581011", Role.ADMIN)
    customer = await _customer(client, "9111188011")
    _, review_id = await _delivered_order_with_review(client, db_session, customer)
    first = await client.post(
        f"{ADMIN_REVIEWS}/{review_id}/reply", headers=admin, json={"reply": "Thanks!"}
    )
    assert first.status_code == 200
    second = await client.post(
        f"{ADMIN_REVIEWS}/{review_id}/reply", headers=admin, json={"reply": "Again?"}
    )
    assert second.status_code == 409


async def test_draft_reply_missing_review_404(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581012", Role.ADMIN)
    resp = await client.post(f"{ADMIN_REVIEWS}/424242/draft-reply", headers=admin)
    assert resp.status_code == 404


# -------------------------------------------------------------------- trends


async def test_trends_alert_on_latest_week_spike(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581013", Role.ADMIN)
    customer = await _customer(client, "9111188012")
    now = datetime.now(UTC).replace(tzinfo=None)
    # 3 fresh freshness-complaints this week, none earlier → alert
    for i in range(3):
        _, review_id = await _delivered_order_with_review(
            client, db_session, customer, text=f"Too oily dosa #{i}", created_at=now
        )
        review = await db_session.scalar(select(Review).where(Review.id == review_id))
        review.sentiment = "NEGATIVE"
        review.aspects = [{"aspect": "freshness", "sentiment": "NEGATIVE"}]
    await db_session.commit()

    resp = await client.get(f"{ADMIN_REVIEWS}/trends?weeks=4", headers=admin)
    assert resp.status_code == 200, resp.text
    by_aspect = {a["aspect"]: a for a in resp.json()["aspects"]}
    fresh = by_aspect["freshness"]
    assert fresh["alert"] is True
    assert fresh["points"][-1]["count"] == 3
    assert "Masala Dosa" in fresh["top_dishes"]
    assert by_aspect["taste"]["alert"] is False


async def test_trends_no_alert_when_flat(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555581014", Role.ADMIN)
    customer = await _customer(client, "9111188013")
    now = datetime.now(UTC).replace(tzinfo=None)
    # one complaint per week for 3 weeks → flat, no alert
    for weeks_ago in (0, 1, 2):
        _, review_id = await _delivered_order_with_review(
            client,
            db_session,
            customer,
            text="late",
            created_at=now - timedelta(weeks=weeks_ago),
        )
        review = await db_session.scalar(select(Review).where(Review.id == review_id))
        review.sentiment = "NEGATIVE"
        review.aspects = [{"aspect": "delivery", "sentiment": "NEGATIVE"}]
    await db_session.commit()
    resp = await client.get(f"{ADMIN_REVIEWS}/trends?weeks=4", headers=admin)
    by_aspect = {a["aspect"]: a for a in resp.json()["aspects"]}
    assert by_aspect["delivery"]["alert"] is False
