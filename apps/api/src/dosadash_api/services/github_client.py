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
from dosadash_shared import GITHUB_LABELS

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


def get_github_client() -> GitHubClient:
    settings = get_settings()
    return GitHubClient(settings.github_token, settings.github_repo)
