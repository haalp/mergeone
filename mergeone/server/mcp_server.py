"""mergeone as an MCP server: the agent asks the gate before it merges.

    mergeone mcp --policy .mergeone.yaml [--db .mergeone/gate.db] [--mock]

Tools
  gate_decide(repo, head_sha, state?, paths?, repo_path?, base?, head?)  -> verdict, route, reason, probs
      Give either a rendered `state` (+paths) or a local `repo_path` with base/head refs; the
      server renders the PR state from git the same way the benchmark did.
  gate_verdict(repo, head_sha)                                         -> latest stored decision
  gate_plan(repo)                                                      -> landing plan for the open PRs the gateway knows about
  gate_stats(window_s=3600)                                            -> throughput, verdict mix, p50/p99, cost

Register with Claude Code:
  claude mcp add mergeone -- mergeone mcp --policy .mergeone.yaml
and tell the agent in CLAUDE.md: "Before merging, call gate_decide; do not merge on hold/block."
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .app import Gateway
from .plan import landing_plan


def build(gw: Gateway) -> FastMCP:
    mcp = FastMCP("mergeone", instructions="Merge gateway. Call gate_decide before merging; a verdict of 'hold' or 'block' means do not merge.")

    @mcp.tool()
    def gate_decide(repo: str, head_sha: str, state: str | None = None, paths: list[str] | None = None,
                    repo_path: str | None = None, base: str = "main", head: str = "HEAD", pr: int | None = None) -> dict:
        """Decide whether a change may merge. Returns verdict (pass|hold|block), route, reason, calibrated probabilities."""
        if state is None:
            if not repo_path:
                return {"error": "give either state or repo_path"}
            from ..gate.cli import _state_from_git
            state, paths = _state_from_git(repo_path, base, head)
        res = gw.decide(repo, pr, head_sha, state, paths or [], context={"source": "mcp"})
        d = res.to_dict()
        d["advice"] = {"pass": "You may merge.", "hold": "Do not merge; request the route named.", "block": "Do not merge; a rule blocked this change."}[res.verdict]
        return d

    @mcp.tool()
    def gate_verdict(repo: str, head_sha: str) -> dict:
        """Latest stored decision for a head sha (no model call)."""
        d = gw.store.latest(repo, head_sha)
        if not d:
            return {"error": "no decision for that sha"}
        d.pop("state", None)
        return d

    @mcp.tool()
    def gate_plan(repo: str, semantic: bool = False) -> dict:
        """Landing plan for the open PRs this gateway has seen: waves of non-conflicting PRs, and the conflicts that force ordering."""
        return landing_plan(gw.store.open_prs(repo), gw.gate.model if semantic else None, gw.store, repo)

    @mcp.tool()
    def gate_stats(window_s: int = 3600) -> dict:
        """Gateway throughput and verdict mix over a time window."""
        return gw.store.stats(window_s)

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mergeone mcp")
    ap.add_argument("--policy", default=".mergeone.yaml")
    ap.add_argument("--db", default=".mergeone/gate.db")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)
    model = None
    if a.mock:
        from ..models.mock import MockModel
        model = MockModel()
    gw = Gateway(a.policy, a.db, workers=4, model=model, github_token=os.environ.get("GITHUB_TOKEN"))
    build(gw).run(transport="stdio")


if __name__ == "__main__":
    main()
