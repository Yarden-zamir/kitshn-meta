# kitshn-meta

[![kitshn](https://kitshn.yarden-zamir.com/b/Yarden-zamir/kitshn-meta.svg)](https://kitshn.yarden-zamir.com)

The [KitSHn](https://github.com/Yarden-zamir/kitshn) landing page and its deploy badge server, at
[kitshn.yarden-zamir.com](https://kitshn.yarden-zamir.com). It is itself a KitSHn recipe, on the
same VPS as the deployments its badges describe.

## Badges

Every badge is an SVG, with a `.json` twin in the
[shields.io endpoint](https://shields.io/badges/endpoint-badge) format.

| URL | Shows |
|---|---|
| `/b/<owner>/<repo>.svg` | The main environment's state, then the pull request previews. `?previews=count` (default), `list`, `dots`, or `hide`. |
| `/b/<owner>/<repo>/<env>.svg` | One environment: live, deploying, live with a failed last deploy, or down with the HTTP status. |
| `…/<env>.svg?show=ref` | The deployed commit and its age. |
| `…/<env>.svg?show=rhythm` | Deploys per day for the last 30 days. |
| `…/<env>.svg?show=edge` | The public route's HTTP status and response time. |
| `/b/static.svg` | "Deployed with kitshn", with no state. |

Badges are served for public repos only. A private or unknown repo gets the same "unknown" badge
as a repo with no deployment, so the server does not reveal that a private repo exists.

## Where the data comes from

The KitSHn deploy workflow records every deploy as a GitHub deployment, in an Environment named
after the KitSHn environment, with the statuses in progress, success, or failure. The server reads
those through the GitHub API:

- The latest deployment and its status give the state, the commit, and the public URL.
- The open pull requests decide which `pr-<number>` previews count. A closed pull request's
  Environment stays on GitHub, so its deployments alone cannot tell.
- The deployment list gives the deploys per day for the rhythm badge.

Answers are cached: state for 60 seconds, history for 10 minutes, and repo visibility for 6
hours. Conditional requests keep the rate limit low, and on an error or a rate limit the server
keeps serving its last good answer. It also requests each public URL at most once a minute for
the down state and the response badge. It needs a token in `GITHUB_TOKEN` (the recipe param
`KITSHN_GITHUB_TOKEN`): a fine-grained token with read-only access to public repositories is
enough.

## Develop

```bash
uv sync
uv run pytest
GITHUB_TOKEN=$(gh auth token) uv run uvicorn kitshn_meta.app:app --reload
```

## Deploy

Pushes to `main` deploy `prod`; pull requests deploy `pr-<number>.kitshn.yarden-zamir.com`. After
a push, run `kitshn track`. See `kitshn.md` for the recipe contract.
