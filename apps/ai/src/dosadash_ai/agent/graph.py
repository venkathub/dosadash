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
LangGraph checkpointing/memory arrives in Phase 6.
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
    turn, model = await structured_completion(
        messages=build_messages(state),
        response_model=AgentTurn,
        trace_name="agent.turn",
        prompt_version=ORDER_AGENT_PROMPT_VERSION,
        session_id=request.session_id,
        user_id=str(request.user_id) if request.user_id is not None else None,
        max_tokens=900,
    )
    return {"turn": turn, "model": model}


def build_response(
    ctx: AgentContext, turn: AgentTurn, model: str, user_message: str = ""
) -> AgentChatResponse:
    """Guardrail + response assembly — shared by the graph and the SSE path."""
    draft, warnings = validate_draft(ctx, turn.draft_items)
    draft, substitution_warnings = drop_substitutions(ctx, user_message, draft)
    warnings.extend(substitution_warnings)
    attempted = tuple(line.item_id for line in turn.draft_items)
    notes = serving_notes(ctx, user_message, attempted)
    reply = turn.reply
    if notes:
        # deterministic availability verdicts (Phase 11) — appended AFTER the
        # model's text so the serving-hours story is always present, always
        # true, and never the model's to hallucinate
        reply = (reply.rstrip() + " " if reply.strip() else "") + " ".join(notes)
    if not ctx.kitchen_open:
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
    return {
        "response": build_response(
            state["ctx"], state["turn"], state["model"], state["request"].message
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
