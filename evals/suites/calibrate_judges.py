"""Run provisional judge calibration fixtures; never equate authored labels with human review."""

import asyncio
import json

from _results import load_jsonl
from faithfulness_judge import judge_answer
from tone_judge_eval import judge_reply

from dosadash_ai.llm.eval_telemetry import active_case


async def run(*, case_timeout: float = 120) -> dict:
    reports = []

    async def faithfulness(case):
        problems, detail = await judge_answer(
            case["question"],
            case["answer"],
            case["evidence"],
            case["citations"],
            not_found=case["not_found"],
            case_id=f"calibration:{case['id']}",
        )
        return {"agrees": (not problems) == case["expect_pass"], "problems": problems, **detail}

    async def tone(case):
        verdict = await judge_reply(case, case["reply"])
        return {
            "agrees": case["score_min"] <= verdict.score <= case["score_max"],
            "verdict": verdict.model_dump(),
        }

    for name, evaluate in (("faithfulness", faithfulness), ("tone", tone)):
        for case in load_jsonl(f"{name}_calibration.jsonl"):
            report = {"id": f"{name}:{case['id']}", "human_reviewed": case["human_reviewed"]}
            token = active_case.set(f"calibration:{report['id']}")
            try:
                report.update(await asyncio.wait_for(evaluate(case), timeout=case_timeout))
                report["status"] = "pass" if report["agrees"] else "fail"
            except Exception as exc:  # noqa: BLE001 — preserve errors and continue calibration
                report.update(status="error", agrees=False, error=type(exc).__name__)
                if hasattr(exc, "eval_detail"):
                    report["error_detail"] = exc.eval_detail
            finally:
                active_case.reset(token)
            reports.append(report)
    all_reviewed = bool(reports) and all(c["human_reviewed"] for c in reports)
    return {
        "cases": reports,
        "errors": sum(c["status"] == "error" for c in reports),
        "human_reviewed_cases": sum(c["human_reviewed"] for c in reports),
        "all_samples_human_reviewed": all_reviewed,
        "human_calibration_complete": all_reviewed and all(c["agrees"] for c in reports),
        "authored_label_agreement": sum(c["agrees"] for c in reports) / len(reports),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
