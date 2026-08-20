"""Admin reviews inbox (Phase 8): auto-tags, trend alerts, AI-drafted replies.

GET  /api/v1/admin/reviews                     — inbox (filter unscored/negative/unreplied)
POST /api/v1/admin/reviews/score-pending       — score unscored reviews via the ai service
POST /api/v1/admin/reviews/{id}/draft-reply    — AI-draft an owner reply (backoffice-only)
POST /api/v1/admin/reviews/{id}/reply          — publish the owner reply (human gate)
GET  /api/v1/admin/reviews/trends              — weekly complaint counts + alert flags

Trust model: the LLM tags and drafts; a human publishes. `reply_source` is
AI_DRAFT only when the published text is exactly the draft — one edited
character makes it MANUAL. Owner-written replies are human authority (no
forbidden-term guardrail api-side; the guardrail exists for what the MODEL
writes, ai-side)."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import MenuItem, OrderItem, Review, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    MAX_REVIEW_SCORE_ITEMS,
    RATING_ONLY_MODEL,
    REVIEW_ASPECTS,
    AdminReviewListOut,
    AdminReviewOut,
    AspectLabel,
    ReviewAspectTrend,
    ReviewReplyPublishIn,
    ReviewReplyRequest,
    ReviewScoreRequest,
    ReviewScoreRunOut,
    ReviewScoreSourceItem,
    ReviewTrendPoint,
    ReviewTrendsOut,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/reviews", tags=["admin:reviews"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AIClientDep = Annotated[AIClient, Depends(get_ai_client)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

TREND_ALERT_MIN_COUNT = 3  # complaints in the latest week to even consider
TREND_ALERT_RATIO = 2.0  # vs the mean of earlier weeks


async def _dishes_by_order(session: AsyncSession, order_ids: list[int]) -> dict[int, list[str]]:
    if not order_ids:
        return {}
    rows = await session.execute(
        select(OrderItem.order_id, MenuItem.name)
        .join(MenuItem, MenuItem.id == OrderItem.item_id)
        .where(OrderItem.order_id.in_(order_ids))
    )
    dishes: dict[int, list[str]] = defaultdict(list)
    for order_id, name in rows:
        dishes[order_id].append(name)
    return dishes


def _to_admin_out(review: Review, dishes: dict[int, list[str]]) -> AdminReviewOut:
    out = AdminReviewOut.model_validate(review)
    out.dishes = dishes.get(review.order_id, [])
    return out


# --------------------------------------------------------------------- inbox


@router.get("", response_model=AdminReviewListOut)
async def list_reviews(
    session: SessionDep,
    admin: User = AdminUser,
    filter: Literal["all", "unscored", "negative", "unreplied"] = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminReviewListOut:
    query = select(Review)
    if filter == "unscored":
        query = query.where(Review.sentiment.is_(None))
    elif filter == "negative":
        query = query.where(Review.sentiment == "NEGATIVE")
    elif filter == "unreplied":
        query = query.where(Review.owner_reply.is_(None), Review.text != "")
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    unscored = await session.scalar(
        select(func.count()).select_from(Review).where(Review.sentiment.is_(None))
    )
    rows = (
        (
            await session.execute(
                query.order_by(Review.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    dishes = await _dishes_by_order(session, [r.order_id for r in rows])
    return AdminReviewListOut(
        reviews=[_to_admin_out(r, dishes) for r in rows],
        total=total or 0,
        unscored=unscored or 0,
    )


# ------------------------------------------------------------------- scoring


@router.post("/score-pending", response_model=ReviewScoreRunOut)
async def score_pending(
    session: SessionDep,
    ai: AIClientDep,
    admin: User = AdminUser,
) -> ReviewScoreRunOut:
    rows = (
        (
            await session.execute(
                select(Review)
                .where(Review.sentiment.is_(None))
                .order_by(Review.created_at.desc())
                .limit(MAX_REVIEW_SCORE_ITEMS)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ReviewScoreRunOut(scored=0, rating_only=0, failed=0)
    request = ReviewScoreRequest(
        reviews=[ReviewScoreSourceItem(review_id=r.id, rating=r.rating, text=r.text) for r in rows]
    )
    try:
        result = await ai.score_reviews(request)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    by_id = {r.id: r for r in rows}
    rating_only = set(result.rating_only_ids)
    local = set(result.local_ids)
    now = datetime.now(UTC)
    scored = 0
    for score in result.scores:
        review = by_id.get(score.review_id)
        if review is None:  # ai must never add reviews the api didn't send
            continue
        review.sentiment = score.sentiment
        review.aspects = [a.model_dump() for a in score.aspects]
        if score.review_id in rating_only:
            review.scored_model = RATING_ONLY_MODEL
            review.scored_prompt_version = result.prompt_version
        elif score.review_id in local:
            # INT8 ONNX champion on-CPU — no LLM, no prompt involved
            review.scored_model = result.local_model
            review.scored_prompt_version = None
        else:
            review.scored_model = result.model
            review.scored_prompt_version = result.prompt_version
        review.scored_at = now
        scored += 1
    audit.record(
        session,
        actor=admin,
        action="reviews.score",
        entity="reviews:pending",
        detail={
            "scored": scored,
            "rating_only": len(result.rating_only_ids),
            "local": len(result.local_ids),
            "failed": len(result.rejected),
            "model": result.model,
            "local_model": result.local_model,
            "prompt_version": result.prompt_version,
        },
    )
    await session.commit()
    return ReviewScoreRunOut(
        scored=scored,
        rating_only=len(result.rating_only_ids),
        local=len(result.local_ids),
        failed=len(result.rejected),
        model=result.model,
        local_model=result.local_model,
    )


# ------------------------------------------------------------------- replies


async def _review_or_404(session: AsyncSession, review_id: int) -> Review:
    review = await session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return review


@router.post("/{review_id}/draft-reply", response_model=AdminReviewOut)
async def draft_reply(
    review_id: int,
    session: SessionDep,
    ai: AIClientDep,
    admin: User = AdminUser,
) -> AdminReviewOut:
    review = await _review_or_404(session, review_id)
    dishes = await _dishes_by_order(session, [review.order_id])
    request = ReviewReplyRequest(
        review_id=review.id,
        rating=review.rating,
        text=review.text,
        sentiment=review.sentiment,
        aspects=[AspectLabel.model_validate(a) for a in (review.aspects or [])],
        dishes=dishes.get(review.order_id, [])[:10],
    )
    try:
        result = await ai.draft_review_reply(request)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    review.reply_draft = result.reply
    review.reply_draft_model = "fallback:template" if result.fallback else result.model
    audit.record(
        session,
        actor=admin,
        action="reviews.draft_reply",
        entity=f"review:{review.id}",
        detail={"model": review.reply_draft_model, "fallback": result.fallback},
    )
    await session.commit()
    await session.refresh(review)
    return _to_admin_out(review, dishes)


@router.post("/{review_id}/reply", response_model=AdminReviewOut)
async def publish_reply(
    review_id: int,
    body: ReviewReplyPublishIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> AdminReviewOut:
    review = await _review_or_404(session, review_id)
    if review.owner_reply is not None:
        raise HTTPException(status_code=409, detail="this review already has a published reply")
    reply = body.reply.strip()
    source = "AI_DRAFT" if reply == (review.reply_draft or "").strip() else "MANUAL"
    review.owner_reply = reply
    review.reply_source = source
    review.replied_by = admin.id
    review.replied_at = datetime.now(UTC)
    audit.record(
        session,
        actor=admin,
        action="reviews.reply",
        entity=f"review:{review.id}",
        detail={"source": source},
    )
    await session.commit()
    await session.refresh(review)
    dishes = await _dishes_by_order(session, [review.order_id])
    return _to_admin_out(review, dishes)


# -------------------------------------------------------------------- trends


def _week_start(at: datetime) -> str:
    monday = (at - timedelta(days=at.weekday())).date()
    return monday.isoformat()


@router.get("/trends", response_model=ReviewTrendsOut)
async def trends(
    session: SessionDep,
    admin: User = AdminUser,
    weeks: int = Query(default=8, ge=2, le=26),
) -> ReviewTrendsOut:
    """Weekly NEGATIVE mention counts per aspect over the trailing window.
    Alert when the latest week is both material (≥ TREND_ALERT_MIN_COUNT)
    and ≥ TREND_ALERT_RATIO × the mean of the earlier weeks — that's the
    "dosa – too oily ↑" flag the inbox surfaces."""
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(weeks=weeks)
    rows = (
        (
            await session.execute(
                select(Review).where(Review.created_at >= since, Review.aspects.isnot(None))
            )
        )
        .scalars()
        .all()
    )
    dishes = await _dishes_by_order(session, [r.order_id for r in rows])

    # aspect -> week_start -> count; aspect -> dish counter for the latest week
    counts: dict[str, Counter[str]] = {a: Counter() for a in REVIEW_ASPECTS}
    week_labels = sorted(
        {
            _week_start(datetime.now(UTC).replace(tzinfo=None) - timedelta(weeks=i))
            for i in range(weeks)
        }
    )
    latest_week = week_labels[-1]
    dish_hits: dict[str, Counter[str]] = {a: Counter() for a in REVIEW_ASPECTS}
    for review in rows:
        week = _week_start(review.created_at)
        if week not in week_labels:
            continue
        for entry in review.aspects or []:
            aspect, polarity = entry.get("aspect"), entry.get("sentiment")
            if aspect not in counts or polarity != "NEGATIVE":
                continue
            counts[aspect][week] += 1
            if week == latest_week:
                dish_hits[aspect].update(dishes.get(review.order_id, []))

    out: list[ReviewAspectTrend] = []
    for aspect in REVIEW_ASPECTS:
        points = [
            ReviewTrendPoint(week_start=w, count=counts[aspect].get(w, 0)) for w in week_labels
        ]
        latest = points[-1].count
        earlier = [p.count for p in points[:-1]]
        mean_earlier = sum(earlier) / max(len(earlier), 1)
        alert = latest >= TREND_ALERT_MIN_COUNT and (
            mean_earlier == 0 or latest >= TREND_ALERT_RATIO * mean_earlier
        )
        out.append(
            ReviewAspectTrend(
                aspect=aspect,
                points=points,
                alert=alert,
                top_dishes=[d for d, _ in dish_hits[aspect].most_common(3)] if alert else [],
            )
        )
    return ReviewTrendsOut(weeks=weeks, aspects=out)
