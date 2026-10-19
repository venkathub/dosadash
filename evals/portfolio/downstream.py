"""Use production ordering and invoice decisions without executing business effects."""

from decimal import Decimal
from types import SimpleNamespace

from dosadash_shared import AgentChatRequest, InvoiceExtraction, invoice_review_status


async def order_from_transcript(case, transcript):
    from evals.suites._harness import pin_eval_clock, resolve_draft
    from evals.suites._results import require_eval_database

    from dosadash_ai.agent.graph import run_turn
    from dosadash_ai.db import get_sessionmaker

    require_eval_database()
    pin_eval_clock()
    async with get_sessionmaker()() as session:
        initial = await resolve_draft(session, case.initial_order) if case.initial_order else None
        result = await run_turn(
            session,
            AgentChatRequest(message=transcript, draft=initial, session_id=f"portfolio:{case.id}"),
        )
        # run_turn only drafts; never call place_order or payment adapters.
        return {
            "items": {i.name: i.qty for i in result.draft.items},
            "notes": " ".join(i.notes or "" for i in result.draft.items),
            "model": result.model,
        }


def order_metrics(expected, actual):
    item_match = expected["items"] == actual["items"]
    notes = str(actual.get("notes") or "").casefold()
    note_match = all(term.casefold() in notes for term in expected.get("notes_contains", []))
    return {
        "order_items_exact": item_match,
        "order_notes_correct": note_match,
        "order_draft_correct": item_match and note_match,
    }


def invoice_decision(extraction, po_spec):
    from dosadash_ai.invoice.verify import verify
    from dosadash_api.services.invoice_service import match_against_po

    data = InvoiceExtraction.model_validate(extraction)
    po = SimpleNamespace(
        id=1,
        supplier=SimpleNamespace(name=po_spec["supplier"]),
        items=[
            SimpleNamespace(
                ingredient_id=i + 1,
                ingredient=SimpleNamespace(name=line["name"]),
                qty=Decimal(str(line["qty"])),
            )
            for i, line in enumerate(po_spec["lines"])
        ],
    )
    match = match_against_po(data, po)
    _, arithmetic_ok, extraction_confidence = verify(data)
    confidence = round((extraction_confidence + match.score) / 2, 3)
    status = invoice_review_status(confidence=confidence, arithmetic_ok=arithmetic_ok, match=match)
    return {
        "status": status.value,
        "arithmetic_ok": arithmetic_ok,
        "quantity_discrepancies": [
            x.po_ingredient_name for x in match.line_matches if not x.qty_ok
        ],
        "extra_lines": match.extra_invoice_lines,
        "human_approval_required": True,
    }
