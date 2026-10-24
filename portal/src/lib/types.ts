// JSON shapes of the Go gateway (gate/internal/store, gate/internal/engine).
// Field names are the Go `json:` tags; keep them in sync with the structs.

export type Verdict = "pass" | "hold" | "block";

/** engine.Result.Extra["candidates"][i] — one PR-memory hit the pair question was asked about. */
export interface Candidate {
  number: number;
  state: string;
  title: string;
  retrieval: number;
  duplicate_of: number;
}

/** engine.ActionsReport */
export interface ActionsReport {
  applied: string[];
  skipped?: string;
  errors: string[];
}

/** engine.Result */
export interface Result {
  verdict: Verdict;
  route: string;
  reason: string;
  rule: string;
  probs: Record<string, number>;
  raw_probs: Record<string, number>;
  model_version: string;
  latency_ms: number;
  model_latency_sum_ms?: number;
  cost_usd: number;
  input_sha256: string;
  error?: string;
  ts: string;
  extra?: {
    candidates?: Candidate[];
    duplicate_candidate?: number;
    silent_days?: number;
    [k: string]: unknown;
  };
  drift?: string[];
  rounds?: number;
  capped_rule?: string;
  actions?: ActionsReport;
}

/**
 * store.Record — one gatelog line. `context` is unused by the gateway today; the portal reads
 * `author`, `base` and `created_at` from it when present (see README, "what the Go side needs").
 */
export interface GateRecord {
  ts: string;
  repo: string;
  pr?: number;
  head_sha: string;
  source: string;
  result: Result;
  context?: { author?: string; base?: string; created_at?: string; [k: string]: unknown };
  /** The exact input the model saw (server.RenderState). */
  state?: string;
}

/** GET /api/queue */
export interface QueueResponse {
  holds: GateRecord[];
  /** Latest record of each held PR's matched PR, keyed "owner/repo#number". Optional. */
  matches?: Record<string, GateRecord>;
}

export type EdgeType = "same_change" | "backport" | "competing" | "builds_on";

export interface GraphNode {
  id: string; // "owner/repo#pr"
  repo: string;
  pr: number;
  title: string;
  head_sha: string;
  verdict: Verdict;
  route: string;
  rule: string;
  additions: number;
  deletions: number;
  files: number;
  author?: string;
  updated?: string;
}

export interface GraphEdge {
  source: string; // node id
  target: string; // node id
  relation: "duplicate_of";
  /** calibrated probability */
  p: number;
  /** run-8 typed relation; absent → plain duplicate_of edge */
  type?: EdgeType;
  note?: string;
}

/** GET /api/graph?repo= */
export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** engine.QuestionDrift */
export interface QuestionDrift {
  mean_raw: number;
  shift: number;
  yes_rate: number;
  spread: number;
  n: number;
  ref_mean_raw: number;
  ref_spread: number;
  ref_n: number;
  degraded: boolean;
}

/** engine.ModelDrift */
export interface ModelDrift {
  reference: "gatelog" | "fit_stats" | "none";
  questions: Record<string, QuestionDrift>;
  degraded: string[];
}

export interface DailyRow {
  day: string;
  decisions: number;
  pass: number;
  hold: number;
  block: number;
  cost_usd: number;
}

export interface Resolutions {
  total: number;
  agreed: number;
  approved_as_is: number;
  linked: number;
  rebase_requested: number;
  by_rule: Record<string, { agreed: number; overridden: number }>;
}

/** GET /api/stats — store.Stats, plus optional portal extensions the gateway does not emit yet. */
export interface Stats {
  window_s: number;
  decisions: number;
  per_hour: number;
  verdicts: Record<string, number>;
  routes: Record<string, number>;
  rules: Record<string, number>;
  cost_usd: number;
  error_rate: number;
  p50_ms: number | null;
  p99_ms: number | null;
  drift: Record<string, ModelDrift>;
  // ---- extensions (optional; the page degrades without them)
  history?: { daily: DailyRow[] };
  drift_weekly?: Record<string, Record<string, number[]>>;
  resolutions?: Resolutions;
  calibration?: { model: string; fit_at: string; n: number; file?: string };
}

export type Resolution = "approve" | "link_duplicate" | "request_rebase";

/** POST /api/resolve body */
export interface ResolveRequest {
  repo: string;
  pr: number;
  head_sha: string;
  action: Resolution;
  /** the matched PR for link_duplicate / request_rebase */
  target_pr?: number;
}

export interface ResolveResponse {
  ok: boolean;
  logged_at: string;
  mock?: boolean;
}
