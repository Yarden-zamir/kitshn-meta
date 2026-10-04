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

KitSHn's `deploy` and `destroy` write one status file per deployment under
`/logs/.kitshn/status/<owner>/<repo>/<environment>.json`. The container mounts only that folder,
read-only. It never sees recipe checkouts or params. The server adds two network lookups, each
cached: GitHub's public API for the repo's visibility (6 hours), and a request to the
deployment's public URL (60 seconds).

## Develop

```bash
uv sync
uv run pytest
STATUS_DIR=./sample-status uv run uvicorn kitshn_meta.app:app --reload
```

## Deploy

Pushes to `main` deploy `prod`; pull requests deploy `pr.<number>.kitshn.yarden-zamir.com`. After
a push, run `kitshn track`. See `kitshn.md` for the recipe contract.
