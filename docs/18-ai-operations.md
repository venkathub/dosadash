# AI allowance and recovery operations

## Provider admission allowance

Compose enables `AI_PROVIDER_BUDGET_ENABLED=true`, with defaults
`AI_PROVIDER_DAILY_BUDGET_USD=5` and `AI_PROVIDER_DAILY_MAX_CALLS=1000`.
These are portfolio operational defaults for review before deployment, not a measured
traffic forecast. Set either limit to zero to stop new provider admissions. Restart
AI containers after changing environment settings. Disabling the flag removes the
application protection; it does not disable providers. Standalone development and
evaluation default to disabled and use their own bounded-run accounting.

Migration `a2c4e6f80913` adds `provider_budget_days` and `provider_reservations`.
Deploy migrations before enabling the guard. All instances must use the same ledger
and limits. PostgreSQL determines the UTC day. Admissions have a five-second ledger
bound and fail closed on exhaustion or database failure. This affects AI features;
existing deterministic fallbacks apply where implemented. Ordinary menu browsing and
manual ordering do not require an LLM admission.

Reservations per attempt default to: chat $0.02, embedding $0.005, audio $0.02,
image $0.10, batch $0.02 per JSONL request. They are estimates, **not price guarantees**.
Successful calls with usable cost metadata settle once to the estimated actual cost;
failed, cancelled, streaming and unpriced batch calls retain the reservation. Known
actual charges can exceed the reserved amount and prevent the next admission. There
is no refund based solely on an exception because a provider may already have worked.
Calls retained across a crash are conservative. Never delete today's ledger to
recover service: that resets the allowance and defeats the limit.

The admin Costs tab shows counted allowance, measured cost, attempts and unknown-cost
counts via admin/owner-only `GET /api/v1/admin/reports/provider-budget`.
A missing row means no ledger activity, not proof of zero provider billing. Compare
with provider invoices and Langfuse; GitHub coding agents, tracing and hosting are
outside this service allowance. Ledger rows contain no prompts, output text or keys.
Historical reservations are retained; plan retention before sustained high traffic.

`AI_PROVIDER_TIMEOUT_SECONDS` defaults to 60 seconds per provider attempt; repair,
rate-limit retry and fallback can make a whole request longer. A timeout cancels the
local await, not necessarily provider billing. SDK completion retries are disabled;
application retries/fallbacks are individually admitted. Streams close on exit.

The local classifier is off by default (`AI_REVIEW_LOCAL_ENABLED=false`) after its
confidence audit. Re-enable only for a reviewed comparison, not to hide provider
outages. The nightly provider Batch path remains available.

## Reproduce bounded checks

Use a **fresh, local, migrated and seeded `*_eval` database**, with all three URLs
`EVAL_DATABASE_URL`, `AI_DATABASE_URL`, `API_DATABASE_URL` identical. The scripts
reject production database names. Use fresh report paths; they do not overwrite.

```bash
# No provider calls. Fresh allowance tables are required.
AI_PROVIDER_BUDGET_ENABLED=true AI_PROVIDER_DAILY_BUDGET_USD=0.1 \
  uv run python scripts/check-provider-allowance.py --out /tmp/allowance-check.json

# Contract/scorer replay and deterministic negative controls.
uv run pytest evals/portfolio apps/ai/tests/test_provider_resilience.py -q
# Real local PostgreSQL required; suite resets the TEST_DATABASE_URL database.
uv run pytest apps/api/tests/test_provider_budget.py -q
```

The recorded load probe is an admission-ledger stress test on a developer machine.
It is not a provider outage drill in production or an LLM concurrency benchmark.
Historical API/checkout load results remain dated in `infra/loadtest/results.md`.

## Backup installation and restore review

No DosaDash backup schedule was found in root crontab, the inspected cron directories
or systemd timers on 2026-10-19. This PR supplies tooling; it does not install a timer
or change production. After merge and deployment approval:

1. Check disk space and choose `DOSADASH_BACKUP_DIR` (default `/opt/dosadash-backups`).
2. Run `/opt/dosadash/infra/backup/postgres.sh` once. It uses `umask 077`, mode 700
   storage, a nonblocking lock, custom-format pg_dump and archive-list verification.
   It publishes an archive/hash only after success and rotates matching files older
   than seven days. Archive listing is not a restore test.
3. Install `infra/backup/dosadash-backup.cron` as `/etc/cron.d/dosadash-backup` with
   root ownership/mode 644 after reviewing paths and server timezone. Verify the
   next scheduled run and arrange failure-log/age alerting. Cron installation and
   alert delivery are not claimed as completed here.
4. Add encrypted off-host retention with a named destination and access policy.
   Local copies alone are lost with the VPS; no off-host destination is configured.
5. Restore an archive to an isolated PostgreSQL 16 + pgvector instance. Verify its
   SHA-256, run `pg_restore --no-owner --no-acl --exit-on-error`, compare schema and
   aggregate counts, then exercise required business reads. Never restore over the
   running production database for a drill.
6. With permission for the specific production data and destination, perform a
   production-data drill. Keep archives private, avoid putting them in Git or any
   model prompt, record only non-sensitive evidence, and remove temporary copies.

The committed synthetic restore report covers 60 menu items, 40 generated users,
3,075 generated orders, matching counts and migration up/down/up in isolation. It
contains no production rows. It does not cover point-in-time recovery, storage
loss, roles/grants (the archive excludes ACLs), every business invariant or an RTO.
After explicit owner authorization, the production dump was also restored in an
isolated local container: 61 menu items, 524 users, 40,642 orders, three POs, one
invoice, 83 eval runs, one inventory workflow and 854 forecast observations matched
before/after the dump and restore. Migration f1b3d5e7a902 → a2c4e6f80913 → downgrade
→ upgrade passed with those counts unchanged. The archive and restored database
were deleted; only aggregate evidence is retained in
`evals/portfolio/results/restore-drill.json`. Production remained read-only. This
is not validation of roles/grants, every business read, PITR or recovery after
loss of the VPS. Schedule installation, off-host storage and alerts remain open.
