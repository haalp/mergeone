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
	for i := 0; i < workers; i++ {
		gw.wg.Add(1)
		go gw.worker()
	}
	return gw
}

// Stop drains workers.
func (gw *Gateway) Stop() { close(gw.stop); gw.Store.Wake(); gw.wg.Wait() }

// Decide is the synchronous path (idempotent on repo+sha+input hash).
func (gw *Gateway) Decide(repo string, pr int, sha, state string, paths []string, source string) engine.Result {
	return gw.DecideMeta(repo, pr, sha, state, engine.PRMeta{Repo: repo, Number: pr, Paths: paths}, source)
}

// DecideMeta is Decide with PR metadata, which enables duplicate_of / silent_days when a memory is configured.
func (gw *Gateway) DecideMeta(repo string, pr int, sha, state string, meta engine.PRMeta, source string) engine.Result {
	h := sha256.Sum256([]byte(state))
	inputSHA := hex.EncodeToString(h[:])
	if prev := gw.Store.Latest(repo, sha); prev != nil && prev.Result.InputSHA256 == inputSHA && prev.Result.Error == "" {
		return prev.Result
	}
	meta.Repo, meta.Number = repo, pr
	res := gw.Gate.DecideFor(state, meta)
	if err := gw.Store.Record(store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: source, Result: res, State: state}); err != nil {
		log.Printf("gatelog write failed: %v", err)
	}
	return res
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
	defer func() { // fail closed on anything unexpected
		if r := recover(); r != nil {
			err = fmt.Errorf("panic: %v", r)
		}
		if err != nil {
			oe := gw.Gate.Policy.OnError
			res := engine.Result{Verdict: oe.Verdict, Route: oe.Route, Reason: oe.Reason + ": " + err.Error(), Rule: "on_error",
				Probs: map[string]float64{}, RawProbs: map[string]float64{}, Error: err.Error(), TS: time.Now().UTC()}
			_ = gw.Store.Record(store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: "worker-error", Result: res})
			if gw.GitHub != nil && sha != "" {
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
	res := gw.DecideMeta(repo, pr, sha, state, meta, source)
	gw.Store.UpsertOpenPR(repo, pr, sha, paths)
	if gw.Memory != nil && meta.Title != "" {
		mf := make([]memory.FileStat, 0, len(files))
		for _, f := range files {
			mf = append(mf, memory.FileStat{Path: f.Path, Insertions: f.Insertions, Deletions: f.Deletions})
		}
		_ = gw.Memory.Repo(repo).Upsert(memory.EntryFromPR(meta, "OPEN", mf))
	}
	if gw.GitHub != nil {
		details := ""
		if gw.PublicURL != "" {
			details = fmt.Sprintf("%s/verdict/%s/%s", gw.PublicURL, repo, sha)
		}
		gw.GitHub.PostCheck(repo, sha, res, details)
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
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(&body); err != nil || body.Repo == "" || body.HeadSHA == "" || body.State == "" {
			writeJSON(w, 400, map[string]string{"error": "need repo, head_sha, state"})
			return
		}
		if body.Wait == nil || *body.Wait {
			writeJSON(w, 200, gw.Decide(body.Repo, body.PR, body.HeadSHA, body.State, body.Paths, "api"))
			return
		}
		ps := make([]any, len(body.Paths))
		for i, p := range body.Paths {
			ps[i] = p
		}
		queued := gw.Submit(body.Repo, body.PR, body.HeadSHA, "api", map[string]any{"state": body.State, "paths": ps})
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
