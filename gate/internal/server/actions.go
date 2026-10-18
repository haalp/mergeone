package server

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strings"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// DocsURL is linked from every verdict comment.
const DocsURL = "https://github.com/haalp/mergeone#policy-verdict-actions"

// maxCommentPages bounds the marker search (100 comments per page).
const maxCommentPages = 10

// CommentMarker is the hidden line that makes the verdict comment idempotent per head sha.
func CommentMarker(sha string) string { return "<!-- mergeone:" + sha + " -->" }

// ActionsSkip returns why the action layer would do nothing for this PR head, or "" if it would run.
func ActionsSkip(cfg engine.Actions, g *GitHub, repo string, pr int, sha string) string {
	switch {
	case !cfg.Enabled:
		return "disabled (policy actions.enabled is false)"
	case g == nil:
		return "no GitHub token"
	case repo == "" || !strings.Contains(repo, "/"):
		return "no owner/name repository"
	case pr <= 0:
		return "no PR number"
	case sha == "":
		return "no head sha"
	}
	return ""
}

// ApplyActions writes a verdict back to GitHub as policy.actions says: commit status, exclusive labels,
// one comment per head sha, reviewers on hold/block with route "human". It is deterministic (no model
// call), makes no retries, never merges, and never changes the verdict: every failure is returned in
// the report's Errors. targetURL ("" = omitted) links the status to the gatelog entry.
func ApplyActions(g *GitHub, cfg engine.Actions, repo string, pr int, sha string, res engine.Result, targetURL string) *engine.ActionsReport {
	rep := &engine.ActionsReport{Applied: []string{}, Errors: []string{}}
	if s := ActionsSkip(cfg, g, repo, pr, sha); s != "" {
		rep.Skipped = s
		return rep
	}
	if err := cfg.Normalize(); err != nil {
		rep.Errors = append(rep.Errors, "policy: "+err.Error())
		return rep
	}
	va, ok := cfg.For(res.Verdict)
	if !ok {
		rep.Skipped = fmt.Sprintf("unknown verdict %q", res.Verdict)
		return rep
	}
	a := actor{g: g, repo: repo, rep: rep}
	a.status(cfg.StatusContext, va.Status, sha, res, targetURL)
	a.labels(pr, va.Labels, allLabels(cfg))
	if cfg.CommentEnabled() {
		a.comment(pr, sha, res)
	}
	if (res.Verdict == "hold" || res.Verdict == "block") && res.Route == "human" && len(va.Reviewers)+len(va.TeamReviewers) > 0 {
		a.reviewers(pr, va.Reviewers, va.TeamReviewers)
	}
	return rep
}

// ApplyStatusOnly posts just the commit status (used when the worker fails and may retry: labels,
// comment and reviewers wait for a real verdict so the per-sha comment is not spent on an error).
func ApplyStatusOnly(g *GitHub, cfg engine.Actions, repo string, pr int, sha string, res engine.Result) *engine.ActionsReport {
	rep := &engine.ActionsReport{Applied: []string{}, Errors: []string{}}
	if s := ActionsSkip(cfg, g, repo, pr, sha); s != "" {
		rep.Skipped = s
		return rep
	}
	_ = cfg.Normalize()
	if va, ok := cfg.For(res.Verdict); ok {
		actor{g: g, repo: repo, rep: rep}.status(cfg.StatusContext, va.Status, sha, res, "")
	}
	return rep
}

type actor struct {
	g    *GitHub
	repo string
	rep  *engine.ActionsReport
}

func (a actor) url(format string, args ...any) string {
	return fmt.Sprintf("%s/repos/%s/", a.g.API, a.repo) + fmt.Sprintf(format, args...)
}

// call does one request and records a failure (no retry). want lists the acceptable status codes.
func (a actor) call(what, method, u string, body any, want ...int) ([]byte, bool) {
	b, code, err := a.g.do(method, u, "application/vnd.github+json", body)
	if err != nil {
		a.rep.Errors = append(a.rep.Errors, fmt.Sprintf("%s: %s %s: %v", what, method, u, err))
		return nil, false
	}
	for _, c := range want {
		if code == c {
			return b, true
		}
	}
	msg := strings.TrimSpace(string(b))
	if len(msg) > 200 {
		msg = msg[:200]
	}
	a.rep.Errors = append(a.rep.Errors, fmt.Sprintf("%s: %s %s: HTTP %d %s", what, method, u, code, msg))
	return nil, false
}

func (a actor) status(context, state, sha string, res engine.Result, targetURL string) {
	body := map[string]any{"state": state, "context": context, "description": StatusDescription(res)}
	if targetURL != "" {
		body["target_url"] = targetURL
	}
	if _, ok := a.call("status", "POST", a.url("statuses/%s", sha), body, 201, 200); ok {
		a.rep.Applied = append(a.rep.Applied, "status:"+state)
	}
}

// StatusDescription is "<verdict> · <rule> · <reason>" trimmed to GitHub's 140 characters.
func StatusDescription(res engine.Result) string {
	d := strings.Join([]string{res.Verdict, res.Rule, res.Reason}, " · ")
	if r := []rune(d); len(r) > 140 {
		d = string(r[:139]) + "…"
	}
	return d
}

// allLabels is the exclusive set: every label any verdict applies.
func allLabels(cfg engine.Actions) map[string]bool {
	m := map[string]bool{}
	for _, va := range []engine.VerdictAction{cfg.OnPass, cfg.OnHold, cfg.OnBlock} {
		for _, l := range va.Labels {
			m[l] = true
		}
	}
	return m
}

func (a actor) labels(pr int, want []string, exclusive map[string]bool) {
	if len(exclusive) == 0 {
		return
	}
	b, ok := a.call("labels", "GET", a.url("issues/%d/labels?per_page=100", pr), nil, 200)
	if !ok {
		return // without the current set, exclusivity cannot be kept: change nothing
	}
	var cur []struct {
		Name string `json:"name"`
	}
	_ = json.Unmarshal(b, &cur)
	have := map[string]bool{}
	for _, l := range cur {
		have[l.Name] = true
	}
	wantSet := map[string]bool{}
	for _, l := range want {
		wantSet[l] = true
	}
	for _, l := range cur {
		if exclusive[l.Name] && !wantSet[l.Name] {
			if _, ok := a.call("labels", "DELETE", a.url("issues/%d/labels/%s", pr, url.PathEscape(l.Name)), nil, 200, 204, 404); ok {
				a.rep.Applied = append(a.rep.Applied, "label-:"+l.Name)
			}
		}
	}
	var add []string
	for _, l := range want {
		if !have[l] {
			add = append(add, l)
		}
	}
	if len(add) > 0 {
		if _, ok := a.call("labels", "POST", a.url("issues/%d/labels", pr), map[string]any{"labels": add}, 200, 201); ok {
			for _, l := range add {
				a.rep.Applied = append(a.rep.Applied, "label+:"+l)
			}
		}
	}
}

func (a actor) comment(pr int, sha string, res engine.Result) {
	marker := CommentMarker(sha)
	for page := 1; page <= maxCommentPages; page++ {
		b, ok := a.call("comment", "GET", a.url("issues/%d/comments?per_page=100&page=%d", pr, page), nil, 200)
		if !ok {
			return // cannot prove there is no comment yet: do not risk a duplicate
		}
		var cs []struct {
			Body string `json:"body"`
		}
		_ = json.Unmarshal(b, &cs)
		for _, c := range cs {
			if strings.Contains(c.Body, marker) {
				return
			}
		}
		if len(cs) < 100 {
			break
		}
	}
	if _, ok := a.call("comment", "POST", a.url("issues/%d/comments", pr), map[string]any{"body": CommentBody(sha, res)}, 201, 200); ok {
		a.rep.Applied = append(a.rep.Applied, "comment")
	}
}

func (a actor) reviewers(pr int, users, teams []string) {
	b, ok := a.call("reviewers", "GET", a.url("pulls/%d/requested_reviewers", pr), nil, 200)
	if !ok {
		return
	}
	var cur struct {
		Users []struct {
			Login string `json:"login"`
		} `json:"users"`
		Teams []struct {
			Slug string `json:"slug"`
		} `json:"teams"`
	}
	_ = json.Unmarshal(b, &cur)
	haveU, haveT := map[string]bool{}, map[string]bool{}
	for _, u := range cur.Users {
		haveU[strings.ToLower(u.Login)] = true
	}
	for _, t := range cur.Teams {
		haveT[strings.ToLower(t.Slug)] = true
	}
	var addU, addT []string
	for _, u := range users {
		if !haveU[strings.ToLower(u)] {
			addU = append(addU, u)
		}
	}
	for _, t := range teams {
		if !haveT[strings.ToLower(t)] {
			addT = append(addT, t)
		}
	}
	if len(addU)+len(addT) == 0 {
		return
	}
	body := map[string]any{}
	if len(addU) > 0 {
		body["reviewers"] = addU
	}
	if len(addT) > 0 {
		body["team_reviewers"] = addT
	}
	if _, ok := a.call("reviewers", "POST", a.url("pulls/%d/requested_reviewers", pr), body, 201, 200); ok {
		all := append(append([]string{}, addU...), prefixed("team:", addT)...)
		a.rep.Applied = append(a.rep.Applied, "reviewers:"+strings.Join(all, ","))
	}
}

func prefixed(p string, xs []string) []string {
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i] = p + x
	}
	return out
}

// CommentBody renders the verdict comment: verdict and rule, reason, typed questions with calibrated
// probabilities, the memory line when a duplicate candidate was scored, a docs link and the marker.
func CommentBody(sha string, res engine.Result) string {
	var sb strings.Builder
	fmt.Fprintf(&sb, "**mergeone: %s** · rule `%s` · route `%s`\n\n", strings.ToUpper(res.Verdict), res.Rule, res.Route)
	if res.Reason != "" {
		fmt.Fprintf(&sb, "%s\n\n", res.Reason)
	}
	rows := 0
	for _, q := range engine.QuestionsV2 {
		p, ok := res.Probs[q]
		if !ok || math.IsNaN(p) {
			continue
		}
		if rows == 0 {
			sb.WriteString("| question | p |\n|---|---|\n")
		}
		fmt.Fprintf(&sb, "| `%s` | %.2f |\n", q, p)
		rows++
	}
	if rows > 0 {
		sb.WriteString("\n")
	}
	if n := toInt(res.Extra["duplicate_candidate"]); n > 0 {
		fmt.Fprintf(&sb, "memory: candidate #%d (duplicate_of p=%.2f)\n\n", n, res.Probs["duplicate_of"])
	}
	short := sha
	if len(short) > 7 {
		short = short[:7]
	}
	fmt.Fprintf(&sb, "<sub>[mergeone](%s) · head `%s` · probabilities are calibrated; the commit status carries the verdict</sub>\n%s\n", DocsURL, short, CommentMarker(sha))
	return sb.String()
}
