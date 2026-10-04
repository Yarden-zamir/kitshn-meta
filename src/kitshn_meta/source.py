"""Deployment state from the GitHub API, with a small in-memory cache.

The KitSHn deploy workflow records every deploy as a GitHub deployment in an Environment named
after the KitSHn environment, with statuses `in_progress`, then `success` or `failure`. Pull
request previews use Environments named `pr-<number>`. Their teardown also records a deployment
and the Environment stays, so a preview counts as live only while its pull request is open.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx2

from .views import PREVIEW_PREFIX, RHYTHM_DAYS, EnvStatus, State

API = "https://api.github.com"
STATE_TTL = 60.0
HISTORY_TTL = 600.0
PUBLIC_TTL = 6 * 3600.0
# 500 deploys in 30 days covers the busiest recipe so far (gr52, 121). Above that the rhythm
# badge undercounts; raise this if a recipe gets there.
HISTORY_PAGES = 5
# The environment that the repo badge shows first. Every KitSHn recipe that `kitshn init` wrote
# maps main to `prod`; revisit if recipes start naming their main environment otherwise.
MAIN_ENVIRONMENT = "prod"

GITHUB_STATES: dict[str, State] = {
    "success": "live",
    "inactive": "live",
    "failure": "failed",
    "error": "failed",
}


@dataclass(slots=True)
class GitHub:
    client: httpx2.AsyncClient
    token: str | None = None
    cache: dict[str, tuple[float, Any]] = field(default_factory=dict)
    etags: dict[str, tuple[str, Any]] = field(default_factory=dict)

    async def get(self, path: str, ttl: float) -> Any:
        """GET an API path. 404 gives None. Errors and rate limits serve the last good answer."""

        url = API + path
        cached = self.cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if url in self.etags:
            # A 304 answer does not count against the rate limit.
            headers["If-None-Match"] = self.etags[url][0]
        try:
            response = await self.client.get(url, headers=headers)
        except httpx2.HTTPError:
            return cached[1] if cached else None
        if response.status_code == 304 and url in self.etags:
            data = self.etags[url][1]
        elif response.status_code == 200:
            data = response.json()
            if etag := response.headers.get("ETag"):
                self.etags[url] = (etag, data)
        elif response.status_code == 404:
            data = None
        else:
            return cached[1] if cached else None
        self.cache[url] = (time.monotonic() + ttl, data)
        return data

    async def is_public(self, owner: str, repo: str) -> bool:
        record = await self.get(f"/repos/{owner}/{repo}", PUBLIC_TTL)
        return isinstance(record, dict) and record.get("private") is False

    async def environment(self, owner: str, repo: str, environment: str, *, history: bool = False) -> EnvStatus | None:
        """The state of one environment, or None when it has no deployment."""

        base = f"/repos/{owner}/{repo}/deployments"
        deployments = await self.get(f"{base}?environment={quote(environment)}&per_page=2", STATE_TTL)
        if not isinstance(deployments, list) or not deployments:
            return None
        latest = deployments[0]
        statuses = await self.get(f"{base}/{latest['id']}/statuses?per_page=1", STATE_TTL)
        status = statuses[0] if isinstance(statuses, list) and statuses else {}
        state = GITHUB_STATES.get(status.get("state", ""), "deploying")
        # While a deploy runs or after it fails, the previous deployment still serves.
        serving = latest if state == "live" or len(deployments) < 2 else deployments[1]
        deploys = await self._history(base, environment) if history else ()
        return EnvStatus(
            environment=environment,
            state=state,
            ref=serving.get("sha"),
            url=status.get("environment_url") or None,
            deployed_at=parse_time(serving.get("created_at")),
            deploys=deploys,
        )

    async def _history(self, base: str, environment: str) -> tuple[datetime, ...]:
        """Deployment times newer than the rhythm window, newest first, across result pages."""

        cutoff = datetime.now(UTC) - timedelta(days=RHYTHM_DAYS)
        times: list[datetime] = []
        for page in range(1, HISTORY_PAGES + 1):
            rows = await self.get(f"{base}?environment={quote(environment)}&per_page=100&page={page}", HISTORY_TTL)
            if not isinstance(rows, list):
                break
            times.extend(when for row in rows if (when := parse_time(row.get("created_at"))))
            if len(rows) < 100 or (times and times[-1] < cutoff):
                break
        return tuple(when for when in times if when >= cutoff)

    async def repo(self, owner: str, repo: str) -> list[EnvStatus]:
        """The main environment, then one entry per open pull request that has a deployment."""

        envs: list[EnvStatus] = []
        if main := await self.environment(owner, repo, MAIN_ENVIRONMENT):
            envs.append(main)
        pulls = await self.get(f"/repos/{owner}/{repo}/pulls?state=open&per_page=100", STATE_TTL)
        for pull in pulls if isinstance(pulls, list) else []:
            if preview := await self.environment(owner, repo, f"{PREVIEW_PREFIX}{pull['number']}"):
                envs.append(preview)
        return envs


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
