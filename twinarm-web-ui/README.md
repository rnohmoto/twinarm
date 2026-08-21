# TwinArm Web UI

The web-based teleoperation UI for the Koch v1.1 arms. It runs entirely against a mock backend
today: there is no server, and nothing here has ever been connected to hardware.

Part of the TwinArm monorepo; see [`../README.md`](../README.md).

## Status

Scaffolding, plus one worked example slice (a dashboard showing mocked telemetry and switching the
force-feedback mode). The point of the example is to fix the conventions — layer boundaries, the
contract, the test seams — not to be a useful screen.

## Stack

| Concern         | Choice                                                                         |
| --------------- | ------------------------------------------------------------------------------ |
| Framework       | React 19 with the React Compiler, Vite 8, TypeScript 6 (`strict`)              |
| Architecture    | [Feature-Sliced Design](https://feature-sliced.design/), enforced by `steiger` |
| Styling         | Tailwind CSS v4 — configured in CSS (`src/index.css`), no `tailwind.config.js` |
| Contract        | zod schemas in `src/shared/api`, mocked with [MSW](https://mswjs.io/)          |
| Tests           | Vitest and Testing Library for units and components, Playwright for E2E        |
| Lint and format | ESLint 10 (flat config) and Prettier                                           |

## Setup

Node is pinned in `mise.toml` and installed by [mise](https://mise.jdx.dev/):

```bash
mise install       # node 24.19.0
mise run install   # npm ci
```

## Commands

Checks run as mise tasks, defined in `mise.toml`:

```bash
mise run format   # eslint --fix, then prettier --write (-c/--check to verify only)
mise run type     # tsc --build
mise run test     # vitest run
mise run fsd      # steiger: layer and public-API rules
mise run check    # format --check + type + fsd + test; writes nothing
mise run e2e      # playwright test (not part of check)
```

The dev server is plain npm: `npm run dev`. It serves the MSW mocks, so the dashboard has data.

`mise run e2e` needs browsers installed once with `npx playwright install chromium`. It starts its
own dev server, so the mocks are live there too.

From the repository root, use the monorepo path instead: `mise run //twinarm-web-ui:check`.

## Structure

```
src/
  app/        application root
  pages/      one slice per screen — dashboard
  widgets/    composed page blocks (empty)
  features/   user actions — ff-mode
  entities/   business objects — telemetry
  shared/     contract, config, helpers, UI primitives
e2e/          Playwright specs
```

Each layer has a README explaining what belongs in it; the rules themselves are in
[`src/README.md`](src/README.md).

## The backend contract

There is no backend. [`src/shared/api`](src/shared/api/README.md) defines the telemetry and command
contract as zod schemas, derived from the working prototype
[`../descovery/koch_web_panel.py`](../descovery/koch_web_panel.py) — Server-Sent Events on
`GET /stream`, commands on `GET /ctl?c=<url-encoded JSON>`. Treat it as **provisional**: when a real
backend appears, reconcile it there first.

MSW implements that contract for the dev server and the E2E tests. Component tests do not use MSW;
they inject a fake `TelemetrySource`, which is why that interface exists.
