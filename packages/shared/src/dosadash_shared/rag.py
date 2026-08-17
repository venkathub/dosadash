"""RAG contracts (Phase 3): retrieval request/response shapes.

Shared between the AI service (which owns ingestion + hybrid search over
pgvector) and any internal caller (core API, later the order agent).
Structured shapes only — Hard Rule 3.
"""

from pydantic import BaseModel, Field

EMBEDDING_DIM = 1536  # text-embedding-3-small — matches vector(1536) columns


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
