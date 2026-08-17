"""Eval scoreboard (Phase 4 LLMOps): live eval run history.

/api/v1/admin/eval-runs        — POST (CI ingest, X-Internal-Token)
                               — GET  (admin/owner: scoreboard list)
/api/v1/admin/eval-runs/{id}   — GET  (admin/owner: per-case drill-down)

CI posts run_live_evals.py results after EVERY gate run — including
failing ones: regressions belong on the scoreboard too. Ingest uses the
shared internal token (same pattern as bot→api) because CI is a service,
not a staff user; reads are RBAC-gated like the rest of the backoffice.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import EvalRun, User
from dosadash_api.db.session import get_session
from dosadash_shared import EvalRunDetailOut, EvalRunIn, EvalRunOut, Role

router = APIRouter(prefix="/api/v1/admin/eval-runs", tags=["admin:evals"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


def _require_internal_token(request: Request) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Eval ingest not configured")
    provided = request.headers.get("X-Internal-Token", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("", response_model=EvalRunOut, status_code=201)
async def ingest_eval_run(body: EvalRunIn, request: Request, session: SessionDep) -> EvalRun:
    """Record one live eval run (CI → api, X-Internal-Token)."""
    _require_internal_token(request)
    metrics = body.metrics
    for key in ("order_accuracy", "tool_correctness"):
        if key not in metrics:
            raise HTTPException(status_code=422, detail=f"metrics missing {key!r}")
    run = EvalRun(
        ran_at=body.ran_at,
        git_sha=body.git_sha,
        trigger=body.trigger,
        cases=body.cases,
        order_accuracy=metrics["order_accuracy"],
        tool_correctness=metrics["tool_correctness"],
        guardrail_bypasses=int(metrics.get("guardrail_bypasses", 0)),
        guardrail_cases=int(metrics.get("guardrail_cases", 0)),
        tone=metrics.get("tone"),
        gates_passed=body.gates_passed,
        failures=body.failures,
        case_reports=[report.model_dump() for report in body.case_reports],
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.get("", response_model=list[EvalRunOut])
async def list_eval_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    admin: User = AdminUser,
) -> list[EvalRun]:
    """Latest runs first — the admin scoreboard."""
    rows = await session.scalars(select(EvalRun).order_by(EvalRun.ran_at.desc()).limit(limit))
    return list(rows)


@router.get("/{run_id}", response_model=EvalRunDetailOut)
async def get_eval_run(run_id: int, session: SessionDep, admin: User = AdminUser) -> EvalRun:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run
