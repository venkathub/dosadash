"""SSE turn streaming: reply tokens now, validated draft at the end.

The agent's output is one JSON object (Hard Rule 3), so we stream the raw
JSON from the primary model and incrementally extract the `"reply"` string
value as it grows — the customer watches the answer type out while the
draft fields are still being generated. When the stream completes, the
full JSON is validated (`AgentTurn`) and the Hard Rule 2 guardrail runs
exactly as in the non-streaming path; the `final` event carries the
authoritative `AgentChatResponse`. Deltas are cosmetic; the final event is
the contract.

Any streaming failure (connection, invalid JSON) falls back to the
non-streaming chain — clients then simply get a `final` without deltas.

Events (SSE `data:` payloads):
    {"type": "delta", "text": "..."}       incremental reply text
    {"type": "final", "data": {...}}       AgentChatResponse (always last on success)
    {"type": "error", "detail": "..."}     whole chain failed
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.graph import (
    AgentState,
    _load_context,
    _retrieve_knowledge,
    build_messages,
    build_response,
)
from dosadash_ai.config import get_settings
from dosadash_ai.llm.client import LLMError, stream_text_completion, structured_completion
from dosadash_shared import ORDER_AGENT_PROMPT_VERSION, AgentChatRequest, AgentTurn

logger = logging.getLogger(__name__)


class ReplyExtractor:
    """Incrementally extracts the string value of the top-level "reply" key
    from a JSON document arriving in arbitrary chunks (escape-aware,
    including split \\uXXXX sequences). Best-effort by design: the final
    validated object is authoritative, deltas are display-only."""

    _KEY = '"reply"'

    def __init__(self) -> None:
        self._buffer = ""
        self._value_start: int | None = None
        self._pos: int | None = None  # next unscanned index in the value
        self._done = False

    def feed(self, piece: str) -> str:
        if self._done:
            return ""
        self._buffer += piece
        if self._value_start is None and not self._locate_value():
            return ""
        return self._scan()

    def _locate_value(self) -> bool:
        key_at = self._buffer.find(self._KEY)
        if key_at == -1:
            return False
        i = key_at + len(self._KEY)
        while i < len(self._buffer) and self._buffer[i] in " \t\r\n:":
            if self._buffer[i] == '"':
                break
            i += 1
        if i >= len(self._buffer) or self._buffer[i] != '"':
            return False
        self._value_start = self._pos = i + 1
        return True

    def _scan(self) -> str:
        out: list[str] = []
        i = self._pos
        while i < len(self._buffer):
            ch = self._buffer[i]
            if ch == '"':
                self._done = True
                i += 1
                break
            if ch == "\\":
                if i + 1 >= len(self._buffer):
                    break  # escape split across chunks — wait for more
                nxt = self._buffer[i + 1]
                if nxt == "u":
                    if i + 6 > len(self._buffer):
                        break  # incomplete \uXXXX
                    out.append(chr(int(self._buffer[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        self._pos = i
        return "".join(out)


async def stream_turn(
    session: AsyncSession, request: AgentChatRequest
) -> AsyncIterator[dict[str, Any]]:
    """Yield delta/final/error events for one agent turn (see module doc)."""
    state: AgentState = {"session": session, "request": request}
    state.update(await _load_context(state))
    state.update(await _retrieve_knowledge(state))
    messages = build_messages(state)

    turn: AgentTurn | None = None
    model = get_settings().llm_models[0]
    extractor = ReplyExtractor()
    raw: list[str] = []
    try:
        async for piece in stream_text_completion(
            messages=messages,
            trace_name="agent.turn.stream",
            prompt_version=ORDER_AGENT_PROMPT_VERSION,
            session_id=request.session_id,
            user_id=str(request.user_id) if request.user_id is not None else None,
        ):
            raw.append(piece)
            delta = extractor.feed(piece)
            if delta:
                yield {"type": "delta", "text": delta}
        turn = AgentTurn.model_validate_json("".join(raw))
    except LLMError:
        logger.warning("agent stream failed — falling back to non-streaming chain")
    except ValidationError:
        logger.warning("agent stream produced invalid JSON — falling back")

    if turn is None:
        try:
            turn, model = await structured_completion(
                messages=messages,
                response_model=AgentTurn,
                trace_name="agent.turn",
                prompt_version=ORDER_AGENT_PROMPT_VERSION,
                session_id=request.session_id,
                user_id=str(request.user_id) if request.user_id is not None else None,
                max_tokens=900,
            )
        except LLMError as exc:
            yield {"type": "error", "detail": f"LLM chain failed: {exc}"}
            return

    response = build_response(state["ctx"], turn, model)
    yield {"type": "final", "data": response.model_dump(mode="json")}
