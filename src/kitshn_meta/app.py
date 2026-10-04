"""The KitSHn landing page and the deploy badge server."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, get_args

import httpx2
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import badges
from .views import (
    Badge,
    PreviewMode,
    Probe,
    Show,
    env_badge,
    load_env,
    load_repo,
    repo_badge,
    safe_name,
    shields_json,
    static,
    unknown,
)

STATIC_DIR = Path(__file__).parent / "static"
BADGE_CACHE = "public, max-age=60"
PROBE_TTL = 60.0
PUBLIC_TTL = 6 * 3600.0
PRIVATE_TTL = 600.0


@dataclass(slots=True)
class Services:
    """Network lookups with small in-memory caches. One instance per process."""

    status_root: Path
    client: httpx2.AsyncClient
    github_token: str | None = None
    visibility: dict[str, tuple[float, bool]] = field(default_factory=dict)
    probes: dict[str, tuple[float, Probe]] = field(default_factory=dict)

    async def is_public(self, owner: str, repo: str) -> bool:
        """Badges show only public repos. A private or unknown repo looks like a missing one."""

        key = f"{owner}/{repo}".lower()
        cached = self.visibility.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        try:
            response = await self.client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
        except httpx2.HTTPError:
            return cached[1] if cached else False
        if response.status_code == 200:
            public = response.json().get("private") is False
        elif response.status_code == 404:
            public = False
        else:
            # Rate limited or GitHub trouble: keep the last answer instead of flapping.
            return cached[1] if cached else False
        self.visibility[key] = (time.monotonic() + (PUBLIC_TTL if public else PRIVATE_TTL), public)
        return public

    async def probe(self, url: str | None) -> Probe | None:
        if url is None:
            return None
        cached = self.probes.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        started = time.monotonic()
        try:
            response = await self.client.get(url, follow_redirects=False)
            result = Probe(status=response.status_code, ms=round((time.monotonic() - started) * 1000))
        except httpx2.HTTPError:
            result = Probe(status=None, ms=None)
        self.probes[url] = (time.monotonic() + PROBE_TTL, result)
        return result


def _services(request: Request) -> Services:
    return request.app.state.services


def _respond(badge: Badge, ext: str) -> Response:
    headers = {"Cache-Control": BADGE_CACHE}
    if ext == "json":
        return JSONResponse(shields_json(badge), headers=headers)
    return Response(badges.render(badge.segments, badge.title), media_type="image/svg+xml", headers=headers)


def _split(name: str) -> tuple[str, str] | None:
    stem, dot, ext = name.rpartition(".")
    return (stem, ext) if dot and ext in ("svg", "json") else None


async def repo_endpoint(request: Request) -> Response:
    owner = request.path_params["owner"]
    parts = _split(request.path_params["name"])
    if parts is None:
        return PlainTextResponse("use .svg or .json", status_code=404)
    repo, ext = parts
    mode = request.query_params.get("previews", "count")
    if mode not in get_args(PreviewMode):
        return PlainTextResponse(f"previews must be one of {', '.join(get_args(PreviewMode))}", status_code=400)
    services = _services(request)
    if not (safe_name(owner) and safe_name(repo)) or not await services.is_public(owner, repo):
        return _respond(unknown(), ext)
    envs = load_repo(services.status_root, owner, repo)
    probes = {env.environment: await services.probe(env.url) for env in envs}
    return _respond(repo_badge(envs, probes, cast(PreviewMode, mode), datetime.now(UTC)), ext)


async def env_endpoint(request: Request) -> Response:
    owner, repo = request.path_params["owner"], request.path_params["repo"]
    parts = _split(request.path_params["name"])
    if parts is None:
        return PlainTextResponse("use .svg or .json", status_code=404)
    environment, ext = parts
    show = request.query_params.get("show", "status")
    if show not in get_args(Show):
        return PlainTextResponse(f"show must be one of {', '.join(get_args(Show))}", status_code=400)
    services = _services(request)
    label = f"kitshn · {environment}" if safe_name(environment) else "kitshn"
    if not (safe_name(owner) and safe_name(repo)) or not await services.is_public(owner, repo):
        return _respond(unknown(label), ext)
    env = load_env(services.status_root, owner, repo, environment)
    if env is None:
        return _respond(unknown(label), ext)
    probe = await services.probe(env.url) if show in ("status", "edge") else None
    return _respond(env_badge(env, cast(Show, show), probe, datetime.now(UTC)), ext)


async def static_endpoint(request: Request) -> Response:
    ext = request.path_params["ext"]
    if ext not in ("svg", "json"):
        return PlainTextResponse("use .svg or .json", status_code=404)
    return _respond(static(), ext)


async def index(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "public, max-age=300"})


async def healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


def create_app(status_root: Path | None = None, transport: httpx2.AsyncBaseTransport | None = None) -> Starlette:
    root = status_root or Path(os.environ.get("STATUS_DIR", "/status"))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(timeout=5.0, transport=transport, headers={"User-Agent": "kitshn-meta"}) as client:
            app.state.services = Services(root, client, os.environ.get("GITHUB_TOKEN") or None)
            yield

    return Starlette(
        routes=[
            Route("/", index),
            Route("/healthz", healthz),
            Route("/b/static.{ext}", static_endpoint),
            Route("/b/{owner}/{name}", repo_endpoint),
            Route("/b/{owner}/{repo}/{name}", env_endpoint),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        lifespan=lifespan,
    )


app = create_app()
