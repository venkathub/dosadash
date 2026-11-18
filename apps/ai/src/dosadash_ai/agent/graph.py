"""The order-agent LangGraph: one conversational turn per invocation.

    load_context → retrieve_knowledge → llm_turn → validate_draft

- load_context: fresh DB snapshot (menu, 86/pause, hours, preferences) —
  the agent can never drift from business state (Hard Rule 4).
- retrieve_knowledge: best-effort hybrid RAG for factual grounding; a
  retrieval failure degrades answers, never ordering.
- llm_turn: one structured completion (AgentTurn, Hard Rule 3) via the
  litellm chain, phone-redacted (Hard Rule 8), Langfuse-traced (Hard Rule 6).
- validate_draft: the Hard Rule 2 guardrail — DB-validates every item_id,
  merges duplicates, rewrites names/prices from the DB, gates
  ready_to_place, surfaces warnings.

The graph is stateless across turns (adapters own history + last draft);
Order history is adapter-owned; inventory recovery uses a separate relational workflow.
"""

import json
import logging
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.context import (
    AgentContext,
    load_context,
    memory_payload,
    menu_payload,
    prefs_payload,
)
from dosadash_ai.agent.guardrail import (
    drop_substitutions,
    gate_ready,
    reply_draft_contradictions,
    serving_notes,
    validate_draft,
)
from dosadash_ai.llm.client import LLMError, embed_texts, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_ai.rag.search import hybrid_search
from dosadash_ai.redaction import redact_phones
from dosadash_shared import (
    ORDER_AGENT_PROMPT_VERSION,
    AgentChatRequest,
    AgentChatResponse,
    AgentTurn,
)

logger = logging.getLogger(__name__)

_RETRIEVE_TOP_K = 3


class AgentState(TypedDict, total=False):
    session: AsyncSession
    request: AgentChatRequest
    ctx: AgentContext
    knowledge: list[dict[str, Any]]
    turn: AgentTurn
    model: str
    response: AgentChatResponse


async def _load_context(state: AgentState) -> dict[str, Any]:
    ctx = await load_context(state["session"], state["request"].user_id)
    return {"ctx": ctx}


async def _retrieve_knowledge(state: AgentState) -> dict[str, Any]:
    query = redact_phones(state["request"].message)
    try:
        [embedding] = await embed_texts([query], trace_name="agent.retrieve.embed")
        scored = await hybrid_search(state["session"], query, embedding, top_k=_RETRIEVE_TOP_K)
    except LLMError:
        logger.warning("agent: knowledge retrieval unavailable, continuing without")
        return {"knowledge": []}
    return {
        "knowledge": [
            {"id": i, "heading": s.chunk.heading, "content": s.chunk.content}
            for i, s in enumerate(scored, start=1)
        ]
    }


def build_messages(state: AgentState) -> list[dict[str, str]]:
    """Prompt-caching-friendly layout (docs/05: provider prompt caching).

    The prefix [static system prompt, MENU context] is byte-stable across
    turns and users (deterministic serialization; menu changes only on
    admin edits), so OpenAI-style automatic prefix caching hits on every
    turn. Volatile per-turn state (kitchen, prefs, knowledge, draft) comes
    after the stable prefix, followed by redacted history + message.
    """
    request = state["request"]
    ctx = state["ctx"]
    # ORDERABLE dishes only (Phase 11): every prompt variant that exposed
    # off-window items or serving-hours text to gpt-4o-mini caused
    # hallucinated refusals of on-menu dishes (measured in the live gate).
    # The serving-window story is appended deterministically in
    # build_response (guardrail.serving_notes) instead.
    menu_json = json.dumps({"menu": menu_payload(ctx)}, ensure_ascii=False, sort_keys=True)
    state_payload = {
        "kitchen": {"open": ctx.kitchen_open, "paused": ctx.kitchen_paused},
        "preferences": prefs_payload(ctx),
        "knowledge": state.get("knowledge", []),
        "memory": memory_payload(ctx),  # Phase 6: "my usual" + episodes
        "current_draft": request.draft.model_dump(mode="json") if request.draft else None,
    }
    return [
        {"role": "system", "content": load_prompt(ORDER_AGENT_PROMPT_VERSION)},
        {"role": "system", "content": "MENU: " + menu_json},
        {
            "role": "system",
            "content": "STATE: " + json.dumps(state_payload, ensure_ascii=False, sort_keys=True),
        },
        *[{"role": m.role, "content": redact_phones(m.content)} for m in request.history],
        {"role": "user", "content": redact_phones(request.message)},
    ]


async def _llm_turn(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    messages = build_messages(state)
    turn, model = await structured_completion(
        messages=messages,
        response_model=AgentTurn,
        trace_name="agent.turn",
        prompt_version=ORDER_AGENT_PROMPT_VERSION,
        session_id=request.session_id,
        user_id=str(request.user_id) if request.user_id is not None else None,
        max_tokens=900,
    )
    # One-round self-correction (copilot precedent): if the reply refused —
    # or promised but didn't draft — an ORDERABLE dish the customer asked
    # for by name, tell the model exactly which dishes ARE on the menu and
    # let it redo the turn once. Deterministic detection, narrow trigger;
    # a failed retry keeps the original turn (never a 5xx).
    contradicted = reply_draft_contradictions(state["ctx"], request.message, turn)
    if contradicted:
        logger.info("agent: self-correcting wrongly-refused dishes %s", contradicted)
        correction_items = [
            {"item_id": item.id, "name": item.name}
            for item in state["ctx"].items.values()
            if item.name in contradicted and item.orderable
        ]
        try:
            retry_turn, retry_model = await structured_completion(
                messages=[
                    *messages,
                    {"role": "assistant", "content": turn.model_dump_json()},
                    {
                        "role": "system",
                        "content": (
                            load_prompt("order_correction_v1")
                            + "\nCORRECTION_ITEMS: "
                            + json.dumps(correction_items, ensure_ascii=False)
                        ),
                    },
                ],
                response_model=AgentTurn,
                trace_name="agent.turn.self_correct",
                prompt_version="order_correction_v1",
                session_id=request.session_id,
                user_id=str(request.user_id) if request.user_id is not None else None,
                max_tokens=900,
            )
        except LLMError:
            logger.warning("agent: self-correction retry failed, keeping original turn")
        else:
            if not reply_draft_contradictions(state["ctx"], request.message, retry_turn):
                turn, model = retry_turn, retry_model
    return {"turn": turn, "model": model}


def build_response(
    ctx: AgentContext,
    turn: AgentTurn,
    model: str,
    user_message: str = "",
    prior_ids: frozenset[int] = frozenset(),
) -> AgentChatResponse:
    """Guardrail + response assembly — shared by the graph and the SSE path."""
    draft, warnings = validate_draft(ctx, turn.draft_items)
    draft, substitution_warnings = drop_substitutions(ctx, user_message, draft, prior_ids)
    warnings.extend(substitution_warnings)
    attempted = tuple(line.item_id for line in turn.draft_items)
    notes = serving_notes(ctx, user_message, attempted)
    reply = turn.reply
    removed_ids = set(attempted) - {line.item_id for line in draft.items}
    if removed_ids or (notes and draft.items):
        # The model's original reply may promise a removed item or an invalid
        # subtotal. Rebuild from validated state instead of appending a contradiction.
        explanation = " ".join(notes) or "Some requested items aren't available right now."
        summary = ", ".join(f"{line.qty}× {line.name}" for line in draft.items)
        language = turn.reply_language
        phrases = {
            "en": ("Sorry,", "Your order has", "Would you like something else?"),
            "hinglish": ("Maaf kijiye,", "Aapke order mein", "Kuch aur chahiye?"),
            "tanglish": ("Sorry,", "Unga order-la", "Vera edhaavadhu venuma?"),
            "ta": ("மன்னிக்கவும்,", "உங்கள் ஆர்டரில்", "வேறு ஏதாவது வேண்டுமா?"),
        }
        apology, order_prefix, offer = phrases[language]
        if language == "hinglish":
            explanation = explanation.replace("is sold out right now", "abhi sold out hai")
        elif language == "tanglish":
            explanation = explanation.replace("is sold out right now", "ippo sold out")
        elif language == "ta":
            explanation = explanation.replace("is sold out right now", "இப்போது கிடைக்கவில்லை")
        reply = f"{apology} {explanation}"
        if summary:
            reply += f" {order_prefix} {summary} (subtotal ₹{draft.subtotal:.2f})."
        if ctx.kitchen_open:
            reply += f" {offer}"

    elif notes:
        # deterministic availability verdicts (Phase 11) — appended AFTER the
        # model's text so the serving-hours story is always present, always
        # true, and never the model's to hallucinate
        reply = (reply.rstrip() + " " if reply.strip() else "") + " ".join(notes)
    if not ctx.kitchen_open:
        # Availability and acceptance are business facts. Do not leave a model's
        # "order placed" promise in the reply when the server refuses checkout.
        status = "paused" if ctx.kitchen_paused else "closed"
        closed_replies = {
            "en": (
                f"Sorry, the kitchen is currently {status}, so orders cannot be placed. "
                "You can browse the menu and order when the kitchen reopens."
            ),
            "hinglish": (
                "Maaf kijiye, kitchen abhi band hai, isliye abhi order place nahi ho sakta. "
                "Menu dekh sakte hain; kitchen khulne par order kijiye."
            ),
            "tanglish": (
                "Sorry, kitchen ippo moodiruku; ippo order panna mudiyadhu. "
                "Menu paarkalaam; kitchen thirandha piragu order pannunga."
            ),
            "ta": (
                "மன்னிக்கவும், சமையலறை இப்போது மூடப்பட்டுள்ளது; ஆர்டர் செய்ய முடியாது. "
                "மெனுவைப் பார்க்கலாம்; சமையலறை திறந்த பிறகு ஆர்டர் செய்யுங்கள்."
            ),
        }
        reply = closed_replies[turn.reply_language]
        warnings.append("The kitchen is currently closed — orders cannot be placed.")
    return AgentChatResponse(
        reply=reply,
        draft=draft,
        ready_to_place=gate_ready(ctx, draft, turn.ready_to_place),
        warnings=warnings,
        kitchen_open=ctx.kitchen_open,
        model=model,
    )


async def _validate_draft(state: AgentState) -> dict[str, Any]:
    request = state["request"]
    prior = frozenset(i.item_id for i in request.draft.items) if request.draft else frozenset()
    return {
        "response": build_response(
            state["ctx"], state["turn"], state["model"], request.message, prior
        )
    }


@lru_cache
def get_graph() -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("load_context", _load_context)
    graph.add_node("retrieve_knowledge", _retrieve_knowledge)
    graph.add_node("llm_turn", _llm_turn)
    graph.add_node("validate_draft", _validate_draft)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "llm_turn")
    graph.add_edge("llm_turn", "validate_draft")
    graph.add_edge("validate_draft", END)
    return graph.compile()


async def run_turn(session: AsyncSession, request: AgentChatRequest) -> AgentChatResponse:
    """Run one agent turn. May raise LLMError when the whole chain fails."""
    state = await get_graph().ainvoke({"session": session, "request": request})
    return state["response"]
