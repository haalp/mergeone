// Package server is the always-on gateway: webhook in, verdicts out.
package server

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/memory"
	"github.com/haalp/mergeone/gate/internal/models"
	"github.com/haalp/mergeone/gate/internal/patchid"
	"github.com/haalp/mergeone/gate/internal/plan"
	"github.com/haalp/mergeone/gate/internal/portal"
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
	// Portal is the outbound ingest sink (policy portal: {url, token}); nil when the block is off.
	Portal *portal.Sink
	// BoundLocal is set by Serve when the listen address is loopback: /api/* then works without
	// portal.api_token. Set it yourself when calling Handler directly.
	BoundLocal bool
	stop       chan struct{}
	wg         sync.WaitGroup
	// webhook debounce (debounce.go): repo#pr → pushes waiting for the window to close
	pmu     sync.Mutex
	pending map[string]*pendingPush
	// metrics (metrics.go): in-process counters for GET /metrics
	metrics *metrics
	// flights and spend (flight.go): one decision per PR at a time, and the daily budget
	flights flights
	spend   *spend
	// guard (webhookguard.go): delivery replay cache and per-repository rate limit
	guard *webhookGuard
	// AllowUnsignedWebhooks lets a non-loopback bind accept webhooks without MERGEONE_WEBHOOK_SECRET
	// (serve --allow-unsigned-webhooks). Off by default: unsigned + public bind → 401.
	AllowUnsignedWebhooks bool
}

// New starts the worker pool.
func New(g *engine.Gate, st *store.Store, gh *GitHub, workers int, publicURL string) *Gateway {
	gw := &Gateway{Gate: g, Store: st, GitHub: gh, PublicURL: publicURL, Workers: workers, stop: make(chan struct{}),
		pending: map[string]*pendingPush{}, metrics: newMetrics(), guard: newWebhookGuard(0)}
	gw.spend = newSpend(st.CostSince(time.Now().UTC().Truncate(24 * time.Hour)))
	if g.Cal != nil {
		var refit time.Time
		if m := g.Cal.Meta; m != nil && m.Source == "gatelog" {
			refit = m.FitAt
		}
		st.SetFitStatsAt(g.Cal.FitStats, refit)
	}
	if g.Policy.Drift.Enabled {
		g.Degraded = func() map[string]bool { return st.DegradedQuestions(time.Now()) }
		go gw.driftLogger(time.Hour)
	}
	if pc := g.Policy.Portal; pc.URL != "" && pc.Token != "" {
		gw.Portal = portal.New(pc.URL, pc.Token)
		st.SetOnRecord(func(r store.Record) { gw.Portal.Enqueue(r) })
		st.SetOnEvent(func(e store.Event) { gw.Portal.EnqueueEvent(e) })
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
		if sd := md.Slow; sd != nil {
			for _, q := range sd.Degraded {
				d := sd.Questions[q]
				log.Printf("drift (slow): model=%s question=%s auc=%.3f ref_auc=%.3f delta=%.3f n=%d label=%s",
					model, q, *d.AUC, *d.RefAUC, *d.Delta, d.N, sd.Label)
			}
		}
	}
}

// Stop drains workers.
func (gw *Gateway) Stop() {
	gw.stopPending()
	close(gw.stop)
	gw.Store.Wake()
	gw.wg.Wait()
	gw.Portal.Close(10 * time.Second)
}

// Decide is the synchronous path (idempotent on repo+sha+input hash).
func (gw *Gateway) Decide(repo string, pr int, sha, state string, paths []string, source string) engine.Result {
	return gw.DecideMeta(repo, pr, sha, state, engine.PRMeta{Repo: repo, Number: pr, Paths: paths}, source)
}

// DecideMeta is Decide with PR metadata, which enables duplicate_of / silent_days when a memory is configured.
// It never writes to GitHub (the MCP tools use this path).
func (gw *Gateway) DecideMeta(repo string, pr int, sha, state string, meta engine.PRMeta, source string) engine.Result {
	return gw.decide(repo, pr, sha, state, meta, nil, source, false)
}

// DecideAndAct is DecideMeta plus the policy's verdict actions (webhook/CI mode only). The actions report
// is attached to the result and logged in the same gatelog entry; action errors never change the verdict.
// A cached decision is not acted on again unless it was never acted on (or was skipped for another reason).
func (gw *Gateway) DecideAndAct(repo string, pr int, sha, state string, meta engine.PRMeta, source string) engine.Result {
	return gw.decide(repo, pr, sha, state, meta, nil, source, true)
}

// PRContext is Record.Context for a PR fetched from GitHub: what the portal shows beside the verdict
// (agent name, "same base") without parsing the state.
func PRContext(meta engine.PRMeta, files []FileStat) map[string]any {
	if meta.Author == "" && meta.Base == "" && meta.Title == "" && meta.CreatedAt.IsZero() && files == nil {
		return nil
	}
	add, del := 0, 0
	for _, f := range files {
		add += f.Insertions
		del += f.Deletions
	}
	c := map[string]any{"author": meta.Author, "base": meta.Base, "title": meta.Title,
		"additions": add, "deletions": del, "files": len(files)}
	if !meta.CreatedAt.IsZero() {
		c["created_at"] = meta.CreatedAt.UTC().Format(time.RFC3339)
	}
	return c
}

// decideReq is one decision request: the head, its rendered state and how to record it.
type decideReq struct {
	repo, sha, state, source string
	pr                       int
	meta                     engine.PRMeta
	rctx                     map[string]any
	act                      bool
	patchID                  string          // "" = derived from the state (patchid.State)
	deliveryID               string          // X-GitHub-Delivery of the webhook that caused this decision
	ctx                      context.Context // nil = not cancellable
	debounced                []string        // heads skipped by the webhook debounce, recorded on this decision
}

func (gw *Gateway) decide(repo string, pr int, sha, state string, meta engine.PRMeta, rctx map[string]any, source string, act bool) engine.Result {
	return gw.decideReq(decideReq{repo: repo, pr: pr, sha: sha, state: state, meta: meta, rctx: rctx, source: source, act: act})
}

// budgetResult is the verdict of a head that was not sent to the model because a budget ceiling is reached. It
// carries an Error so it is never served as that head's cached verdict or copied to a rebase: the next event
// after 00:00 UTC decides the head normally.
func (gw *Gateway) budgetResult(inputSHA, which string, spent, limit float64) engine.Result {
	o := gw.Gate.Policy.Budget.OnExceeded
	return engine.Result{Verdict: o.Verdict, Route: o.Route, Reason: o.Reason, Rule: "budget", Probs: map[string]float64{},
		RawProbs: map[string]float64{}, InputSHA256: inputSHA, TS: time.Now().UTC(),
		Error: fmt.Sprintf("BudgetExceeded: budget.%s $%.4f of $%.4f spent today (UTC)", which, spent, limit)}
}

func (gw *Gateway) decideReq(q decideReq) engine.Result {
	shadow := gw.Gate.Policy.Shadow()
	if shadow {
		q.act = false // mode: shadow — decide and record, write nothing to GitHub
	}
	repo, pr, sha, state, meta, rctx, act := q.repo, q.pr, q.sha, q.state, q.meta, q.rctx, q.act
	h := sha256.Sum256([]byte(state))
	inputSHA := hex.EncodeToString(h[:])
	pid := q.patchID
	if pid == "" {
		pid = patchid.State(state)
	}
	phash := gw.Gate.PolicyHash()
	reusedFrom := ""
	var res engine.Result
	if prev := gw.Store.Latest(repo, sha); prev != nil && prev.Result.InputSHA256 == inputSHA && prev.Result.Error == "" {
		if !act {
			return prev.Result
		}
		if pa := prev.Result.Actions; pa != nil && (pa.Skipped == "" || pa.Skipped == ActionsSkip(gw.Gate.Policy.Actions, gw.GitHub, repo, pr, sha)) {
			return prev.Result
		}
		res = prev.Result
		reusedFrom = prev.ReusedFrom
		if q.debounced == nil {
			q.debounced = prev.DebouncedSHAs
		}
	} else if old := gw.reusable(repo, pr, sha, pid, phash); old != nil {
		// Same patch on a new head (a rebase): copy the verdict, no model call. The record is the new head's.
		res = old.Result
		res.Actions, res.LatencyMS, res.ModelLatencySumMS, res.CostUSD = nil, 0, 0, 0
		res.InputSHA256, res.TS = inputSHA, time.Now().UTC()
		reusedFrom = old.HeadSHA
	} else {
		meta.Repo, meta.Number = repo, pr
		if which, spent, limit := gw.spend.exceeded(gw.Gate.Policy.Budget, repo); which != "" {
			res = gw.budgetResult(inputSHA, which, spent, limit)
		} else {
			ctx := q.ctx
			if ctx == nil {
				ctx = context.Background()
			}
			res = gw.Gate.DecideForCtx(ctx, state, meta)
			if res.Error == engine.ErrCanceled {
				return res // superseded by a newer head: no record, no write-back
			}
			gw.spend.add(repo, res.CostUSD)
		}
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
	if rctx == nil { // a cached decision re-acted on keeps the context it was recorded with
		if prev := gw.Store.Latest(repo, sha); prev != nil {
			rctx = prev.Context
		}
	}
	rec := store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: q.source, Result: res, Context: rctx, State: state,
		PatchID: pid, PolicyHash: phash, ReusedFrom: reusedFrom, DebouncedSHAs: q.debounced, Shadow: shadow, DeliveryID: q.deliveryID}
	rec.State = gw.Gate.Policy.LoggedState(rec.State)
	if err := gw.Store.Record(rec); err != nil {
		log.Printf("gatelog write failed: %v", err)
	}
	gw.metrics.observe(rec)
	return res
}

// reusable is the earlier decision whose verdict a new head may copy (policy reuse_verdict_on_same_patch),
// or nil: same PR, same patch id, same policy hash, same served model version, and the drift monitor
// flagging the same questions now as when it was decided.
func (gw *Gateway) reusable(repo string, pr int, sha, pid, phash string) *store.Record {
	if !gw.Gate.Policy.ReuseVerdictOnSamePatch {
		return nil
	}
	old := gw.Store.ReusableForPatch(repo, pr, pid, phash, sha)
	if old == nil || !slices.Equal(old.Result.Drift, gw.Gate.DegradedNow()) {
		return nil
	}
	return old
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
			shadow := gw.Gate.Policy.Shadow()
			if gw.Gate.Policy.Actions.Enabled && act && !shadow {
				res.Actions = ApplyStatusOnly(gw.GitHub, gw.Gate.Policy.Actions, repo, pr, sha, res)
			}
			erec := store.Record{Repo: repo, PR: pr, HeadSHA: sha, Source: "worker-error", Result: res, Shadow: shadow}
			erec.DeliveryID, _ = j.Payload["delivery_id"].(string)
			_ = gw.Store.Record(erec)
			gw.metrics.observe(erec)
			if gw.GitHub != nil && sha != "" && !gw.Gate.Policy.Actions.Enabled && !shadow {
				gw.GitHub.PostCheck(repo, sha, gw.Gate.Policy.QuestionSet, res, "")
				gw.settleLegacyStatus(j, repo, sha, res)
			}
		}
	}()
	var state string
	var paths []string
	meta := engine.PRMeta{Repo: repo, Number: pr}
	var files []FileStat
	var rctx map[string]any
	var pid string
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
		rctx = PRContext(meta, files)
		pid = gw.GitHub.PatchID(repo, pr, sha)
	}
	var debounced []string
	for _, d := range payloadStrings(j.Payload["debounced_shas"]) {
		if d != sha { // the head that actually ran is not a skipped one
			debounced = append(debounced, d)
		}
	}
	deliveryID, _ := j.Payload["delivery_id"].(string)
	ctx, end, ok, old := gw.flights.begin(repo, pr, sha)
	if !ok {
		gw.metrics.count("joined") // this head is already being decided; that decision records the verdict
		return nil
	}
	defer end()
	if old != "" {
		gw.metrics.count("superseded")
	}
	res := gw.decideReq(decideReq{repo: repo, pr: pr, sha: sha, state: state, meta: meta, rctx: rctx, source: source, act: act,
		patchID: pid, debounced: debounced, deliveryID: deliveryID, ctx: ctx})
	if res.Error == engine.ErrCanceled {
		return nil // a newer head took over; its job decides
	}
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
	if gw.GitHub != nil && !gw.Gate.Policy.Actions.Enabled && !gw.Gate.Policy.Shadow() {
		gw.GitHub.PostCheck(repo, sha, gw.Gate.Policy.QuestionSet, res, gw.verdictURL(repo, sha))
		gw.GitHub.SetLabels(repo, pr, res)
		gw.settleLegacyStatus(j, repo, sha, res)
	}
	return nil
}

// Handler builds the HTTP API.
func (gw *Gateway) Handler(webhookSecret string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		h := map[string]any{"ok": true, "workers": gw.Workers, "queue": gw.Store.QueueDepth(), "model": gw.Gate.Model.Name(), "mode": gw.Gate.Policy.Mode}
		if r, ok := gw.Gate.Model.(*models.Resilient); ok {
			h["breaker"] = r.Stats()
		}
		writeJSON(w, 200, h)
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		// off loopback, /metrics needs a bearer token (MERGEONE_METRICS_TOKEN): rule names and repository
		// activity are not public data. On a loopback bind it is open, for a local scraper.
		if !gw.BoundLocal {
			tok := os.Getenv("MERGEONE_METRICS_TOKEN")
			if tok == "" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+tok)) != 1 {
				writeJSON(w, 403, map[string]string{"error": "/metrics needs Authorization: Bearer $MERGEONE_METRICS_TOKEN when the server is not bound to localhost"})
				return
			}
		}
		var br *models.BreakerStats
		if rm, ok := gw.Gate.Model.(*models.Resilient); ok {
			b := rm.Stats()
			br = &b
		}
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		gw.metrics.write(w, gw.Store.QueueDepth(), br, gw.Gate.Policy.Mode)
	})
	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, r *http.Request) {
		win := 86400
		if v := r.URL.Query().Get("window"); v != "" {
			win, _ = strconv.Atoi(v)
		}
		writeJSON(w, 200, gw.stats(time.Duration(win)*time.Second))
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
			writeJSON(w, 200, gw.decide(body.Repo, body.PR, body.HeadSHA, body.State, meta, nil, "api", act))
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
		if webhookSecret == "" && !gw.BoundLocal && !gw.AllowUnsignedWebhooks {
			writeJSON(w, 401, map[string]string{"error": "webhook secret not configured: set MERGEONE_WEBHOOK_SECRET (or serve --allow-unsigned-webhooks behind a proxy that verifies)"})
			return
		}
		if webhookSecret != "" {
			mac := hmac.New(sha256.New, []byte(webhookSecret))
			mac.Write(raw)
			want := "sha256=" + hex.EncodeToString(mac.Sum(nil))
			if !hmac.Equal([]byte(want), []byte(r.Header.Get("X-Hub-Signature-256"))) {
				writeJSON(w, 401, map[string]string{"error": "bad signature"})
				return
			}
		}
		delivery := r.Header.Get("X-GitHub-Delivery")
		if gw.guard.duplicate(delivery) {
			gw.metrics.webhook(r.Header.Get("X-GitHub-Event"), "duplicate")
			writeJSON(w, 200, map[string]any{"duplicate": true, "delivery": delivery})
			return
		}
		if r.Header.Get("X-GitHub-Event") != "pull_request" {
			gw.metrics.webhook(r.Header.Get("X-GitHub-Event"), "")
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
		if repo == "" {
			gw.guard.forget(delivery)
			writeJSON(w, 400, map[string]string{"error": "bad payload: no repository"})
			return
		}
		if ok, wait := gw.guard.allow(repo); !ok {
			gw.metrics.webhook("pull_request", "rate_limited")
			gw.guard.forget(delivery) // not accepted: the redelivery must be decided, not called a duplicate
			w.Header().Set("Retry-After", strconv.Itoa(int(wait.Seconds())+1))
			writeJSON(w, 429, map[string]any{"error": "rate limited: too many webhooks for " + repo, "retry_after_s": int(wait.Seconds()) + 1})
			return
		}
		gw.metrics.webhook("pull_request", ev.Action)
		switch ev.Action {
		case "opened", "synchronize", "reopened", "ready_for_review":
			if ev.Action == "synchronize" {
				if gw.flights.supersede(repo, pr, sha) != "" { // stop paying for the head this push replaced
					gw.metrics.count("superseded")
				}
				gw.GitHub.InvalidateHead(repo, pr, sha)
				if gw.debounceWindow() > 0 {
					gw.debouncePush(repo, pr, sha, delivery)
					writeJSON(w, 200, map[string]any{"debounced": true, "settle_s": gw.debounceWindow().Seconds()})
					return
				}
			}
			extra := map[string]any{}
			if skipped := gw.flushPending(repo, pr); len(skipped) > 0 { // a non-push event decides now
				extra["debounced_shas"] = skipped
			}
			if delivery != "" {
				extra["delivery_id"] = delivery
			}
			writeJSON(w, 200, map[string]any{"queued": gw.Submit(repo, pr, sha, "webhook", extra)})
		case "closed":
			if gw.flights.supersede(repo, pr, "") != "" { // nothing left to decide
				gw.metrics.count("superseded")
			}
			gw.Store.ClosePR(repo, pr, ev.PullRequest.Merged)
			// Outcome for the slow drift signal and calibrate: merged from the event; iterated is the gateway's
			// proxy for the run-5 label (force_pushes >= 2): decided on >= 3 distinct heads. reverted comes
			// later, from CI (POST /api/outcome).
			merged, iterated := ev.PullRequest.Merged, gw.Store.DistinctHeads(repo, pr) >= store.IteratedHeads
			if _, err := gw.Store.RecordOutcome(store.PROutcome{Repo: repo, PR: pr, Merged: &merged, Iterated: &iterated, Source: "webhook"}); err != nil {
				log.Printf("outcome %s#%d not recorded: %v", repo, pr, err)
			}
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
	gw.apiRoutes(mux)
	return mux
}

// SetWebhookRate sets the per-repository webhook budget (events per minute; 0 = default 300).
func (gw *Gateway) SetWebhookRate(perMinute int) { gw.guard = newWebhookGuard(perMinute) }

// Serve runs the HTTP server until ctx is done.
func (gw *Gateway) Serve(ctx context.Context, addr, webhookSecret string) error {
	gw.BoundLocal = isLoopback(addr)
	if webhookSecret == "" && !gw.BoundLocal {
		if gw.AllowUnsignedWebhooks {
			log.Printf("warning: MERGEONE_WEBHOOK_SECRET unset and --allow-unsigned-webhooks given: webhooks on %s are not verified", addr)
		} else {
			log.Printf("warning: MERGEONE_WEBHOOK_SECRET unset: POST /webhook/github on %s answers 401 until it is set (or --allow-unsigned-webhooks)", addr)
		}
	}
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
