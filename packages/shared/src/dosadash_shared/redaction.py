"""PII redaction (Hard Rule 8): strip phone numbers before text leaves us.

Shared twin of apps/ai/src/dosadash_ai/redaction.py — the api needs the
same regex before feedback text is stored/mirrored to GitHub, and apps/api
must not import apps/ai. Keep the two in byte-sync; converging apps/ai onto
this module is tracked in docs/14 (an apps/ai touch requires eval-case
updates per Hard Rule 5, so it rides a later slice).
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
