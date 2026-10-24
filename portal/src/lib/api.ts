// Typed client for the gateway's portal API. With NEXT_PUBLIC_API_BASE unset, every call is served
// from the fixtures in ./fixtures (shaped exactly like the Go JSON), so the portal runs with no gateway.

import graphFixture from "./fixtures/graph.json";
import queueFixture from "./fixtures/queue.json";
import statsFixture from "./fixtures/stats.json";
import type {
  GateRecord,
  GraphResponse,
  QueueResponse,
  ResolveRequest,
  ResolveResponse,
  Stats,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
export const USING_FIXTURES = API_BASE === "";

/** The instant the fixtures were generated at; fixture timestamps are shifted so it reads as "now". */
const FIXTURE_NOW = Date.parse("2026-10-25T12:00:00Z");

function shiftTimes<T>(value: T, offsetMs: number): T {
  if (Array.isArray(value)) return value.map((v) => shiftTimes(v, offsetMs)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if ((k === "ts" || k === "updated" || k === "created_at" || k === "fit_at") && typeof v === "string") {
        out[k] = new Date(Date.parse(v) + offsetMs).toISOString();
      } else {
        out[k] = shiftTimes(v, offsetMs);
      }
    }
    return out as T;
  }
  return value;
}

function fixture<T>(data: unknown): Promise<T> {
  const copy = shiftTimes(structuredClone(data), Date.now() - FIXTURE_NOW) as T;
  return new Promise((resolve) => setTimeout(() => resolve(copy), 120));
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include", headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  /** GET /api/queue → holds routed to a human (dedup / human) and blocks, oldest first. */
  getQueue(): Promise<QueueResponse> {
    if (USING_FIXTURES) return fixture<QueueResponse>(queueFixture);
    return get<QueueResponse>("/api/queue");
  },

  /** GET /api/graph?repo=&since= → open PRs and the relations the gate found between them. */
  async getGraph(repo?: string, since?: string): Promise<GraphResponse> {
    if (USING_FIXTURES) {
      const g = await fixture<GraphResponse>(graphFixture);
      if (!repo) return g;
      const nodes = g.nodes.filter((n) => n.repo === repo);
      const ids = new Set(nodes.map((n) => n.id));
      return { nodes, edges: g.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
    }
    return get<GraphResponse>(`/api/graph${qs({ repo, since })}`);
  },

  /** GET /api/stats?window=<seconds> → store.Stats incl. the drift block. */
  getStats(windowS = 604800): Promise<Stats> {
    if (USING_FIXTURES) return fixture<Stats>({ ...statsFixture, window_s: windowS });
    return get<Stats>(`/api/stats${qs({ window: windowS })}`);
  },

  /** GET /api/verdict/<sha>?repo= → the newest gatelog record for a head sha. */
  async getVerdict(sha: string, repo?: string): Promise<GateRecord> {
    if (USING_FIXTURES) {
      const q = await fixture<QueueResponse>(queueFixture);
      const all = [...q.holds, ...Object.values(q.matches ?? {})];
      const hit = all.find((r) => r.head_sha === sha && (!repo || r.repo === repo));
      if (!hit) throw new Error(`no decision for ${sha}`);
      return hit;
    }
    return get<GateRecord>(`/api/verdict/${encodeURIComponent(sha)}${qs({ repo })}`);
  },

  /** POST /api/resolve → records a human resolution. Never merges or closes anything. */
  resolve(req: ResolveRequest): Promise<ResolveResponse> {
    if (USING_FIXTURES) return fixture<ResolveResponse>({ ok: true, logged_at: new Date(FIXTURE_NOW).toISOString(), mock: true });
    return post<ResolveResponse>("/api/resolve", req);
  },

  /** POST /api/calibrate → asks for a refit from the gatelog (mergeone calibrate --from-gatelog). */
  refitCalibration(sinceDays = 7): Promise<{ ok: boolean; mock?: boolean }> {
    if (USING_FIXTURES) return fixture({ ok: true, mock: true });
    return post("/api/calibrate", { since: `${sinceDays}d` });
  },
};
