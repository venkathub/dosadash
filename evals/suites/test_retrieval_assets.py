"""Key-free asset gates for the retrieval golden set (runs in CI).

Live hit-rate scoring lives in retrieval_eval.py (needs provider keys and
an ingested DB); these gates keep the golden set itself honest.
"""

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "retrieval.jsonl"
KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"

REQUIRED_FIELDS = {"id", "language", "query", "expect_docs"}
LANGUAGES = {"en", "hinglish", "tanglish"}


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def test_golden_set_is_substantial():
    assert len(load_cases()) >= 12


def test_cases_have_required_fields():
    for case in load_cases():
        missing = REQUIRED_FIELDS - set(case)
        assert not missing, f"{case.get('id')}: missing {missing}"
        assert case["language"] in LANGUAGES, case["id"]
        assert case["query"].strip(), case["id"]
        assert case["expect_docs"], case["id"]


def test_ids_unique():
    ids = [c["id"] for c in load_cases()]
    assert len(ids) == len(set(ids))


def test_expected_docs_exist_in_knowledge():
    for case in load_cases():
        for doc in case["expect_docs"]:
            assert (KNOWLEDGE / doc).is_file(), f"{case['id']}: {doc} not in knowledge/"


def test_all_three_languages_covered():
    """Domain rule: eval sets cover EN, Hinglish, and Tanglish."""
    seen = {c["language"] for c in load_cases()}
    assert seen == LANGUAGES


def test_expected_docs_chunk_cleanly():
    """Every referenced doc obeys the knowledge authoring contract."""
    from dosadash_ai.rag.chunking import chunk_document

    for doc in {d for c in load_cases() for d in c["expect_docs"]}:
        chunks = chunk_document(doc, (KNOWLEDGE / doc).read_text())
        assert chunks, doc
