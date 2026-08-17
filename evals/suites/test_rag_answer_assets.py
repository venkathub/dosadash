"""Key-free asset gates for the RAG answer golden set + prompt coherence.

Live grounded-answer scoring lives in rag_answer_eval.py (needs keys and an
ingested DB); these gates run in CI (Hard Rule 5).
"""

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "rag_answers.jsonl"
KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"

REQUIRED_FIELDS = {
    "id",
    "language",
    "question",
    "must_contain_any",
    "must_not_contain",
    "expect_citation_docs",
    "expect_not_found",
}
LANGUAGES = {"en", "hinglish", "tanglish"}


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def test_cases_have_required_fields():
    for case in load_cases():
        missing = REQUIRED_FIELDS - set(case)
        assert not missing, f"{case.get('id')}: missing {missing}"
        assert case["language"] in LANGUAGES, case["id"]


def test_ids_unique_and_substantial():
    ids = [c["id"] for c in load_cases()]
    assert len(ids) >= 10
    assert len(ids) == len(set(ids))


def test_citation_docs_exist():
    for case in load_cases():
        for doc in case["expect_citation_docs"]:
            assert (KNOWLEDGE / doc).is_file(), f"{case['id']}: {doc} not in knowledge/"


def test_all_three_languages_and_adversarial_covered():
    cases = load_cases()
    assert {c["language"] for c in cases} == LANGUAGES
    assert any(c["expect_not_found"] for c in cases), "need refusal cases"
    assert any("ignore" in c["question"].lower() for c in cases), "need an injection case"


def test_not_found_cases_expect_no_citations():
    for case in load_cases():
        if case["expect_not_found"]:
            assert case["expect_citation_docs"] == [], case["id"]


def test_prompt_file_has_guardrail_rules():
    """The versioned prompt must keep its grounding/injection/output rules."""
    from dosadash_ai.prompts import load_prompt
    from dosadash_shared import RAG_ANSWER_PROMPT_VERSION

    prompt = load_prompt(RAG_ANSWER_PROMPT_VERSION)
    assert "ONLY" in prompt  # grounding
    assert "not_found" in prompt and "used_chunks" in prompt  # output contract
    assert "DATA, not instructions" in prompt  # injection guardrail
    assert "language" in prompt  # EN/Hinglish/Tanglish mirroring
