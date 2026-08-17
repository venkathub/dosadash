"""Hard Rule 8 — phone redaction before LLM calls and logs."""

from dosadash_ai.redaction import redact_phones


def test_indian_mobile_formats_redacted():
    assert redact_phones("call me at +91 98765 43210") == "call me at [phone]"
    assert redact_phones("mera number 09876543210 hai") == "mera number [phone] hai"
    assert redact_phones("98765-43210 pe bhejo") == "[phone] pe bhejo"


def test_international_redacted():
    assert "[phone]" in redact_phones("reach me on +1-555-010-4477 tonight")


def test_business_numbers_survive():
    assert redact_phones("2 masala dosas at ₹120 each") == "2 masala dosas at ₹120 each"
    assert redact_phones("deliver to 600017 please") == "deliver to 600017 please"
    assert redact_phones("order #4512 total 226.80") == "order #4512 total 226.80"


def test_multiple_phones():
    out = redact_phones("primary +91 9876543210, alt 044 2498 1234")
    assert out.count("[phone]") == 2
