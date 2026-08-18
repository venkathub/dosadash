"""Text-to-SQL analytics copilot (Phase 5, docs/03 #21).

Defense in depth, in order:
1. `guardrail.validate_sql` — allowlist/denylist static validation
2. READ ONLY transaction + statement_timeout at execution time
3. optional dedicated `dosadash_readonly` DB role (migration b3f9c82d4e61)
   with SELECT grants on the same table allowlist
"""
