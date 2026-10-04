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
from .source import GitHub
from .views import (
    Badge,
    PreviewMode,
    Probe,
    Show,
    env_badge,
    repo_badge,
    safe_name,
    shields_json,
    static,
    unknown,
)

STATIC_DIR = Path(__file__).parent / "static"
BADGE_CACHE = "public, max-age=60"
PROBE_TTL = 60.0


@dataclass(slots=True)
class Services:
    """GitHub reads and public route probes, each with a small in-memory cache. One per process."""

    github: GitHub
    client: httpx2.AsyncClient
    probes: dict[str, tuple[float, Probe]] = field(default_factory=dict)

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
    # Badges show only public repos. A private repo looks like a missing one.
    if not (safe_name(owner) and safe_name(repo)) or not await services.github.is_public(owner, repo):
        return _respond(unknown(), ext)
    envs = await services.github.repo(owner, repo)
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
    if not all(safe_name(part) for part in (owner, repo, environment)):
        return _respond(unknown(label), ext)
    if not await services.github.is_public(owner, repo):
        return _respond(unknown(label), ext)
    env = await services.github.environment(owner, repo, environment, history=show == "rhythm")
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


def create_app(transport: httpx2.AsyncBaseTransport | None = None) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(timeout=5.0, transport=transport, headers={"User-Agent": "kitshn-meta"}) as client:
            github = GitHub(client, os.environ.get("GITHUB_TOKEN") or None)
            app.state.services = Services(github, client)
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
