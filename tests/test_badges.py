from datetime import UTC, datetime, timedelta

import httpx2
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


class FakeGitHub:
    """The GitHub API paths that source.py reads, from plain dicts."""

    def __init__(self) -> None:
        self.private: set[str] = set()
        self.open_pulls: dict[str, list[int]] = {}
        # (repo, environment) -> newest first: (id, sha, created_at, state, environment_url)
        self.deployments: dict[tuple[str, str], list[tuple[int, str, str, str, str]]] = {}
        self.calls: list[str] = []

    def deploy(self, repo: str, environment: str, *items: tuple[str, str, str]) -> None:
        start = len(self.deployments) * 100
        self.deployments[(repo, environment)] = [
            (start + index, sha, "2026-10-02T12:00:00Z", state, url) for index, (sha, state, url) in enumerate(items)
        ]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != "api.github.com":
            return httpx2.Response(502 if "down" in request.url.host else 200)
        path = request.url.path
        self.calls.append(str(request.url))
        parts = path.split("/")
        repo = f"{parts[2]}/{parts[3]}"
        if len(parts) == 4:
            return httpx2.Response(200, json={"private": repo in self.private})
        if parts[4] == "pulls":
            return httpx2.Response(200, json=[{"number": n} for n in self.open_pulls.get(repo, [])])
        if len(parts) == 5:
            environment = request.url.params["environment"]
            per_page = int(request.url.params.get("per_page", "30"))
            page = int(request.url.params.get("page", "1"))
            rows = self.deployments.get((repo, environment), [])[(page - 1) * per_page : page * per_page]
            return httpx2.Response(200, json=[{"id": i, "sha": sha, "created_at": at} for i, sha, at, _, _ in rows])
        deployment_id = int(parts[5])
        for rows in self.deployments.values():
            for i, _, _, state, url in rows:
                if i == deployment_id:
                    return httpx2.Response(200, json=[{"state": state, "environment_url": url}])
        return httpx2.Response(404)


def _client(github: FakeGitHub) -> TestClient:
    return TestClient(create_app(httpx2.MockTransport(github.handler)))


def test_repo_badge_counts_only_previews_of_open_pull_requests() -> None:
    github = FakeGitHub()
    github.deploy("o/site", "prod", ("a" * 40, "success", "https://site.example.com"))
    github.deploy("o/site", "pr-7", ("b" * 40, "in_progress", ""))
    # pr-3 has a deployment from its teardown, but its pull request is closed.
    github.deploy("o/site", "pr-3", ("c" * 40, "success", ""))
    github.open_pulls["o/site"] = [7]
    with _client(github) as client:
        svg = client.get("/b/o/site.svg?previews=list")
        data = client.get("/b/o/site.json").json()
    assert svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["cache-control"] == "public, max-age=60"
    assert "#7" in svg.text and "#3" not in svg.text
    assert data["label"] == "kitshn" and data["message"].startswith("prod live") and data["message"].endswith("1 preview")
    assert data["logoSvg"].startswith("<svg")


def test_a_running_or_failed_deploy_reports_the_commit_that_still_serves() -> None:
    github = FakeGitHub()
    github.deploy("o/site", "prod", ("n" * 40, "failure", ""), ("o" * 40, "success", "https://site.example.com"))
    with _client(github) as client:
        status = client.get("/b/o/site/prod.json").json()["message"]
        ref = client.get("/b/o/site/prod.json?show=ref").json()["message"]
    assert status == "live · deploy failed"
    assert ref.startswith("ooooooo")


def test_env_badge_probes_the_environment_url() -> None:
    github = FakeGitHub()
    github.deploy("o/site", "prod", ("a" * 40, "success", "https://down.example.com"))
    with _client(github) as client:
        assert client.get("/b/o/site/prod.json").json()["message"] == "down · 502"


def test_responses_are_cached_between_badge_requests() -> None:
    github = FakeGitHub()
    github.deploy("o/site", "prod", ("a" * 40, "success", ""))
    with _client(github) as client:
        client.get("/b/o/site/prod.svg")
        first = len(github.calls)
        client.get("/b/o/site/prod.svg")
    assert first == 3 and len(github.calls) == first


def test_private_and_missing_repos_get_the_same_unknown_badge() -> None:
    github = FakeGitHub()
    github.private.add("o/secret")
    github.deploy("o/secret", "prod", ("a" * 40, "success", ""))
    with _client(github) as client:
        private = client.get("/b/o/secret/prod.json").json()
        missing = client.get("/b/o/nothing/prod.json").json()
    assert private == missing and private["message"] == "unknown"
    assert not any("/deployments" in call and "secret" in call for call in github.calls)


def test_bad_options_are_rejected_with_the_allowed_values() -> None:
    with _client(FakeGitHub()) as client:
        response = client.get("/b/o/site.svg?previews=all")
    assert response.status_code == 400 and "count, list, dots, hide" in response.text


@pytest.mark.parametrize("mode", ["count", "dots"])
def test_one_preview_is_singular_in_every_layout(mode: PreviewMode) -> None:
    badge = repo_badge([env("prod"), env("pr-9")], {}, mode, NOW)
    assert badge.message.endswith("· 1 preview")


def test_rhythm_reads_every_page_inside_the_thirty_days() -> None:
    github = FakeGitHub()
    now = datetime.now(UTC)
    # 150 deploys over the last 15 days: two pages of the GitHub API.
    github.deployments[("o/site", "prod")] = [
        (index, "a" * 40, (now - timedelta(hours=index * 2)).isoformat(), "success", "") for index in range(150)
    ]
    with _client(github) as client:
        assert client.get("/b/o/site/prod.json?show=rhythm").json()["message"] == "150 / 30d"
