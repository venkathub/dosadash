"""PII redaction (Hard Rule 8): strip phone numbers before LLM calls/logs.

Indian mobile formats and general international numbers: +91 98765 43210,
09876543210, 98765-43210, +1-555-0100 ... — anything that looks like an
8-to-14-digit phone with optional separators becomes [phone].
"""

import re

_PHONE_RE = re.compile(
    r"""
    (?<![\d/])                # not preceded by digit or / (avoid dates/paths)
    \+?\d{1,3}?               # optional country code
    [\s.-]?
    (?:\d[\s.-]?){7,12}\d     # 8–14 digits with optional separators
    (?![\d/])
    """,
    re.VERBOSE,
)

REDACTED = "[phone]"


def redact_phones(text: str) -> str:
    """Replace phone-number-looking sequences with [phone].

    Prices (₹120), pincodes (600001), and quantities survive: they are under
    8 digits. Better to over-redact a long number than leak a phone to a
    provider or a log line.
    """
    return _PHONE_RE.sub(REDACTED, text)
