"""SQL validation guardrail — static checks before anything touches the DB.

Philosophy: allowlist what we understand, reject everything else. The LLM
gets error messages back (self-correction), so rejections are cheap; a
false-accept is not.
"""

import re

# Tables the copilot may read. users is allowlisted but the phone column is
# banned outright (Hard Rule 8 adjacent: PII stays out of LLM-visible data).
ALLOWED_TABLES = frozenset(
    {
        "orders",
        "order_items",
        "menu_items",
        "ingredients",
        "recipe_ingredients",
        "forecasts",
        "customer_segments",
        "coupons",
        "coupon_redemptions",
        "combos",
        "users",
        "eval_runs",
    }
)

MAX_LIMIT = 200

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|execute|call|do|"
    r"vacuum|listen|notify|set|reset|show|prepare|deallocate|lock|refresh|reindex|cluster|"
    r"security|merge|returning|into)\b",
    re.IGNORECASE,
)
_FORBIDDEN_TOKENS = re.compile(
    r"(phone|pg_sleep|pg_catalog|pg_stat|pg_read|pg_ls|information_schema|current_setting|"
    r"dblink|lo_import|lo_export|::regclass|\$\$)",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_CTE_NAME = re.compile(r"(?:\bwith\s+|,\s*)([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit\s+(\d+)", re.IGNORECASE)


class SqlValidationError(ValueError):
    """Rejected SQL — the message is fed back to the LLM for self-correction."""


def validate_sql(sql: str) -> str:
    """Validate + normalize one SELECT statement. Returns SQL that is safe to
    run (LIMIT enforced); raises SqlValidationError with an actionable reason."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise SqlValidationError("empty SQL")
    if "--" in cleaned or "/*" in cleaned:
        raise SqlValidationError("comments are not allowed")
    if ";" in cleaned:
        raise SqlValidationError("only a single statement is allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise SqlValidationError("only SELECT queries are allowed")

    if match := _FORBIDDEN.search(cleaned):
        raise SqlValidationError(f"forbidden keyword: {match.group(1)}")
    if match := _FORBIDDEN_TOKENS.search(cleaned):
        raise SqlValidationError(f"forbidden reference: {match.group(1)}")

    cte_names = {m.group(1).lower() for m in _CTE_NAME.finditer(cleaned)}
    for match in _TABLE_REF.finditer(cleaned):
        ref = match.group(1).lower().removeprefix("public.")
        if ref in cte_names or ref == "(":
            continue
        if ref not in ALLOWED_TABLES:
            raise SqlValidationError(
                f"table '{ref}' is not in the allowlist: {', '.join(sorted(ALLOWED_TABLES))}"
            )

    if match := _LIMIT.search(cleaned):
        if int(match.group(1)) > MAX_LIMIT:
            raise SqlValidationError(f"LIMIT must be <= {MAX_LIMIT}")
    else:
        cleaned = f"{cleaned} LIMIT {MAX_LIMIT}"
    return cleaned
