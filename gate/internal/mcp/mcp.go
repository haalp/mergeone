// Package mcp exposes the gateway to coding agents over the Model Context Protocol (stdio).
package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	gomcp "github.com/mark3labs/mcp-go/mcp"
	mcpserver "github.com/mark3labs/mcp-go/server"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/plan"
	"github.com/haalp/mergeone/gate/internal/server"
	"github.com/haalp/mergeone/gate/internal/store"
)

// StateFn renders a PR state from a local git repo (injected by main to avoid an import cycle).
type StateFn func(repo, base, head string) (string, []string, error)

// Serve runs the MCP server on stdio until the client disconnects (or SIGINT/SIGTERM).
func Serve(gw *server.Gateway, fromGit StateFn) error {
	return mcpserver.ServeStdio(newServer(gw, fromGit))
}

// ServeIO runs the MCP server over in/out until ctx is cancelled or in reaches EOF. A cancelled ctx
// is a clean stop, not an error. It installs no signal handlers: the caller owns ctx.
func ServeIO(ctx context.Context, gw *server.Gateway, fromGit StateFn, in io.Reader, out io.Writer) error {
	ss := mcpserver.NewStdioServer(newServer(gw, fromGit))
	ss.SetErrorLogger(log.New(os.Stderr, "", log.LstdFlags))
	err := ss.Listen(ctx, in, out)
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}

func newServer(gw *server.Gateway, fromGit StateFn) *mcpserver.MCPServer {
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

	s.AddTool(gomcp.NewTool("gate_similar",
		gomcp.WithDescription("Find recent pull requests in the repository's memory that look like this change (file overlap + text), with the model's duplicate_of probability for each. Use before opening a PR to avoid duplicating landed or open work."),
		gomcp.WithString("repo", gomcp.Required(), gomcp.Description("owner/name")),
		gomcp.WithString("title", gomcp.Required()),
		gomcp.WithString("body", gomcp.DefaultString("")),
		gomcp.WithString("paths", gomcp.Description("comma-separated changed paths")),
		gomcp.WithString("author", gomcp.DefaultString("")),
		gomcp.WithNumber("k", gomcp.DefaultNumber(5)),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		if gw.Memory == nil {
			return errResult("PR memory is not enabled in the policy (memory.enabled)"), nil
		}
		args := req.Params.Arguments
		repo, _ := args["repo"].(string)
		title, _ := args["title"].(string)
		body, _ := args["body"].(string)
		author, _ := args["author"].(string)
		var paths []string
		if ps, _ := args["paths"].(string); ps != "" {
			for _, p := range strings.Split(ps, ",") {
				if p = strings.TrimSpace(p); p != "" {
					paths = append(paths, p)
				}
			}
		}
		k := 5
		if v, ok := args["k"].(float64); ok && v > 0 {
			k = int(v)
		}
		aState := server.RenderState(title, body, nil, "")
		meta := engine.PRMeta{Repo: repo, Title: title, Body: body, Author: author, Paths: paths, CreatedAt: time.Now().UTC()}
		out := []map[string]any{}
		for _, c := range gw.Memory.Candidates(meta, aState, k) {
			row := map[string]any{"number": c.Number, "title": c.Title, "state": c.State, "retrieval_score": c.Score}
			if gw.Gate.Model != nil {
				d := gw.Gate.Model.Decide(c.PairState, []string{"same_change"})
				if d.Error == "" {
					row["duplicate_of"] = d.Probs["same_change"]
				} else {
					row["error"] = d.Error
				}
			}
			out = append(out, row)
		}
		return jsonResult(map[string]any{"repo": repo, "candidates": out, "memory_size": gw.Memory.Repo(repo).Len()}), nil
	})

	s.AddTool(gomcp.NewTool("gate_queue",
		gomcp.WithDescription("Pull requests that wait for a person: the latest head of every open PR the gate held or blocked and nobody has answered yet, oldest first. "+
			"Each row has the verdict, route, rule and reason, and for a duplicate hold the other pull request. Read from the gatelog as it is on disk now, so it includes what a running gateway or `mergeone pr` wrote since this server started. No model call; nothing is written."),
		gomcp.WithString("repo", gomcp.Description("owner/name; omit for every repository in the log")),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		repo, _ := req.Params.Arguments["repo"].(string)
		rows := server.QueueRows(store.View(gw.Store.Path()), repo)
		return jsonResult(map[string]any{"as_of": time.Now().UTC().Format(time.RFC3339), "count": len(rows), "holds": rows, "never_merges": true}), nil
	})

	s.AddTool(gomcp.NewTool("gate_board",
		gomcp.WithDescription("Everything a read-only board shows, in one answer: the pull requests that wait for a person (with the related check's answer when the policy sets actions.related_context), "+
			"the braid graph (open pull requests as nodes, duplicate_of edges), the gatelog's stats over a window (verdict mix, cost, drift, how often people agreed), and the policy's mode. "+
			"Read from the gatelog as it is on disk now. No model call; nothing is written."),
		gomcp.WithString("repo", gomcp.Description("owner/name; omit for every repository in the log")),
		gomcp.WithNumber("since_days", gomcp.DefaultNumber(14), gomcp.Description("how far back the braid goes")),
		gomcp.WithNumber("window_days", gomcp.DefaultNumber(7), gomcp.Description("the window of the stats")),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		repo, _ := req.Params.Arguments["repo"].(string)
		days := func(k string, def float64) time.Duration {
			if v, ok := req.Params.Arguments[k].(float64); ok && v > 0 && v <= 365 {
				def = v
			}
			return time.Duration(def * 24 * float64(time.Hour))
		}
		return jsonResult(server.Board(store.View(gw.Store.Path()), gw.Gate.Policy, repo, time.Now().Add(-days("since_days", 14)), days("window_days", 7))), nil
	})

	s.AddTool(gomcp.NewTool("gate_resolve",
		gomcp.WithDescription("Record a person's answer to a held or blocked pull request head: approve (keep it as it is), link_duplicate, or request_rebase. "+
			"Written to the gatelog and, when the policy's actions are on, mirrored as the gateway's own label and comment; an approve also answers the related check for that head. "+
			"It never merges or closes a pull request and never changes the verdict's own status. Recorded under the GitHub login of the gateway's token; refused without one."),
		gomcp.WithString("repo", gomcp.Required()),
		gomcp.WithNumber("pr", gomcp.Required()),
		gomcp.WithString("head_sha", gomcp.Required(), gomcp.Description("the head the verdict was made on; a newer push needs a new verdict")),
		gomcp.WithString("action", gomcp.Required(), gomcp.Enum("approve", "link_duplicate", "request_rebase")),
		gomcp.WithNumber("target_pr", gomcp.Description("the other pull request, for link_duplicate; defaults to the gate's candidate")),
	), func(ctx context.Context, req gomcp.CallToolRequest) (*gomcp.CallToolResult, error) {
		a := req.Params.Arguments
		body := server.ResolveRequest{}
		body.Repo, _ = a["repo"].(string)
		body.HeadSHA, _ = a["head_sha"].(string)
		body.Action, _ = a["action"].(string)
		if v, ok := a["pr"].(float64); ok {
			body.PR = int(v)
		}
		if v, ok := a["target_pr"].(float64); ok {
			body.TargetPR = int(v)
		}
		if gw.GitHub == nil {
			return errResult("a resolution is recorded under a GitHub login, and this gateway has no GITHUB_TOKEN to take one from; set it for `mergeone mcp`, or decide on the portal"), nil
		}
		login, err := gw.GitHub.Login()
		if err != nil {
			return errResult("could not read the token's GitHub login: " + err.Error()), nil
		}
		res, _, err := gw.Resolve(store.View(gw.Store.Path()), body, login)
		if err != nil {
			return errResult(err.Error()), nil
		}
		return jsonResult(map[string]any{"ok": true, "resolution": res, "never_merges": true}), nil
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

	return s
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
