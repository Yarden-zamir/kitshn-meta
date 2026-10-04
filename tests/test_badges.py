import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from kitshn_meta.app import create_app
from kitshn_meta.badges import DEPLOYING, DOWN, LIVE, WARN, Segment, render, text_width
from kitshn_meta.views import EnvStatus, PreviewMode, Probe, State, age, repo_badge, rhythm, safe_name, state_of

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def env(name: str, state: State = "live", deployed: datetime | None = NOW - timedelta(days=2)) -> EnvStatus:
    return EnvStatus(name, state, "06fec4472672", f"https://{name}.example.com", deployed, ())


def test_text_width_sums_the_verdana_table_and_falls_back_to_m() -> None:
    assert text_width("kitshn") == 34
    assert text_width("€") == text_width("m")


def test_render_sizes_segments_to_their_content() -> None:
    svg = render([Segment(text="kitshn", logo=True), Segment(text="live", dot=True)], "kitshn: live")
    # Each segment: 6px padding, its parts with 4px gaps, 6px padding.
    label = 6 + 14 + 4 + text_width("kitshn") + 6
    state = 6 + 7 + 4 + text_width("live") + 6
    assert f'width="{label + state}" height="20"' in svg
    assert "<title>kitshn: live</title>" in svg


@pytest.mark.parametrize(
    ("status", "probe", "expected"),
    [
        ("live", Probe(200, 30), ("live · 2d", LIVE, False)),
        ("live", None, ("live · 2d", LIVE, False)),
        ("deploying", Probe(502, 30), ("deploying", DEPLOYING, True)),
        ("failed", Probe(200, 30), ("live · deploy failed", WARN, False)),
        ("live", Probe(502, 30), ("down · 502", DOWN, False)),
        ("failed", Probe(None, None), ("down", DOWN, False)),
    ],
)
def test_state_of(status: State, probe: Probe | None, expected: tuple[str, str, bool]) -> None:
    assert state_of(env("prod", status), probe, NOW) == expected


def test_age_rounds_to_one_unit() -> None:
    assert [age(NOW - delta, NOW) for delta in (timedelta(seconds=5), timedelta(minutes=59), timedelta(hours=47), timedelta(days=9))] == ["1m", "59m", "47h", "9d"]


def test_rhythm_counts_the_last_thirty_utc_days() -> None:
    deploys = (NOW, NOW - timedelta(hours=1), NOW - timedelta(days=29), NOW - timedelta(days=30))
    bars = rhythm(deploys, NOW)
    assert (len(bars), bars[-1], bars[0], sum(bars)) == (30, 2, 1, 3)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("count", "prod live · 2d · 2 previews"),
        ("list", "prod live · 2d · #4 #41"),
        ("dots", "prod live · 2d · 2 previews"),
        ("hide", "prod live · 2d"),
    ],
)
def test_repo_badge_preview_modes(mode: PreviewMode, message: str) -> None:
    envs = [env("pr-41"), env("prod"), env("pr-4", "failed")]
    badge = repo_badge(envs, {}, mode, NOW)
    assert badge.message == message
    if mode == "dots":
        assert badge.segments[-1].dots == (WARN, LIVE)


def test_safe_name_rejects_traversal() -> None:
    assert safe_name("kitshn-meta") and safe_name("pr-4") and safe_name("a.b")
    assert not any(safe_name(value) for value in ("", "..", ".hidden", "a/b", "a b"))


def _write(root: Path, owner: str, repo: str, environment: str, **fields: object) -> None:
    path = root / owner / repo / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"environment": environment, "state": "live", "ref": "06fec4472672", "url": f"https://{environment}.example.com",
              "deployed_at": (NOW - timedelta(days=2)).isoformat(), "deploys": []}
    path.write_text(json.dumps(record | fields), encoding="utf-8")


def _client(root: Path, private: set[str] | None = None) -> TestClient:
    private = private or set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            name = request.url.path.removeprefix("/repos/")
            return httpx.Response(200, json={"private": name in private})
        if "down" in request.url.host:
            return httpx.Response(502)
        return httpx.Response(200)

    return TestClient(create_app(root, httpx.MockTransport(handler)))


def test_repo_endpoint_renders_svg_and_shields_json(tmp_path: Path) -> None:
    _write(tmp_path, "o", "site", "prod")
    _write(tmp_path, "o", "site", "pr-7", state="deploying")
    with _client(tmp_path) as client:
        svg = client.get("/b/o/site.svg?previews=list")
        data = client.get("/b/o/site.json").json()
    assert svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["cache-control"] == "public, max-age=60"
    assert "#7" in svg.text
    assert data["schemaVersion"] == 1 and data["label"] == "kitshn" and data["message"].startswith("prod live")
    assert data["logoSvg"].startswith("<svg")


def test_env_endpoint_probes_the_public_url(tmp_path: Path) -> None:
    _write(tmp_path, "o", "site", "prod", url="https://down.example.com")
    with _client(tmp_path) as client:
        assert "down · 502" in client.get("/b/o/site/prod.json").json()["message"]
        assert client.get("/b/o/site/prod.json?show=ref").json()["message"].startswith("06fec44")


def test_private_and_missing_repos_get_the_same_unknown_badge(tmp_path: Path) -> None:
    _write(tmp_path, "o", "secret", "prod")
    with _client(tmp_path, private={"o/secret"}) as client:
        private = client.get("/b/o/secret/prod.json").json()
        missing = client.get("/b/o/nothing/prod.json").json()
        traversal = client.get("/b/o/..%2Fsecret/prod.json")
    assert private == missing and private["message"] == "unknown"
    assert traversal.status_code in (200, 404) and "06fec44" not in traversal.text


def test_bad_options_are_rejected_with_the_allowed_values(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/b/o/site.svg?previews=all")
    assert response.status_code == 400 and "count, list, dots, hide" in response.text


@pytest.mark.parametrize("mode", ["count", "dots"])
def test_one_preview_is_singular_in_every_layout(mode: PreviewMode) -> None:
    badge = repo_badge([env("prod"), env("pr-9")], {}, mode, NOW)
    assert badge.message.endswith("· 1 preview")
