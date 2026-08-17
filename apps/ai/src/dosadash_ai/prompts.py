"""Prompt loading — prompts are versioned files in the repo (CLAUDE.md
conventions) and tagged in Langfuse via their filename stem (e.g.
`nutrition_v1`). Bump the version by adding a new file, never by editing
an old one silently."""

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    """Load a prompt by versioned name, e.g. load_prompt("nutrition_v1")."""
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
