// Package mcp exposes the gateway to coding agents over the Model Context Protocol (stdio).
package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	gomcp "github.com/mark3labs/mcp-go/mcp"
	mcpserver "github.com/mark3labs/mcp-go/server"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/plan"
	"github.com/haalp/mergeone/gate/internal/server"
)

// StateFn renders a PR state from a local git repo (injected by main to avoid an import cycle).
type StateFn func(repo, base, head string) (string, []string, error)

// Serve runs the MCP server on stdio until the client disconnects.
func Serve(gw *server.Gateway, fromGit StateFn) error {
	s := mcpserver.NewMCPServer("mergeone", "0.2.0",
		mcpserver.WithInstructions("Merge gateway. Call gate_decide before merging; a verdict of 'hold' or 'block' means do not merge."))

	s.AddTool(gomcp.NewTool("gate_decide",
		gomcp.WithDescription("Decide whether a change may merge. Returns verdict (pass|hold|block), route, reason and calibrated probabilities. Give either state (+paths) or repo_path with base/head refs."),
		gomcp.WithString("repo", gomcp.Required(), gomcp.Description("owner/name, used as the gatelog key")),
		gomcp.WithString("head_sha", gomcp.Required(), gomcp.Description("head commit sha (or any stable id for the change)")),
		gomcp.WithString("state", gomcp.Description("rendered PR state; omit to render from repo_path")),
		gomcp.WithString("repo_path", gomcp.Description("local git repo to render the change from")),
		gomcp.WithString("base", gomcp.DefaultString("main")),
		gomcp.WithString("head", gomcp.DefaultString("HEAD")),
		gomcp.WithNumber("pr", gomcp.Description("PR number, if known")),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		args := req.Params.Arguments
		repo, _ := args["repo"].(string)
		sha, _ := args["head_sha"].(string)
		state, _ := args["state"].(string)
		var paths []string
		if state == "" {
			rp, _ := args["repo_path"].(string)
			if rp == "" {
				return errResult("give either state or repo_path"), nil
			}
			base, _ := args["base"].(string)
			head, _ := args["head"].(string)
			if base == "" {
				base = "main"
			}
			if head == "" {
				head = "HEAD"
			}
			var err error
			state, paths, err = fromGit(rp, base, head)
			if err != nil {
				return errResult(err.Error()), nil
			}
		}
		pr := 0
		if v, ok := args["pr"].(float64); ok {
			pr = int(v)
		}
		res := gw.Decide(repo, pr, sha, state, paths, "mcp")
		out := map[string]any{"verdict": res.Verdict, "route": res.Route, "reason": res.Reason, "rule": res.Rule, "probs": res.Probs,
			"model_version": res.ModelVersion, "latency_ms": res.LatencyMS, "error": res.Error,
			"advice": map[string]string{"pass": "You may merge.", "hold": "Do not merge; request the route named.", "block": "Do not merge; a rule blocked this change."}[res.Verdict]}
		return jsonResult(out), nil
	})

	s.AddTool(gomcp.NewTool("gate_verdict",
		gomcp.WithDescription("Latest stored decision for a head sha (no model call)."),
		gomcp.WithString("repo", gomcp.Required()), gomcp.WithString("head_sha", gomcp.Required()),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		args := req.Params.Arguments
		rec := gw.Store.Latest(args["repo"].(string), args["head_sha"].(string))
		if rec == nil {
			return errResult("no decision for that sha"), nil
		}
		return jsonResult(rec.Result), nil
	})

	s.AddTool(gomcp.NewTool("gate_plan",
		gomcp.WithDescription("Landing plan for the open PRs this gateway has seen: waves of non-conflicting PRs and the conflicts that force ordering."),
		gomcp.WithString("repo", gomcp.Required()), gomcp.WithBoolean("semantic", gomcp.Description("also ask the model about semantic conflicts")),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		args := req.Params.Arguments
		repo := args["repo"].(string)
		var m engine.Model
		if b, _ := args["semantic"].(bool); b {
			m = gw.Gate.Model
		}
		return jsonResult(plan.Landing(repo, gw.Store.OpenPRs(repo), m, 200, 0.6)), nil
	})

	s.AddTool(gomcp.NewTool("gate_stats",
		gomcp.WithDescription("Gateway throughput and verdict mix over a time window."),
		gomcp.WithNumber("window_s", gomcp.DefaultNumber(3600)),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		w := 3600.0
		if v, ok := req.Params.Arguments["window_s"].(float64); ok {
			w = v
		}
		return jsonResult(gw.Store.Stats(secs(w))), nil
	})

	return mcpserver.ServeStdio(s)
}

func errResult(msg string) *gomcp.CallToolResult {
	r := gomcp.NewToolResultText(msg)
	r.IsError = true
	return r
}

func jsonResult(v any) *gomcp.CallToolResult {
	b, _ := json.MarshalIndent(v, "", "  ")
	return gomcp.NewToolResultText(string(b))
}

func secs(s float64) time.Duration { return time.Duration(s * float64(time.Second)) }

var _ = fmt.Sprintf
