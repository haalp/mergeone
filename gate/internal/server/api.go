package server

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/calibrate"
	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// The portal read API (docs/POLICY.md "Portal API"). Everything under /api/ needs the bearer token
// policy portal.api_token, or no token when the server is bound to loopback. Nothing here merges or
// closes a PR; POST /api/resolve records a person's answer and, with actions enabled, mirrors it as a
// label and a comment.

// LoginHeader names the person acting in the portal. The portal is trusted to set it (it holds the
// API token); without it the acting login is "api" (token) or "local" (loopback, no token).
const LoginHeader = "X-Mergeone-Login"

func isLoopback(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// apiAuth returns the acting login, or writes 401/403 and returns ok=false.
func (gw *Gateway) apiAuth(w http.ResponseWriter, r *http.Request) (string, bool) {
	login := strings.TrimSpace(r.Header.Get(LoginHeader))
	tok := gw.Gate.Policy.Portal.APIToken
	if tok == "" {
		if !gw.BoundLocal {
			writeJSON(w, 403, map[string]string{"error": "the /api/ routes need portal.api_token in the policy when the server is not bound to localhost"})
			return "", false
		}
		if login == "" {
			login = "local"
		}
		return login, true
	}
	got, found := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !found || subtle.ConstantTimeCompare([]byte(got), []byte(tok)) != 1 {
		w.Header().Set("WWW-Authenticate", `Bearer realm="mergeone"`)
		writeJSON(w, 401, map[string]string{"error": "missing or wrong bearer token"})
		return "", false
	}
	if login == "" {
		login = "api"
	}
	return login, true
}

// cors sets the CORS headers for the configured portal origin (exact match, credentials allowed).
func (gw *Gateway) cors(w http.ResponseWriter, r *http.Request) {
	w.Header().Add("Vary", "Origin")
	origin := gw.Gate.Policy.Portal.CORSOrigin()
	if origin == "" || r.Header.Get("Origin") != origin {
		return
	}
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", origin)
	h.Set("Access-Control-Allow-Credentials", "true")
	h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "Authorization, Content-Type, "+LoginHeader)
	h.Set("Access-Control-Max-Age", "600")
}

// api wraps a handler with CORS and auth; the handler gets the acting login.
func (gw *Gateway) api(h func(w http.ResponseWriter, r *http.Request, login string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		gw.cors(w, r)
		if login, ok := gw.apiAuth(w, r); ok {
			h(w, r, login)
		}
	}
}

func (gw *Gateway) apiRoutes(mux *http.ServeMux) {
	mux.HandleFunc("OPTIONS /api/", func(w http.ResponseWriter, r *http.Request) { // preflight: no auth
		gw.cors(w, r)
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("GET /api/queue", gw.api(gw.apiQueue))
	mux.HandleFunc("GET /api/graph", gw.api(gw.apiGraph))
	mux.HandleFunc("GET /api/verdict/{sha}", gw.api(gw.apiVerdict))
	mux.HandleFunc("GET /api/stats", gw.api(func(w http.ResponseWriter, r *http.Request, _ string) {
		win, err := parseAge(r.URL.Query().Get("window"), 24*time.Hour)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "window: " + err.Error()})
			return
		}
		writeJSON(w, 200, gw.stats(win))
	}))
	mux.HandleFunc("POST /api/resolve", gw.api(gw.apiResolve))
	mux.HandleFunc("POST /api/outcome", gw.api(gw.apiOutcome))
	mux.HandleFunc("POST /api/calibrate", gw.api(gw.apiCalibrate))
}

// parseAge reads "86400" (seconds), "36h", "7d"; "" gives def.
func parseAge(v string, def time.Duration) (time.Duration, error) {
	switch {
	case v == "":
		return def, nil
	case strings.HasSuffix(v, "d"):
		n, err := strconv.Atoi(strings.TrimSuffix(v, "d"))
		if err != nil || n <= 0 {
			return 0, fmt.Errorf("bad duration %q", v)
		}
		return time.Duration(n) * 24 * time.Hour, nil
	}
	if n, err := strconv.Atoi(v); err == nil && n > 0 {
		return time.Duration(n) * time.Second, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil || d <= 0 {
		return 0, fmt.Errorf("bad duration %q", v)
	}
	return d, nil
}

// stats is /stats and /api/stats: the store's Stats plus the portal sink and calibration blocks.
func (gw *Gateway) stats(win time.Duration) map[string]any {
	out := gw.Store.Stats(win)
	if ps := gw.Portal.Stats(); ps != nil {
		out["portal"] = ps
	}
	if c := gw.Gate.Cal; c != nil && c.Path != "" {
		n := 0
		for _, q := range c.FitStats {
			n = max(n, q.N)
		}
		out["calibration"] = map[string]any{"model": gw.Gate.Policy.Model, "fit_at": c.FitAt.Format(time.RFC3339),
			"n": n, "file": filepath.Base(c.Path)}
	}
	return out
}

func prID(repo string, pr int) string { return repo + "#" + strconv.Itoa(pr) }

func (gw *Gateway) apiQueue(w http.ResponseWriter, r *http.Request, _ string) {
	repo := r.URL.Query().Get("repo")
	holds := gw.Store.Queue(repo)
	matches := map[string]store.Record{}
	for _, h := range holds {
		if n := toInt(h.Result.Extra["duplicate_candidate"]); n > 0 {
			if m := gw.Store.LatestForPR(h.Repo, n); m != nil {
				matches[prID(h.Repo, n)] = *m
			}
		}
	}
	if holds == nil {
		holds = []store.Record{}
	}
	writeJSON(w, 200, map[string]any{"holds": holds, "matches": matches})
}

// GraphNode and GraphEdge are the portal's graph shapes.
type GraphNode struct {
	ID        string `json:"id"`
	Repo      string `json:"repo"`
	PR        int    `json:"pr"`
	Title     string `json:"title"`
	HeadSHA   string `json:"head_sha"`
	Verdict   string `json:"verdict"`
	Route     string `json:"route"`
	Rule      string `json:"rule"`
	Additions int    `json:"additions"`
	Deletions int    `json:"deletions"`
	Files     int    `json:"files"`
	Author    string `json:"author,omitempty"`
	Updated   string `json:"updated,omitempty"`
}

type GraphEdge struct {
	Source   string  `json:"source"`
	Target   string  `json:"target"`
	Relation string  `json:"relation"`
	P        float64 `json:"p"`
	Type     string  `json:"type,omitempty"` // set only from a typed relation record (none exist yet)
	Note     string  `json:"note,omitempty"`
}

func (gw *Gateway) apiGraph(w http.ResponseWriter, r *http.Request, _ string) {
	q := r.URL.Query()
	var since time.Time
	if v := q.Get("since"); v != "" {
		if t, err := time.Parse(time.RFC3339, v); err == nil {
			since = t
		} else if d, err := parseAge(v, 0); err == nil {
			since = time.Now().Add(-d)
		} else {
			writeJSON(w, 400, map[string]string{"error": "since: want RFC3339 or an age like 7d"})
			return
		}
	}
	recs := gw.Store.PRRecords(q.Get("repo"), since)
	nodes := make([]GraphNode, 0, len(recs))
	have := map[string]bool{}
	for _, rec := range recs {
		n := nodeOf(rec)
		nodes = append(nodes, n)
		have[n.ID] = true
	}
	edges := []GraphEdge{}
	for _, rec := range recs {
		t := toInt(rec.Result.Extra["duplicate_candidate"])
		if t <= 0 || !have[prID(rec.Repo, t)] {
			continue
		}
		edges = append(edges, GraphEdge{Source: prID(rec.Repo, rec.PR), Target: prID(rec.Repo, t), Relation: "duplicate_of",
			P: rec.Result.Probs["duplicate_of"]})
	}
	writeJSON(w, 200, map[string]any{"nodes": nodes, "edges": edges})
}

// nodeOf builds a graph node from Record.Context, falling back to the rendered state for title and size when the
// record has no context (a CLI record without a GitHub token carries title "" on purpose: its state title is a commit
// subject, not the PR title).
func nodeOf(rec store.Record) GraphNode {
	n := GraphNode{ID: prID(rec.Repo, rec.PR), Repo: rec.Repo, PR: rec.PR, HeadSHA: rec.HeadSHA, Verdict: rec.Result.Verdict,
		Route: rec.Result.Route, Rule: rec.Result.Rule, Updated: rec.TS.UTC().Format(time.RFC3339)}
	c := rec.Context
	_, hasTitle := c["title"] // a context with an empty title means "unknown": never guess it from the state
	if s, ok := c["title"].(string); ok {
		n.Title = s
	}
	if s, ok := c["author"].(string); ok {
		n.Author = s
	}
	if _, ok := c["files"]; ok {
		n.Additions, n.Deletions, n.Files = toInt(c["additions"]), toInt(c["deletions"]), toInt(c["files"])
	} else if i := strings.Index(rec.State, "\nSUMMARY: "); i >= 0 {
		fmt.Sscanf(rec.State[i+1:], "SUMMARY: %d files changed, +%d / -%d lines", &n.Files, &n.Additions, &n.Deletions)
	}
	if !hasTitle {
		if t, ok := strings.CutPrefix(rec.State, "PULL REQUEST TITLE: "); ok {
			n.Title, _, _ = strings.Cut(t, "\n")
		}
	}
	return n
}

func (gw *Gateway) apiVerdict(w http.ResponseWriter, r *http.Request, _ string) {
	sha, repo := r.PathValue("sha"), r.URL.Query().Get("repo")
	var rec *store.Record
	if repo != "" {
		rec = gw.Store.Latest(repo, sha)
	} else {
		rec = gw.Store.FindSHA(sha)
	}
	if rec == nil {
		writeJSON(w, 404, map[string]string{"error": "no decision for that sha"})
		return
	}
	writeJSON(w, 200, rec)
}

func (gw *Gateway) apiResolve(w http.ResponseWriter, r *http.Request, login string) {
	var body struct {
		Repo     string `json:"repo"`
		PR       int    `json:"pr"`
		HeadSHA  string `json:"head_sha"`
		Action   string `json:"action"`
		TargetPR int    `json:"target_pr"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil || body.Repo == "" || body.PR <= 0 || body.HeadSHA == "" {
		writeJSON(w, 400, map[string]string{"error": "need repo, pr, head_sha, action"})
		return
	}
	if !store.ResolveActions[body.Action] {
		writeJSON(w, 400, map[string]string{"error": "action must be approve, link_duplicate or request_rebase"})
		return
	}
	rec := gw.Store.Latest(body.Repo, body.HeadSHA)
	if rec == nil || (rec.PR != 0 && rec.PR != body.PR) {
		writeJSON(w, 404, map[string]string{"error": "no decision for that PR head"})
		return
	}
	if body.Action == "link_duplicate" && body.TargetPR == 0 {
		body.TargetPR = toInt(rec.Result.Extra["duplicate_candidate"])
		if body.TargetPR <= 0 {
			writeJSON(w, 400, map[string]string{"error": "link_duplicate needs target_pr (no duplicate candidate on record)"})
			return
		}
	}
	res := store.Resolution{Repo: body.Repo, PR: body.PR, HeadSHA: body.HeadSHA, Action: body.Action, TargetPR: body.TargetPR,
		Login: login, Verdict: rec.Result.Verdict, Route: rec.Result.Route, Rule: rec.Result.Rule}
	if gw.Gate.Policy.Actions.Enabled {
		res.Actions = ApplyResolution(gw.GitHub, gw.Gate.Policy.Actions, res)
	}
	res, err := gw.Store.Resolve(res)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "resolution not recorded: " + err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "logged_at": res.TS.Format(time.RFC3339Nano), "resolution": res})
}

// apiOutcome records what happened to a PR (CI: merged, reverted, iterated) for the slow drift signal and
// `mergeone calibrate --from-gatelog`. Fields left out stay as they were; at least one is required.
func (gw *Gateway) apiOutcome(w http.ResponseWriter, r *http.Request, _ string) {
	var body struct {
		Repo     string `json:"repo"`
		PR       int    `json:"pr"`
		Merged   *bool  `json:"merged"`
		Reverted *bool  `json:"reverted"`
		Iterated *bool  `json:"iterated"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil || body.Repo == "" || body.PR <= 0 ||
		(body.Merged == nil && body.Reverted == nil && body.Iterated == nil) {
		writeJSON(w, 400, map[string]string{"error": "need repo, pr and at least one of merged, reverted, iterated (booleans)"})
		return
	}
	o, err := gw.Store.RecordOutcome(store.PROutcome{Repo: body.Repo, PR: body.PR, Merged: body.Merged, Reverted: body.Reverted,
		Iterated: body.Iterated, Source: "api"})
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "outcome not recorded: " + err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "outcome": o})
}

// apiCalibrate runs `mergeone calibrate --from-gatelog` (body, all optional: {since: "28d", model, label}) and
// returns the fit summary. The map is written beside the gatelog under a timestamped name and is never
// loaded by the running gateway: nothing refits automatically. 422 under 200 labelled decisions.
func (gw *Gateway) apiCalibrate(w http.ResponseWriter, r *http.Request, _ string) {
	var body struct {
		Since string `json:"since"`
		Model string `json:"model"`
		Label string `json:"label"`
	}
	if r.ContentLength != 0 {
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil && err != io.EOF {
			writeJSON(w, 400, map[string]string{"error": "bad body: " + err.Error()})
			return
		}
	}
	opt := calibrate.Options{Model: body.Model, Label: body.Label}
	if body.Since != "" {
		d, err := calibrate.ParseAge(body.Since)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "since: " + err.Error()})
			return
		}
		opt.Since = d
	}
	f, sum, err := calibrate.Fit(gw.Store, opt)
	if err != nil {
		code := 400
		if errors.Is(err, calibrate.ErrTooFew) {
			code = 422
		}
		writeJSON(w, code, map[string]any{"ok": false, "error": err.Error(), "summary": sum})
		return
	}
	p := gw.Gate.Policy
	name := fmt.Sprintf("calibration_%s_%s.gatelog-%s.json", p.Model, p.QuestionSet, sum.FitAt.UTC().Format("20060102T150405Z"))
	path := filepath.Join(filepath.Dir(gw.Store.Path()), name)
	if err := calibrate.Write(path, f, false); err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error(), "summary": sum})
		return
	}
	sum.File = path
	writeJSON(w, 200, map[string]any{"ok": true, "summary": sum, "file": path,
		"note": fmt.Sprintf("not loaded: the gateway refits nothing by itself; to use it, replace calibration_%s_%s.json with this file and restart", p.Model, p.QuestionSet)})
}

// ResolutionLabels are the labels a resolution sets (exclusive among themselves; verdict labels and
// the commit status are left as they are, so an approval never lets auto-merge proceed by itself).
var ResolutionLabels = map[string]string{"approve": "mergeone:approved", "link_duplicate": "mergeone:duplicate", "request_rebase": "mergeone:rebase"}

// ResolveMarker makes the resolution comment idempotent per (head sha, action).
func ResolveMarker(sha, action string) string {
	return "<!-- mergeone:resolve:" + sha + ":" + action + " -->"
}

// ApplyResolution mirrors a portal resolution on the PR through the verdict-action path: one label and
// one comment. It never merges, closes, or changes the commit status.
func ApplyResolution(g *GitHub, cfg engine.Actions, res store.Resolution) *engine.ActionsReport {
	rep := &engine.ActionsReport{Applied: []string{}, Errors: []string{}}
	if s := ActionsSkip(cfg, g, res.Repo, res.PR, res.HeadSHA); s != "" {
		rep.Skipped = s
		return rep
	}
	a := actor{g: g, repo: res.Repo, rep: rep}
	excl := map[string]bool{}
	for _, l := range ResolutionLabels {
		excl[l] = true
	}
	a.labels(res.PR, []string{ResolutionLabels[res.Action]}, excl)
	if cfg.CommentEnabled() {
		a.commentOnce(res.PR, ResolveMarker(res.HeadSHA, res.Action), ResolutionComment(res))
	}
	return rep
}

// ResolutionComment is the comment body for a resolution.
func ResolutionComment(res store.Resolution) string {
	what := map[string]string{"approve": "approved as is", "link_duplicate": "linked as a duplicate", "request_rebase": "rebase requested"}[res.Action]
	if res.TargetPR > 0 {
		what += fmt.Sprintf(" (#%d)", res.TargetPR)
	}
	short := res.HeadSHA
	if len(short) > 7 {
		short = short[:7]
	}
	return fmt.Sprintf("**mergeone: %s** by `%s` · was %s · rule `%s` · head `%s`\n\n"+
		"<sub>recorded in the portal; mergeone never merges or closes a PR</sub>\n%s\n",
		what, res.Login, res.Verdict, res.Rule, short, ResolveMarker(res.HeadSHA, res.Action))
}
