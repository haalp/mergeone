# mergeone portal (v0.3 front end)

Three views over the gatelog — **Queue**, **Graph**, **Evidence** — as a Next.js (App Router, TypeScript) app with
Tailwind v4, shadcn/ui and EasyUI. ROADMAP.md "v0.3 — the portal" is the spec; the design canvas is the layout.
With no gateway configured it runs entirely on fixtures shaped exactly like the Go JSON.

## Run

```sh
cd portal
npm install
npm run dev          # http://localhost:3000 → redirects to /queue
npm run build        # production build (all three routes prerender as static shells)
npm run start        # serve the build
npm run lint
```

### API base

| `NEXT_PUBLIC_API_BASE` | data source |
|---|---|
| unset (default) | `src/lib/fixtures/{queue,graph,stats}.json`, timestamps shifted so the newest reads as "now"; resolutions and refits are mocked |
| `http://localhost:8080` (or `""` behind the same origin later) | the gateway's `/api/*` routes, `credentials: "include"` |

```sh
NEXT_PUBLIC_API_BASE=http://localhost:8080 npm run dev
```

The value is inlined at build time (it is `NEXT_PUBLIC_*`), so set it before `npm run build`.

## Routes

| route | what it shows | data |
|---|---|---|
| `/` | redirects to `/queue` | — |
| `/queue` | holds routed to a human (dedup / human) and blocks, oldest first; Tabs filter; right Sheet with the exact model input as diff blocks, calibrated probabilities as Progress bars, the rule; three resolutions (link as duplicate · approve as is · request rebase) | `GET /api/queue`, `POST /api/resolve` |
| `/graph` | d3-force layout on a `<canvas>`: node = PR (ring = verdict, size = diff), edge = relation (thickness = p); click a node → its cluster in the side panel, click an edge → the pair; zoom / fit; deep link to `/queue#owner/repo#pr` | `GET /api/graph?repo=` |
| `/evidence` | 5 stat cards, drift per question (8 weekly means, state badge, alert when degraded), rules table, gatebench list, refit dialog, live decisions/h, decisions/day | `GET /api/stats?window=`, `POST /api/calibrate` |

The repo picker in the top nav filters all three views.

## What the Go side needs to implement

All JSON field names are the existing Go `json:` tags (`src/lib/types.ts` mirrors them).

| method + path | response | notes |
|---|---|---|
| `GET /api/queue` | `{"holds": Record[], "matches"?: {"owner/repo#N": Record}}` | `holds`: latest `store.Record` per open PR whose verdict is `hold` (route `dedup`/`human`) or `block`, oldest first. `matches` (optional): latest record of each hold's `result.extra.duplicate_candidate`, so the Sheet can show B's diff. |
| `GET /api/graph?repo=&since=` | `{"nodes": GraphNode[], "edges": GraphEdge[]}` | node: `{id:"owner/repo#N", repo, pr, title, head_sha, verdict, route, rule, additions, deletions, files, author?, updated?}`; edge: `{source, target, relation:"duplicate_of", p, type?, note?}` with `type ∈ same_change\|backport\|competing\|builds_on` only once run 8 ships typed edges (absent → drawn as a plain purple `duplicate_of`). |
| `GET /api/stats?window=<s>` | `store.Stats` as today (`window_s, decisions, per_hour, verdicts, routes, rules, cost_usd, error_rate, p50_ms, p99_ms, drift`) **plus optional** `history.daily[] {day, decisions, pass, hold, block, cost_usd}`, `drift_weekly {model: {question: number[8]}}`, `resolutions {total, agreed, approved_as_is, linked, rebase_requested, by_rule{rule:{agreed, overridden}}}`, `calibration {model, fit_at, n, file?}` | Same handler as `/stats`. Every extension is optional; the page shows "—" or hides the widget without it. |
| `GET /api/verdict/<sha>?repo=` | `store.Record` | Like `/verdict/{owner}/{repo}/{sha}` but returns the whole record (incl. `state`), not just `result`. |
| `POST /api/resolve` | body `{repo, pr, head_sha, action: "approve"\|"link_duplicate"\|"request_rebase", target_pr?}` → `{ok, logged_at}` | Records the resolution with the viewer's login and the head SHA. Never merges or closes. |
| `POST /api/calibrate` | body `{since: "7d"}` → `{ok}` | Optional; the dialog is mocked until it exists. |

Two fields the portal reads that the gateway does not write yet:

- `Record.context` — `{author, base, created_at}` from `engine.PRMeta` (the field exists on `store.Record` but is never set).
  Without it the queue rows drop the agent name and the "same base" bar.
- Title, files, +/− and the diff are **not** new fields: the portal parses them from `Record.state` (the exact
  `server.RenderState` output), see `parseState` in `src/lib/format.ts`.

"watch" on the drift table (shift ≥ 0.15, not degraded) is a portal-side hint only; "degraded" is the gateway's flag.

## Components

shadcn/ui primitives (`src/components/ui/`): button, badge, card, tabs, sheet, table, select, progress, tooltip,
alert, dialog, sonner (+ `utils`).

EasyUI (from the shadcn GitHub registry `Surajmaurya1/easyui`), and where each is used:

| component | used in |
|---|---|
| `stat-card` (`StatCard`, `StatGrid`) | Evidence — the five top stat cards |
| `bar-chart` | Evidence — drift, 8 weekly means per question |
| `live-line-chart` | Evidence — decisions per hour |
| `activity-heatmap` | Evidence — decisions per day |
| `magnetic-button` | Queue Sheet — the primary resolution |

Two lint fixes were made to vendored EasyUI files (`magnetic-button.tsx`: `as any` → `HTMLMotionProps<'button'>`;
`stat-card.tsx`: ref assignment moved out of render). `magnetic-button` asked to overwrite `lib/utils.ts`; the answer
was no (shadcn's `cn` is kept; same signature).

### Install commands that were run

```sh
npx create-next-app@latest portal --ts --tailwind --app --eslint --src-dir --import-alias "@/*" --use-npm --no-git

# shadcn: `npx shadcn@latest init -d` FAILED in the build sandbox — ui.shadcn.com is blocked there
#   ("Request to https://ui.shadcn.com/init?... failed, reason: Request was cancelled"; proxy 403 on CONNECT).
# components.json was written by hand with what init -d writes (new-york-v4, neutral, CSS variables, lucide),
# and `shadcn add` ran against a local mirror of the official registry built from github.com/shadcn-ui/ui
# (apps/v4/registry/new-york-v4) via the CLI's REGISTRY_URL override:
REGISTRY_URL=http://127.0.0.1:8765/r npx shadcn@latest add button badge card tabs sheet table select progress tooltip alert dialog sonner --yes
REGISTRY_URL=http://127.0.0.1:8765/r npx shadcn@latest add utils --yes
npm i lucide-react tw-animate-css class-variance-authority      # what init would have installed

# EasyUI, straight from the GitHub registry (worked as documented):
npx shadcn@latest add Surajmaurya1/easyui/stat-card --yes
npx shadcn@latest add Surajmaurya1/easyui/bar-chart --yes
npx shadcn@latest add Surajmaurya1/easyui/live-line-chart --yes
npx shadcn@latest add Surajmaurya1/easyui/activity-heatmap --yes
npx shadcn@latest add Surajmaurya1/easyui/magnetic-button          # answered "n" to overwriting lib/utils.ts

npm i d3-force geist && npm i -D @types/d3-force
```

On a normal network `npx shadcn@latest init -d` and plain `npx shadcn@latest add …` work; the result is the same files.

### Fonts

Geist and Geist Mono come from the `geist` package (`geist/font/sans`, `geist/font/mono`, which wrap
`next/font/local`) instead of `next/font/google`: Google Fonts is unreachable from the build sandbox and
`next/font/google` downloads at build time. Same families, same `--font-geist-sans` / `--font-geist-mono` variables.

### Theme

`src/app/globals.css` maps the canvas tokens onto shadcn's variables (dark only, `<html class="dark">`):
background `#050505`, card `#111`, border `#262626`, zinc text, ring = brand; plus `--brand #A371F7`,
`--pass #3FB950`, `--hold #D29922`, `--block #F85149` exposed as Tailwind colours (`text-hold`, `bg-brand`, …).
