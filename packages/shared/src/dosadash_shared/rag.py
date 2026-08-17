"""RAG contracts (Phase 3): retrieval request/response shapes.

Shared between the AI service (which owns ingestion + hybrid search over
pgvector) and any internal caller (core API, later the order agent).
Structured shapes only — Hard Rule 3.
"""

from pydantic import BaseModel, Field

EMBEDDING_DIM = 1536  # text-embedding-3-small — matches vector(1536) columns

RAG_ANSWER_PROMPT_VERSION = "rag_answer_v1"


class RagSearchRequest(BaseModel):
    """Internal retrieval request. Query text is redacted (Hard Rule 8)
    by the AI service before any provider call."""

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=12)


class RagChunkOut(BaseModel):
    """One retrieved knowledge chunk with provenance for citations."""

    id: int
    doc_path: str
    doc_type: str
    title: str
    heading: str
    content: str
    score: float = Field(ge=0)


class RagSearchResponse(BaseModel):
    query: str
    chunks: list[RagChunkOut]


class RagAnswerRequest(BaseModel):
    """Question → grounded, cited answer (web chat / Telegram / agent tool)."""

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=12)
    session_id: str | None = None
    user_id: str | None = None  # opaque id for tracing — never a phone number


class RagAnswerDraft(BaseModel):
    """The LLM's structured output (Hard Rule 3) — validated, never free-text.

    `used_chunks` are 1-based indexes into the context given to the model;
    the service maps them back to document provenance."""

    answer: str = Field(min_length=1, max_length=1500)
    used_chunks: list[int] = Field(default_factory=list, max_length=12)
    not_found: bool = False


class RagCitation(BaseModel):
    doc_path: str
    title: str
    heading: str


class RagAnswerResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
    not_found: bool
    model: str  # "" when answered without an LLM call (empty retrieval)
    prompt_version: str = RAG_ANSWER_PROMPT_VERSION
    cached: bool = False  # served from the semantic cache (Phase 4)
