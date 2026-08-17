"""Live tone eval — the project's first LLM-as-judge suite (Phase 4).

A judge model (litellm chain, Hard Rules 1/3/6) scores agent replies
against evals/rubrics/tone_v1.md on a 1–5 scale: warmth, brevity,
language mirroring, graceful refusals, no over-promising. Content
accuracy is order_accuracy's job — this suite only scores how replies
read.

Judged subset: conversational tags (basic, confirm, factual, meal_period,
preference) plus refusal surfaces (sold_out, kitchen_paused), capped per
tag to keep judge cost bounded.

    PASS_THRESHOLD=0.8 uv run python evals/suites/tone_judge_eval.py

Metric = mean(score) / 5. Gate: >= 0.8 (i.e. average >= 4/5).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import CaseResult, load_cases, run_all  # noqa: E402
from pydantic import BaseModel, Field

from dosadash_ai.db import get_sessionmaker  # noqa: E402
from dosadash_ai.llm import structured_completion  # noqa: E402
from dosadash_ai.redaction import redact_phones  # noqa: E402

RUBRIC = Path(__file__).resolve().parents[1] / "rubrics" / "tone_v1.md"
TONE_PROMPT_VERSION = "tone_judge_v1"
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))
TONE_TAGS = {
    "basic",
    "confirm",
    "factual",
    "meal_period",
    "preference",
    "sold_out",
    "kitchen_paused",
}
MAX_PER_TAG = 3  # bound judge cost; coverage over volume


class ToneVerdict(BaseModel):
    """Structured judge output (Hard Rule 3 applies to judges too)."""

    score: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1, max_length=300)


def select_tone_cases(cases: list[dict]) -> list[dict]:
    """First MAX_PER_TAG cases per tone-relevant tag (deduplicated, ordered)."""
    picked: dict[str, dict] = {}
    per_tag: dict[str, int] = dict.fromkeys(TONE_TAGS, 0)
    for case in cases:
        for tag in case.get("tags", []):
            if tag in TONE_TAGS and per_tag[tag] < MAX_PER_TAG and case["id"] not in picked:
                picked[case["id"]] = case
                per_tag[tag] += 1
                break
    return list(picked.values())


async def judge_reply(case: dict, reply: str) -> ToneVerdict:
    verdict, _model = await structured_completion(
        messages=[
            {"role": "system", "content": RUBRIC.read_text()},
            {
                "role": "user",
                "content": (
                    f"Customer message ({case['language']}): "
                    f"{redact_phones(case['message'])}\n\n"
                    f"Assistant reply: {redact_phones(reply)}\n\n"
                    'Score per the rubric. JSON only: {"score": <1-5>, "reason": "..."}'
                ),
            },
        ],
        response_model=ToneVerdict,
        trace_name="eval.tone_judge",
        prompt_version=TONE_PROMPT_VERSION,
        session_id=f"eval:{case['id']}",
        max_tokens=200,
    )
    return verdict


async def report(results: list[CaseResult]) -> float:
    scores: list[int] = []
    for result in results:
        verdict = await judge_reply(result.case, result.response.reply)
        scores.append(verdict.score)
        print(
            f"[{verdict.score}/5] {result.case['id']} ({result.case['language']}): {verdict.reason}"
        )
        if verdict.score <= 2:
            print(f"        reply: {result.response.reply[:120]!r}")
    mean = sum(scores) / len(scores)
    rate = mean / 5
    print(f"\ntone (LLM-as-judge, {TONE_PROMPT_VERSION}): mean {mean:.2f}/5 = {rate:.0%}")
    print(f"threshold: {PASS_THRESHOLD:.0%}")
    return rate


async def run() -> int:
    cases = select_tone_cases(load_cases())
    print(f"judging {len(cases)} cases (tags capped at {MAX_PER_TAG} each)\n")
    async with get_sessionmaker()() as session:
        results = await run_all(session, cases)
    return 0 if await report(results) >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # Hard Rule 6
    sys.exit(asyncio.run(run()))
