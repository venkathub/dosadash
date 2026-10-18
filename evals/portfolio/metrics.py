"""Scorers shared by synthetic demos and permissioned multimodal datasets."""

import re
from collections import Counter
from decimal import Decimal

from dosadash_ai.invoice.verify import verify
from dosadash_shared import InvoiceExtraction


def words(text):
    return re.findall(r"\w+", text.casefold())


def edit_distance(reference, hypothesis):
    row = list(range(len(hypothesis) + 1))
    for i, token in enumerate(reference, 1):
        following = [i]
        for j, candidate in enumerate(hypothesis, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (token != candidate)))
        row = following
    return row[-1]


def audio_metrics(reference, hypothesis, required_terms):
    expected, actual = words(reference), words(hypothesis)
    if not expected:
        raise ValueError("reference transcript must not be empty")
    normalized = " ".join(actual)
    return {
        "word_error_rate": edit_distance(expected, actual) / len(expected),
        "reference_words": len(expected),
        "edit_distance": edit_distance(expected, actual),
        "command_term_recall": sum(" ".join(words(t)) in normalized for t in required_terms)
        / len(required_terms)
        if required_terms
        else None,
    }


def invoice_metrics(reference, hypothesis):
    expected = InvoiceExtraction.model_validate(reference)
    actual = InvoiceExtraction.model_validate(hypothesis)

    def fields(extraction):
        def number(value):
            return str(Decimal(value).normalize()) if value is not None else None

        rows = Counter()
        for line in extraction.lines:
            rows[
                (
                    " ".join(words(line.name)),
                    number(line.qty),
                    " ".join(words(line.unit or "")),
                    number(line.unit_price),
                    number(line.amount),
                )
            ] += 1
        return rows

    a, b = fields(expected), fields(actual)
    correct = sum((a & b).values())
    precision = correct / sum(b.values())
    recall = correct / sum(a.values())
    header_fields = ("supplier_name", "invoice_number", "invoice_date", "total")
    matches = sum(getattr(expected, k) == getattr(actual, k) for k in header_fields)
    _, arithmetic_ok, _ = verify(actual)
    return {
        "line_precision": precision,
        "line_recall": recall,
        "line_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        "header_accuracy": matches / len(header_fields),
        "arithmetic_ok": arithmetic_ok,
        "exact_match": a == b and matches == len(header_fields),
    }
