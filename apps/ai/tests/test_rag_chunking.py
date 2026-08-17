"""Chunker unit tests + contract check over the real knowledge/ corpus."""

import pytest
from conftest import KNOWLEDGE_DIR

from dosadash_ai.rag.chunking import (
    KnowledgeFormatError,
    chunk_document,
    load_knowledge_dir,
    parse_front_matter,
)

SAMPLE = """---
title: Sample Guide
doc_type: menu_guide
tags: [dosa, sample]
---

# Sample Guide

Intro paragraph before any section.

## Crisp Dosas

### Masala Dosa

Potato filling, mustard tempering.

### Ghee Roast

Roasted in ghee.

## Ordering

Order via the website.
"""


def test_front_matter_parsed():
    meta, body = parse_front_matter(SAMPLE, doc_path="sample.md")
    assert meta.title == "Sample Guide"
    assert meta.doc_type == "menu_guide"
    assert meta.tags == ("dosa", "sample")
    assert body.lstrip().startswith("# Sample Guide")


def test_missing_front_matter_rejected():
    with pytest.raises(KnowledgeFormatError, match="front-matter"):
        parse_front_matter("# No front matter\n\ntext\n", doc_path="bad.md")


def test_heading_chunks_and_breadcrumbs():
    chunks = chunk_document("sample.md", SAMPLE)
    breadcrumbs = [c.heading for c in chunks]
    assert breadcrumbs == [
        "Sample Guide",  # preamble
        "Sample Guide › Crisp Dosas › Masala Dosa",
        "Sample Guide › Crisp Dosas › Ghee Roast",
        "Sample Guide › Ordering",
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]
    assert chunks[1].content == "Potato filling, mustard tempering."


def test_content_hash_changes_with_content():
    a = chunk_document("sample.md", SAMPLE)
    b = chunk_document("sample.md", SAMPLE.replace("Potato", "Aloo"))
    assert a[1].content_hash != b[1].content_hash
    assert a[2].content_hash == b[2].content_hash  # untouched section stable


def test_empty_doc_rejected():
    with pytest.raises(KnowledgeFormatError, match="no content"):
        chunk_document("empty.md", "---\ntitle: T\ndoc_type: faq\n---\n\n# T\n")


# ------------------------------------------------- real corpus contract gate


def test_real_knowledge_corpus_chunks_cleanly():
    """Every committed knowledge doc obeys the authoring contract."""
    chunks = load_knowledge_dir(KNOWLEDGE_DIR)
    assert len(chunks) >= 30, "suspiciously small corpus"
    doc_types = {c.doc_type for c in chunks}
    assert doc_types == {"allergen_guide", "menu_guide", "faq", "policy"}
    assert all(c.heading and c.content for c in chunks)
    paths = {c.doc_path for c in chunks}
    assert "allergens.md" in paths
    assert "faq.md" in paths
    assert "policies.md" in paths
    assert not any("README" in p for p in paths)
