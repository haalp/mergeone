// Package server is the always-on gateway: webhook in, verdicts out.
package server

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/memory"
	"github.com/haalp/mergeone/gate/internal/plan"
	"github.com/haalp/mergeone/gate/internal/store"
)

// Gateway owns the gate, the store, the worker pool and the GitHub client.
type Gateway struct {
	Gate      *engine.Gate
	Store     *store.Store
	Memory    *memory.Store // optional PR memory (run 4)
	GitHub    *GitHub
	PublicURL string
	Workers   int
	stop      chan struct{}
	wg        sync.WaitGroup
}

// New starts the worker pool.
func New(g *engine.Gate, st *store.Store, gh *GitHub, workers int, publicURL string) *Gateway {
	gw := &Gateway{Gate: g, Store: st, GitHub: gh, PublicURL: publicURL, Workers: workers, stop: make(chan struct{})}
	if g.Cal != nil {
		st.SetFitStats(g.Cal.FitStats)
	}
	if g.Policy.Drift.Enabled {
		g.Degraded = func() map[string]bool { return st.DegradedQuestions(time.Now()) }
		go gw.driftLogger(time.Hour)
	}
	for i := 0; i < workers; i++ {
		gw.wg.Add(1)
		go gw.worker()
	}
	return gw
}

// driftLogger logs every degraded question once per interval until the gateway stops.
func (gw *Gateway) driftLogger(every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-gw.stop:
			return
		case now := <-t.C:
			gw.logDrift(now)
		}
	}
}

func (gw *Gateway) logDrift(now time.Time) {
	for model, md := range gw.Store.Drift(now) {
		for _, q := range md.Degraded {
			d := md.Questions[q]
			log.Printf("drift: model=%s question=%s mean_raw=%.3f ref=%.3f shift=%.3f spread=%.3f ref_spread=%.3f n=%d reference=%s",
				model, q, d.MeanRaw, d.RefMeanRaw, d.Shift, d.Spread, d.RefSpread, d.N, md.Reference)
		}
	}
}

// Stop drains workers.
func (gw *Gateway) Stop() { close(gw.stop); gw.Store.Wake(); gw.wg.Wait() }

// Decide is the synchronous path (idempotent on repo+sha+input hash).
func (gw *Gateway) Decide(repo string, pr int, sha, state string, paths []string, source string) engine.Result {
	return gw.DecideMeta(repo, pr, sha, state, engine.PRMeta{Repo: repo, Number: pr, Paths: paths}, source)
}

// DecideMeta is Decide with PR metadata, which enables duplicate_of / silent_days when a memory is configured.
// It never writes to GitHub (the MCP tools use this path).
func (gw *Gateway) DecideMeta(repo string, pr int, sha, state string, meta engine.PRMeta, source string) engine.Result {
	return gw.decide(repo, pr, sha, state, meta, source, false)
}

// DecideAndAct is DecideMeta plus the policy's verdict actions (webhook/CI mode only). The actions report
// is attached to the result and logged in the same gatelog entry; action errors never change the verdict.
// A cached decision is not acted on again unless it was never acted on (or was skipped for another reason).
func (gw *Gateway) DecideAndAct(repo string, pr int, sha, state string, meta engine.PRMeta, source string) engine.Result {
	return gw.decide(repo, pr, sha, state, meta, source, true)
}

func (gw *Gateway) decide(repo string, pr int, sha, state string, meta engine.PRMeta, source string, act bool) engine.Result {
	h := sha256.Sum256([]byte(state))
	inputSHA := hex.EncodeToString(h[:])
	var res engine.Result
	if prev := gw.Store.Latest(repo, sha); prev != nil && prev.Result.InputSHA256 == inputSHA && prev.Result.Error == "" {
		if !act {
			return prev.Result
		}
		if pa := prev.Result.Actions; pa != nil && (pa.Skipped == "" || pa.Skipped == ActionsSkip(gw.Gate.Policy.Actions, gw.GitHub, repo, pr, sha)) {
			return prev.Result
		}
		res = prev.Result
	} else {
		meta.Repo, meta.Number = repo, pr
		res = gw.Gate.DecideFor(state, meta)
		if pr > 0 && gw.Gate.Policy.MaxRounds > 0 { // no PR number → no rounds to count
			gw.Gate.ApplyMaxRounds(&res, gw.Store.NonPassRounds(repo, pr, sha))
		}
	}
	if act {
		res.Actions = ApplyActions(gw.GitHub, gw.Gate.Policy.Actions, repo, pr, sha, res, gw.verdictURL(repo, sha))
		for _, e := range res.Actions.Errors {
			log.Printf("actions %s#%d@%s: %s", repo, pr, sha, e)
		}
	}
	if err := gw.Store.Record(store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: source, Result: res, State: state}); err != nil {
		log.Printf("gatelog write failed: %v", err)
	}
	return res
}

// verdictURL is the public gatelog entry for a head sha, or "" when the server has no public URL.
func (gw *Gateway) verdictURL(repo, sha string) string {
	if gw.PublicURL == "" {
		return ""
	}
	return fmt.Sprintf("%s/verdict/%s/%s", strings.TrimRight(gw.PublicURL, "/"), repo, sha)
}

// Submit enqueues an async decision for a PR head. Returns false if already queued.
func (gw *Gateway) Submit(repo string, pr int, sha, source string, extra map[string]any) bool {
	payload := map[string]any{"repo": repo, "pr": pr, "head_sha": sha, "source": source}
	for k, v := range extra {
		payload[k] = v
	}
	return gw.Store.Enqueue(fmt.Sprintf("%s:%d:%s", repo, pr, sha), payload)
}

func (gw *Gateway) worker() {
	defer gw.wg.Done()
	for {
		j := gw.Store.Claim(gw.stop)
		if j == nil {
			return
		}
		err := gw.run(j)
		gw.Store.Finish(j, err == nil, errStr(err), 3)
	}
}

func (gw *Gateway) run(j *store.Job) (err error) {
	repo, _ := j.Payload["repo"].(string)
	pr := toInt(j.Payload["pr"])
	sha, _ := j.Payload["head_sha"].(string)
	source, _ := j.Payload["source"].(string)
	act := true // webhook and /decide jobs act unless the caller opted out with "actions": false
	if v, ok := j.Payload["actions"].(bool); ok {
		act = v
	}
	defer func() { // fail closed on anything unexpected
		if r := recover(); r != nil {
			err = fmt.Errorf("panic: %v", r)
		}
		if err != nil {
			oe := gw.Gate.Policy.OnError
			res := engine.Result{Verdict: oe.Verdict, Route: oe.Route, Reason: oe.Reason + ": " + err.Error(), Rule: "on_error",
				Probs: map[string]float64{}, RawProbs: map[string]float64{}, Error: err.Error(), TS: time.Now().UTC()}
			if gw.Gate.Policy.Actions.Enabled && act {
				res.Actions = ApplyStatusOnly(gw.GitHub, gw.Gate.Policy.Actions, repo, pr, sha, res)
			}
			_ = gw.Store.Record(store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: "worker-error", Result: res})
			if gw.GitHub != nil && sha != "" && !gw.Gate.Policy.Actions.Enabled {
				gw.GitHub.PostCheck(repo, sha, res, "")
			}
		}
	}()
	var state string
	var paths []string
	meta := engine.PRMeta{Repo: repo, Number: pr}
	var files []FileStat
	if s, ok := j.Payload["state"].(string); ok && s != "" {
		state = s
		if ps, ok := j.Payload["paths"].([]any); ok {
			for _, p := range ps {
				paths = append(paths, fmt.Sprint(p))
			}
		}
		meta.Paths = paths
	} else {
		if gw.GitHub == nil {
			return fmt.Errorf("no state in payload and no GitHub token to fetch PR %d", pr)
		}
		state, paths, sha, meta, files, err = gw.GitHub.PRStateMeta(repo, pr)
		if err != nil {
			return err
		}
	}
	res := gw.decide(repo, pr, sha, state, meta, source, act)
	gw.Store.UpsertOpenPR(repo, pr, sha, paths)
	if gw.Memory != nil && meta.Title != "" {
		mf := make([]memory.FileStat, 0, len(files))
		for _, f := range files {
			mf = append(mf, memory.FileStat{Path: f.Path, Insertions: f.Insertions, Deletions: f.Deletions})
		}
		_ = gw.Memory.Repo(repo).Upsert(memory.EntryFromPR(meta, "OPEN", mf))
	}
	// Legacy write-back (check-run + gate:/route: labels) stays as it was while actions are off;
	// with actions enabled the commit status and mergeone:* labels replace it (one signal, one name).
	if gw.GitHub != nil && !gw.Gate.Policy.Actions.Enabled {
		gw.GitHub.PostCheck(repo, sha, res, gw.verdictURL(repo, sha))
		gw.GitHub.SetLabels(repo, pr, res)
	}
	return nil
}

// Handler builds the HTTP API.
func (gw *Gateway) Handler(webhookSecret string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "workers": gw.Workers, "queue": gw.Store.QueueDepth(), "model": gw.Gate.Model.Name()})
	})
	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, r *http.Request) {
		win := 86400
		if v := r.URL.Query().Get("window"); v != "" {
			win, _ = strconv.Atoi(v)
		}
		writeJSON(w, 200, gw.Store.Stats(time.Duration(win)*time.Second))
	})
	mux.HandleFunc("GET /verdict/pr/{owner}/{repo}/{pr}", func(w http.ResponseWriter, r *http.Request) {
		pr, _ := strconv.Atoi(r.PathValue("pr"))
		rec := gw.Store.LatestForPR(r.PathValue("owner")+"/"+r.PathValue("repo"), pr)
		if rec == nil {
			writeJSON(w, 404, map[string]string{"error": "no decision for that PR"})
			return
		}
		writeJSON(w, 200, rec.Result)
	})
	mux.HandleFunc("GET /verdict/{owner}/{repo}/{sha}", func(w http.ResponseWriter, r *http.Request) {
		rec := gw.Store.Latest(r.PathValue("owner")+"/"+r.PathValue("repo"), r.PathValue("sha"))
		if rec == nil {
			writeJSON(w, 404, map[string]string{"error": "no decision for that sha"})
			return
		}
		writeJSON(w, 200, rec.Result)
	})
	mux.HandleFunc("GET /plan/{owner}/{repo}", func(w http.ResponseWriter, r *http.Request) {
		repo := r.PathValue("owner") + "/" + r.PathValue("repo")
		var m engine.Model
		if r.URL.Query().Get("semantic") == "true" {
			m = gw.Gate.Model
		}
		writeJSON(w, 200, plan.Landing(repo, gw.Store.OpenPRs(repo), m, 200, 0.6))
	})
	mux.HandleFunc("POST /decide", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Repo    string   `json:"repo"`
			PR      int      `json:"pr"`
			HeadSHA string   `json:"head_sha"`
			State   string   `json:"state"`
			Paths   []string `json:"paths"`
			Wait    *bool    `json:"wait"`
			Actions *bool    `json:"actions"` // false: never write to GitHub for this call (default: policy decides)
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(&body); err != nil || body.Repo == "" || body.HeadSHA == "" || body.State == "" {
			writeJSON(w, 400, map[string]string{"error": "need repo, head_sha, state"})
			return
		}
		act := body.Actions == nil || *body.Actions
		if body.Wait == nil || *body.Wait {
			meta := engine.PRMeta{Repo: body.Repo, Number: body.PR, Paths: body.Paths}
			writeJSON(w, 200, gw.decide(body.Repo, body.PR, body.HeadSHA, body.State, meta, "api", act))
			return
		}
		ps := make([]any, len(body.Paths))
		for i, p := range body.Paths {
			ps[i] = p
		}
		queued := gw.Submit(body.Repo, body.PR, body.HeadSHA, "api", map[string]any{"state": body.State, "paths": ps, "actions": act})
		writeJSON(w, 202, map[string]any{"queued": queued, "poll": "/verdict/" + body.Repo + "/" + body.HeadSHA})
	})
	mux.HandleFunc("POST /webhook/github", func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(io.LimitReader(r.Body, 8<<20))
		if webhookSecret != "" {
			mac := hmac.New(sha256.New, []byte(webhookSecret))
			mac.Write(raw)
			want := "sha256=" + hex.EncodeToString(mac.Sum(nil))
			if !hmac.Equal([]byte(want), []byte(r.Header.Get("X-Hub-Signature-256"))) {
				writeJSON(w, 401, map[string]string{"error": "bad signature"})
				return
			}
		}
		if r.Header.Get("X-GitHub-Event") != "pull_request" {
			writeJSON(w, 200, map[string]string{"ignored": r.Header.Get("X-GitHub-Event")})
			return
		}
		var ev struct {
			Action     string `json:"action"`
			Repository struct {
				FullName string `json:"full_name"`
			} `json:"repository"`
			PullRequest struct {
				Number int  `json:"number"`
				Merged bool `json:"merged"`
				Head   struct {
					SHA string `json:"sha"`
				} `json:"head"`
			} `json:"pull_request"`
		}
		if err := json.Unmarshal(raw, &ev); err != nil {
			writeJSON(w, 400, map[string]string{"error": "bad payload"})
			return
		}
		repo, pr, sha := ev.Repository.FullName, ev.PullRequest.Number, ev.PullRequest.Head.SHA
		switch ev.Action {
		case "opened", "synchronize", "reopened", "ready_for_review":
			if ev.Action == "synchronize" {
				gw.GitHub.InvalidateHead(repo, pr, sha)
			}
			writeJSON(w, 200, map[string]any{"queued": gw.Submit(repo, pr, sha, "webhook", nil)})
		case "closed":
			gw.Store.ClosePR(repo, pr)
			if gw.Memory != nil {
				if e, ok := gw.Memory.Repo(repo).Get(pr); ok {
					e.State = "CLOSED"
					if ev.PullRequest.Merged {
						e.State, e.MergedAt = "MERGED", time.Now().UTC().Format("2006-01-02T15:04:05Z")
					} else {
						e.ClosedAt = time.Now().UTC().Format("2006-01-02T15:04:05Z")
					}
					_ = gw.Memory.Repo(repo).Upsert(e)
				}
			}
			writeJSON(w, 200, map[string]any{"closed": true})
		default:
			writeJSON(w, 200, map[string]string{"ignored": ev.Action})
		}
	})
	return mux
}

// Serve runs the HTTP server until ctx is done.
func (gw *Gateway) Serve(ctx context.Context, addr, webhookSecret string) error {
	srv := &http.Server{Addr: addr, Handler: gw.Handler(webhookSecret), ReadHeaderTimeout: 10 * time.Second}
	go func() { <-ctx.Done(); _ = srv.Shutdown(context.Background()) }()
	log.Printf("mergeone gateway listening on %s (model=%s, workers=%d)", addr, gw.Gate.Model.Name(), gw.Workers)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return err
	}
	gw.Stop()
	return nil
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func errStr(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func toInt(v any) int {
	switch x := v.(type) {
	case int:
		return x
	case float64:
		return int(x)
	case string:
		i, _ := strconv.Atoi(strings.TrimSpace(x))
		return i
	}
	return 0
}
