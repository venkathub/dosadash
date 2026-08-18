"""Analytics copilot schemas (Phase 5, docs/03 #21).

Structured everywhere (Hard Rule 3): the LLM emits `CopilotDraft` (SQL +
chart intent), the service returns `CopilotAnswer` (validated SQL, rows,
chart spec, provenance). Rows are JSON-safe scalars only.
"""

from typing import Literal

from pydantic import BaseModel, Field

COPILOT_PROMPT_VERSION = "copilot_v1"

CellValue = str | int | float | bool | None


class CopilotChart(BaseModel):
    """How to visualize the result — 'none' for scalar/wide answers."""

    type: Literal["bar", "line", "none"] = "none"
    x: str = ""  # column name for the x axis
    y: str = ""  # numeric column for the y axis


class CopilotDraft(BaseModel):
    """The LLM's structured output — parsed and validated, never free-text."""

    sql: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(min_length=1, max_length=500)
    chart: CopilotChart = CopilotChart()


class CopilotAskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class CopilotAnswer(BaseModel):
    question: str
    sql: str | None = None  # the validated SQL that actually ran
    explanation: str | None = None
    columns: list[str] = []
    rows: list[list[CellValue]] = []
    row_count: int = 0
    truncated: bool = False
    chart: CopilotChart = CopilotChart()
    attempts: int = 1  # 1 = first draft ran; >1 = self-corrections used
    model: str | None = None
    prompt_version: str = COPILOT_PROMPT_VERSION
    error: str | None = None  # set when all attempts failed
