"""LLM access layer (litellm-only, Hard Rule 1)."""

from dosadash_ai.llm.client import LLMError, configure_tracing, structured_completion

__all__ = ["LLMError", "configure_tracing", "structured_completion"]
