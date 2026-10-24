import type { GateRecord, Verdict } from "./types";

export const VERDICT_COLOR: Record<Verdict, string> = {
  pass: "var(--pass)",
  hold: "var(--hold)",
  block: "var(--block)",
};

/** Raw hex for canvas drawing (CSS variables don't resolve inside a 2D context). */
export const HEX = {
  bg: "#050505",
  panel: "#0A0A0A",
  card: "#111111",
  raised: "#171717",
  border: "#262626",
  borderStrong: "#2E2E2E",
  text: "#FAFAFA",
  muted: "#A1A1AA",
  subtle: "#71717A",
  brand: "#A371F7",
  pass: "#3FB950",
  hold: "#D29922",
  block: "#F85149",
} as const;

export function age(iso: string | undefined, now: number | null): string {
  if (!iso || now === null) return "—";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, "0")} m`;
  return `${Math.floor(h / 24)} d`;
}

export const p2 = (n: number | undefined) => (n === undefined || Number.isNaN(n) ? "—" : n.toFixed(2));
export const short = (sha: string) => sha.slice(0, 7);
export const int = (n: number) => new Intl.NumberFormat("en-US").format(Math.round(n));
export const pct = (n: number) => `${Math.round(n * 100)}`;
export const prKey = (repo: string, pr: number | undefined) => `${repo}#${pr ?? 0}`;

export interface ParsedState {
  title: string;
  body: string;
  files: { path: string; ins: number; del: number }[];
  ins: number;
  del: number;
  diff: string;
}

/** Parses server.RenderState output back into its parts (the exact input the model saw). */
export function parseState(state: string | undefined): ParsedState {
  const out: ParsedState = { title: "", body: "", files: [], ins: 0, del: 0, diff: "" };
  if (!state) return out;
  out.title = /^PULL REQUEST TITLE: (.*)$/m.exec(state)?.[1] ?? "";
  out.body = /MERGE MESSAGE BODY:\n([\s\S]*?)\n\nSUMMARY:/.exec(state)?.[1] ?? "";
  const sum = /SUMMARY: \d+ files changed, \+(\d+) \/ -(\d+) lines/.exec(state);
  if (sum) {
    out.ins = Number(sum[1]);
    out.del = Number(sum[2]);
  }
  const files = /FILES CHANGED \(path \+ins -del\):\n([\s\S]*?)\n\nDIFF/.exec(state)?.[1] ?? "";
  for (const line of files.split("\n")) {
    const m = /^\s+(\S+) \+(\d+) -(\d+)$/.exec(line);
    if (m) out.files.push({ path: m[1], ins: Number(m[2]), del: Number(m[3]) });
  }
  out.diff = /DIFF \(first \d+ chars(?:, truncated)?\):\n([\s\S]*)$/.exec(state)?.[1] ?? "";
  return out;
}

export function title(r: GateRecord): string {
  return parseState(r.state).title || `PR #${r.pr ?? "?"}`;
}

/** Questions the policy reads, in display order; anything else in probs follows. */
export const QUESTION_ORDER = [
  "duplicate_of",
  "needs_human_review",
  "weakens_tests",
  "is_logic_change",
  "touches_concurrency",
  "message_matches_diff",
  "silent_days",
];

/** Questions where a high answer is a risk; the rest (message_matches_diff, is_logic_change) are drawn neutral. */
export const RISK_QUESTIONS = new Set(["weakens_tests", "needs_human_review", "touches_concurrency"]);

/** Tailwind text colour for a probability chip. */
export function riskText(q: string, p: number): string {
  if (!RISK_QUESTIONS.has(q)) return "text-subtle";
  return p >= 0.8 ? "text-block" : p >= 0.5 ? "text-hold" : "text-subtle";
}
