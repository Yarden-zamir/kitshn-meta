"""Turn KitSHn deployment status files into badges.

KitSHn writes one JSON file per deployment under `<status root>/<owner>/<repo>/<environment>.json`
(see `specs/deploy-flow.md` in the kitshn repo). This module only reads them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from .badges import BASIL, DEPLOYING, DOWN, LABEL_COLOR, LIVE, POT_SVG, REF, UNKNOWN, WARN, Segment

State = Literal["deploying", "live", "failed"]
PreviewMode = Literal["count", "list", "dots", "hide"]
Show = Literal["status", "ref", "rhythm", "edge"]
PREVIEW_PREFIX = "pr-"
RHYTHM_DAYS = 30


@dataclass(frozen=True, slots=True)
class Probe:
    """One request to the public URL: an HTTP status code, or None when it did not answer."""

    status: int | None
    ms: int | None

    @property
    def up(self) -> bool:
        return self.status is not None and 200 <= self.status < 400


@dataclass(frozen=True, slots=True)
class EnvStatus:
    environment: str
    state: State
    ref: str | None
    url: str | None
    deployed_at: datetime | None
    deploys: tuple[datetime, ...]

    @property
    def is_preview(self) -> bool:
        return self.environment.startswith(PREVIEW_PREFIX)

    @property
    def preview_number(self) -> str:
        return self.environment.removeprefix(PREVIEW_PREFIX)


@dataclass(frozen=True, slots=True)
class Badge:
    segments: list[Segment]
    title: str
    label: str
    message: str
    color: str


def safe_name(value: str) -> bool:
    """GitHub owner, repo, and KitSHn environment names: letters, digits, `-`, `_`, `.`, no `..`."""

    return bool(value) and not value.startswith(".") and all(char.isalnum() or char in "-_." for char in value)


def load_env(root: Path, owner: str, repo: str, environment: str) -> EnvStatus | None:
    if not all(safe_name(part) for part in (owner, repo, environment)):
        return None
    return _parse(root / owner / repo / f"{environment}.json")


def load_repo(root: Path, owner: str, repo: str) -> list[EnvStatus]:
    if not (safe_name(owner) and safe_name(repo)):
        return []
    folder = root / owner / repo
    if not folder.is_dir():
        return []
    return [env for path in sorted(folder.glob("*.json")) if (env := _parse(path)) is not None]


def _parse(path: Path) -> EnvStatus | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("state") not in ("deploying", "live", "failed"):
        return None
    deploys = record.get("deploys")
    return EnvStatus(
        environment=str(record.get("environment") or path.stem),
        state=record["state"],
        ref=_text(record.get("ref")),
        url=_text(record.get("url")),
        deployed_at=_time(record.get("deployed_at")),
        deploys=tuple(when for item in (deploys if isinstance(deploys, list) else []) if (when := _time(item))),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def age(since: datetime | None, now: datetime) -> str:
    if since is None:
        return ""
    seconds = max(0, int((now - since).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def state_of(env: EnvStatus, probe: Probe | None, now: datetime) -> tuple[str, str, bool]:
    """The state text, its color, and whether the dot pulses."""

    if env.state == "deploying":
        return "deploying", DEPLOYING, True
    if probe is not None and not probe.up:
        return (f"down · {probe.status}" if probe.status else "down"), DOWN, False
    if env.state == "failed":
        return "live · deploy failed", WARN, False
    when = age(env.deployed_at, now)
    return (f"live · {when}" if when else "live"), LIVE, False


def _label(text: str) -> Segment:
    return Segment(text=text, color=LABEL_COLOR, logo=True)


def unknown(label: str = "kitshn") -> Badge:
    return Badge([_label(label), Segment(text="unknown", color=UNKNOWN)], f"{label}: unknown", label, "unknown", UNKNOWN)


def static() -> Badge:
    return Badge([_label("deployed with"), Segment(text="kitshn", color=BASIL)], "deployed with kitshn", "deployed with", "kitshn", BASIL)


def env_badge(env: EnvStatus, show: Show, probe: Probe | None, now: datetime) -> Badge:
    label = f"kitshn · {env.environment}"
    if show == "ref":
        text = env.ref[:7] if env.ref else "no ref"
        when = age(env.deployed_at, now)
        message = f"{text} · {when}" if when else text
        return Badge([_label(label), Segment(text=message, color=REF)], f"{label}: {message}", label, message, REF)
    if show == "rhythm":
        bars = rhythm(env.deploys, now)
        message = f"{sum(bars)} / {RHYTHM_DAYS}d"
        return Badge(
            [_label(label), Segment(text=message, color=LIVE, bars=tuple(bars))],
            f"{label}: {sum(bars)} deploys in {RHYTHM_DAYS} days", label, message, LIVE,
        )
    if show == "edge":
        if probe is None:
            return Badge([_label(label), Segment(text="no public URL", color=UNKNOWN)], f"{label}: no public URL", label, "no public URL", UNKNOWN)
        color = LIVE if probe.up else DOWN
        message = f"{probe.status} · {probe.ms} ms" if probe.status else "no answer"
        return Badge([_label(label), Segment(text=message, color=color, dot=True)], f"{label}: {message}", label, message, color)
    message, color, pulse = state_of(env, probe, now)
    return Badge([_label(label), Segment(text=message, color=color, dot=True, pulse=pulse)], f"{label}: {message}", label, message, color)


def rhythm(deploys: tuple[datetime, ...], now: datetime) -> list[int]:
    """Deploys per UTC day for the last RHYTHM_DAYS days, oldest first, today last."""

    today = now.astimezone(UTC).date()
    start = today - timedelta(days=RHYTHM_DAYS - 1)
    bars = [0] * RHYTHM_DAYS
    for when in deploys:
        day = when.astimezone(UTC).date()
        if start <= day <= today:
            bars[(day - start).days] += 1
    return bars


def repo_badge(envs: list[EnvStatus], probes: dict[str, Probe | None], mode: PreviewMode, now: datetime) -> Badge:
    """The main environment's state, then its pull request previews in the chosen layout."""

    if not envs:
        return unknown()
    previews = sorted((env for env in envs if env.is_preview), key=lambda env: _number(env.preview_number))
    mains = [env for env in envs if not env.is_preview]
    main = next((env for env in mains if env.environment == "prod"), mains[0] if mains else None)
    segments = [_label("kitshn")]
    words: list[str] = []
    color = LIVE
    if main is not None:
        state, color, pulse = state_of(main, probes.get(main.environment), now)
        text = f"{main.environment} {state}"
        segments.append(Segment(text=text, color=color, dot=True, pulse=pulse))
        words.append(text)
    if previews and mode != "hide":
        count = f"{len(previews)} preview" + ("s" if len(previews) != 1 else "")
        if mode == "count":
            segments.append(Segment(text=count, color=DEPLOYING))
        elif mode == "list":
            segments.append(Segment(text=" ".join(f"#{env.preview_number}" for env in previews), color=DEPLOYING))
        else:
            dots = tuple(state_of(env, probes.get(env.environment), now)[1] for env in previews)
            segments.append(Segment(text="previews", color=REF, dots=dots))
        # The shields JSON has one message line, so every layout reads as the count there,
        # except the numbers layout, which lists them.
        words.append(segments[-1].text if mode == "list" else count)
    if len(segments) == 1:
        return unknown()
    message = " · ".join(words)
    return Badge(segments, f"kitshn: {message}", "kitshn", message, color)


def _number(value: str) -> tuple[int, str]:
    return (int(value), "") if value.isdigit() else (1_000_000, value)


def shields_json(badge: Badge) -> dict[str, object]:
    """The shields.io endpoint format, for people who want shields' own styles."""

    return {
        "schemaVersion": 1,
        "label": badge.label,
        "message": badge.message,
        "color": badge.color.removeprefix("#"),
        "labelColor": LABEL_COLOR.removeprefix("#"),
        "logoSvg": POT_SVG,
        "cacheSeconds": 60,
    }
