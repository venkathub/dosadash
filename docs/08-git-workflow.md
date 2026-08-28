# 08 — Git Workflow & Branching Strategy (Production Standard)

Strategy: **GitHub Flow + phase integration branches**. Chosen over GitFlow (built for scheduled releases; too heavy for continuous deploy) and over pure trunk-based (no isolation for multi-week phases). `main` is always deployable production.

## Branch Model

```
main (PROD — protected, always deployable, auto-deploys to VPS on merge)
 ▲
 │  Phase PR: squash-merge, CI + EVAL gates + self-review checklist
 │
phase/N-name        (integration branch per phase, e.g. phase/0-foundation)
 ▲   ▲   ▲
 │   │   │  small PRs into the phase branch
feat/otp-auth  feat/kds-websocket  fix/cart-race
                                        │
hotfix/* ───────────────────────────────► PR straight to main (emergency only)
```

| Branch | Purpose | Lifetime | Merges via |
|---|---|---|---|
| `main` | Production. Every commit is deployed. | Permanent | PR only (squash) |
| `phase/N-name` | One per schedule phase (`phase/0-foundation` … `phase/15-*`) | Weeks | PR → `main` at phase completion |
| `feat/*`, `fix/*`, `evals/*`, `docs/*` | Small units of work | Days | PR → current `phase/*` |
| `hotfix/*` | Production emergency | Hours | PR → `main`, then back-merge to active phase |

## Rules

1. **Never commit directly to `main`** — branch protection enforces PR + passing CI.
2. **Phase branches map 1:1 to docs/05 schedule phases.** A phase lands in `main` only when its Definition of Done is met (deployed-ready, tests green, evals green, CLAUDE.md status updated).
3. **CI gates on every PR**: ruff + pytest + **eval suites** (order_accuracy ≥ 0.95 threshold once agent exists). PRs touching `apps/ai/**`, `evals/**`, or prompts MUST include eval updates.
4. **Squash-merge** phase → main (clean prod history: one commit per phase + hotfixes). Regular merge feat → phase (preserve granular history for interviews).
5. **Conventional commits** everywhere: `feat:`, `fix:`, `evals:`, `docs:`, `chore:`, `infra:`.
6. **Deploy pipeline**: merge to `main` → GitHub Actions: build → GHCR → SSH → `docker compose up -d` → smoke `/healthz` → Telegram "✅ deployed <sha>". Rollback = revert PR (previous images still on GHCR).
7. **Keep phase branches fresh**: rebase/merge `main` into the active phase branch after any hotfix.
8. **Solo-dev review discipline**: since there's no second reviewer, every phase PR description must include the self-review checklist (below) — this is the portfolio-visible substitute for peer review.

## Phase PR Checklist (paste into PR description)

```markdown
## Phase N: <name>
### Deliverables (from docs/05)
- [ ] <list each deliverable, checked>
### Quality gates
- [ ] pytest green · ruff clean
- [ ] Eval suites green (scores: ___)
- [ ] New agent capabilities have new eval cases
- [ ] Langfuse traces verified for new LLM paths
- [ ] No secrets in diff · PII redaction intact
- [ ] Memory budget respected (docs/02 table)
- [ ] CLAUDE.md status checklist updated
### Deploy
- [ ] Staged locally via docker compose · smoke tested
```

## Branch Protection (`main`) — applied via gh api

- Require pull request before merging (no direct pushes)
- **Required status checks (since Phase 13 arming): Python lint+tests · Web build · Eval suites · Live eval gate** (the live gate reports on EVERY PR — skip counts as satisfied when no AI paths changed); UI smoke and the AI-review verdict run as additional checks
- Auto-merge enabled — used by the self-healing fixer for S/LOW bug fixes (`gh pr merge --auto --squash`; branch protection decides)
- Enforce for administrators
- No force pushes, no deletions

## Autonomous PRs (Phases 13–15)

The self-healing loop's fixer opens PRs from `fix/issue-N` branches via a PAT
(so CI triggers) and faces the **same required checks as a human PR**, plus an
independent AI-review verdict from a different model. Deploys run a
deterministic canary; a breach opens a mechanical `git revert` PR through the
same gates. Nothing about this section weakens rule 1: `main` still only moves
by PR + green checks.

## Phase → Branch Map

| Phase | Branch |
|---|---|
| 0 Foundation | `phase/0-foundation` |
| 1 Core platform + Auth (wk 2–3) | `phase/1-core-auth` |
| 2 Admin Backend I (wk 4) | `phase/2-admin-backoffice` |
| 3 RAG + Order Agent (wk 5–6) | `phase/3-rag-order-agent` |
| 4 Evals + LLMOps (wk 7) | `phase/4-evals-llmops` |
| 5 Classical ML + Admin II (wk 8) | `phase/5-ml-reports` |
| 6 Agents + MCP (wk 9) | `phase/6-agents-mcp` |
| 7 Voice/Vision/Recsys/Promos (wk 10) | `phase/7-multimodal-recsys` |
| 8 Fine-tune + Reviews (wk 11) | `phase/8-finetune-reviews` |
| 9 Hardening + Story (wk 12) | `phase/9-hardening` |
| 10 UI Heritage Luxe (post-schedule) | `phase/10-ui-premium` |
| 11 Highway menu + serving windows | `phase/11-highway-menu` |
| 12 UI Madras Pop | `phase/12-*` (PRs #104–#110) |
| 13 Self-Healing Loop | `phase/13-*` (PRs #112–#118) |
| 14 Fixer/Verifier Observability | `phase/14-fixer-observability` |
| 15 SDLC Loop v2 | `phase/15-*` (PRs #142–#150) |
| — Autonomous fixes | `fix/issue-N` → PR straight to `main` through full gates |

## Why This Is Interview Gold

- Prod history reads as clean phase releases; phase branches preserve granular engineering process.
- Eval-gated merges = "we don't ship prompt regressions" — most portfolios can't show this.
- Documented solo-review discipline shows you understand *why* review exists, not just the ritual.
