"""GitHub issue mirror for the self-healing loop (Phase 13, docs/14).

Trust model:
- The token is a fine-grained PAT or GitHub App installation token scoped
  to ONE repo with `issues:write` — it can file and label issues, nothing
  else. It lives in env (`API_GITHUB_TOKEN`, Hard Rule 9), never in code.
- GitHub is never on the customer's critical path: every caller treats
  these methods as best-effort (store the local row first, record
  `github_error` on failure — hotfix-#72 "nice-to-have degrades" pattern).
- Labels applied here are the automation signal the fixer workflow triggers
  on, so the label registry lives in dosadash_shared.feedback (one source
  of truth for api, triage policy, approval flow, and workflow filter).

Injectable via `get_github_client()` so tests substitute a fake without
touching the network (ai_client.py pattern).
"""

import logging

import httpx

from dosadash_api.config import get_settings
from dosadash_shared import FIX_BRANCH_PREFIX, GITHUB_LABELS

logger = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
_TIMEOUT_SECONDS = 15


class GitHubError(Exception):
    """GitHub call failed — callers degrade, never 5xx the reporter."""


class GitHubClient:
    def __init__(self, token: str, repo: str, base_url: str = "https://api.github.com") -> None:
        self._token = token
        self._repo = repo
        self._base = base_url.rstrip("/")
        # Per-process cache: a label successfully ensured once is never
        # re-ensured (idempotent create; 422 already_exists counts as success).
        self._labels_ensured: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._repo)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    async def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.request(
                    method, f"{self._base}{path}", json=json, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub call failed: {exc}") from exc
        return resp

    async def _ensure_labels(self, labels: list[str]) -> None:
        """Create registry labels lazily so a fresh repo needs no manual setup."""
        for name in labels:
            if name in self._labels_ensured or name not in GITHUB_LABELS:
                continue
            color, description = GITHUB_LABELS[name]
            resp = await self._request(
                "POST",
                f"/repos/{self._repo}/labels",
                json={"name": name, "color": color, "description": description},
            )
            # 201 created | 422 already exists — both mean the label is usable.
            if resp.status_code not in (201, 422):
                raise GitHubError(f"label ensure failed: {name} → HTTP {resp.status_code}")
            self._labels_ensured.add(name)

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> int:
        """File one issue; returns the issue number."""
        await self._ensure_labels(labels)
        resp = await self._request(
            "POST",
            f"/repos/{self._repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        if resp.status_code != 201:
            raise GitHubError(f"issue create failed: HTTP {resp.status_code}")
        return int(resp.json()["number"])

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        """Append labels to an existing issue (triage/approval flips)."""
        await self._ensure_labels(labels)
        resp = await self._request(
            "POST",
            f"/repos/{self._repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        if resp.status_code != 200:
            raise GitHubError(f"label add failed: HTTP {resp.status_code}")

    async def remove_label(self, issue_number: int, label: str) -> None:
        """Remove one label; a label already absent (404) is success."""
        resp = await self._request(
            "DELETE", f"/repos/{self._repo}/issues/{issue_number}/labels/{label}"
        )
        if resp.status_code not in (200, 404):
            raise GitHubError(f"label remove failed: HTTP {resp.status_code}")

    async def comment(self, issue_number: int, body: str) -> None:
        """Post a comment (decision trail: approvals/rejections land here too)."""
        resp = await self._request(
            "POST", f"/repos/{self._repo}/issues/{issue_number}/comments", json={"body": body}
        )
        if resp.status_code != 201:
            raise GitHubError(f"comment failed: HTTP {resp.status_code}")

    # -------------------------------------------------- reads (Phase 14)
    # The reconciler heals missed webhooks by diffing an issue's CURRENT
    # labels/state against the local projection. Requires the PAT to also
    # carry `pull-requests:read` (runbook: docs/14 §7).

    async def get_issue(self, issue_number: int) -> dict:
        """Current issue state: labels, open/closed, close reason."""
        resp = await self._request("GET", f"/repos/{self._repo}/issues/{issue_number}")
        if resp.status_code != 200:
            raise GitHubError(f"issue get failed: HTTP {resp.status_code}")
        data = resp.json()
        return {
            "state": data.get("state"),
            "state_reason": data.get("state_reason"),
            "labels": [label["name"] for label in data.get("labels", [])],
            "closed_at": data.get("closed_at"),
        }

    async def find_fix_pr(self, issue_number: int) -> dict | None:
        """Locate the fixer's PR via its branch-naming contract
        (`fix/issue-N` — dosadash_shared.FIX_BRANCH_PREFIX). Deterministic
        and one cheap list call; no search-API rate-limit exposure."""
        owner = self._repo.split("/")[0]
        resp = await self._request(
            "GET",
            f"/repos/{self._repo}/pulls"
            f"?head={owner}:{FIX_BRANCH_PREFIX}{issue_number}&state=all&per_page=1",
        )
        if resp.status_code != 200:
            raise GitHubError(f"pr lookup failed: HTTP {resp.status_code}")
        pulls = resp.json()
        if not pulls:
            return None
        pr = pulls[0]
        return {
            "number": pr["number"],
            "state": pr.get("state"),
            "merged_at": pr.get("merged_at"),
            "html_url": pr.get("html_url"),
        }

    # -------------------------------------------- workflow runs (watchdog)
    # The dispatch watchdog needs run-level truth GitHub webhooks cannot
    # carry: a run stuck `queued` or dead with `startup_failure` (zero
    # jobs, zero logs — observed live during the 2026-08-26 Actions
    # outage) never triggers any webhook and never self-reports through
    # the run-ingest step. Listing runs works with the repo-scoped PAT on
    # a public repo; cancelling additionally needs `actions:write`, so
    # cancel degrades honestly (False, never raises for permission).

    async def list_workflow_runs(self, workflow_file: str, *, per_page: int = 30) -> list[dict]:
        """Recent runs of one workflow file, newest first."""
        resp = await self._request(
            "GET",
            f"/repos/{self._repo}/actions/workflows/{workflow_file}/runs?per_page={per_page}",
        )
        if resp.status_code != 200:
            raise GitHubError(f"workflow runs list failed: HTTP {resp.status_code}")
        return [
            {
                "id": run.get("id"),
                "status": run.get("status"),  # queued | in_progress | completed
                "conclusion": run.get("conclusion"),
                "display_title": run.get("display_title"),
                "event": run.get("event"),
                "created_at": run.get("created_at"),
            }
            for run in resp.json().get("workflow_runs", [])
        ]

    async def cancel_workflow_run(self, run_id: int) -> bool:
        """Cancel one run. True on accepted; False when the token lacks
        `actions:write` (403) or the run can no longer be cancelled (409)
        — callers treat False as 'do not redispatch yet'."""
        resp = await self._request("POST", f"/repos/{self._repo}/actions/runs/{run_id}/cancel")
        if resp.status_code == 202:
            return True
        if resp.status_code in (403, 409):
            logger.warning("run cancel refused (run %s): HTTP %s", run_id, resp.status_code)
            return False
        raise GitHubError(f"run cancel failed: HTTP {resp.status_code}")


def get_github_client() -> GitHubClient:
    settings = get_settings()
    return GitHubClient(settings.github_token, settings.github_repo)
