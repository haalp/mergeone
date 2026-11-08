package server

import (
	"fmt"
	"log"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// Webhook debounce (policy `debounce:`, default 45s; 0 disables). Agents push in bursts: every synchronize
// event on a PR starts or extends a per-PR timer, and when it fires only the PR's head at that moment is
// decided (the worker fetches the PR, so a push whose webhook is still in flight is the one decided). Each
// pushed sha gets a `pending` commit status ("mergeone: waiting for pushes to settle") so no head is silently
// unchecked; the decision sets the final status on the head that actually ran, and its gatelog record lists
// the superseded heads in debounced_shas.
//
// Timers live in memory: a gateway restart drops the pending ones. Those heads keep the pending status and
// are decided on the PR's next push (or a reopen / ready_for_review); nothing is decided twice.

// PendingStatusDescription is the commit-status description of a head waiting for the debounce window.
const PendingStatusDescription = "mergeone: waiting for pushes to settle"

type pendingPush struct {
	timer *time.Timer
	shas  []string // every head pushed in this window, oldest first
}

func (gw *Gateway) debounceWindow() time.Duration {
	if gw.Gate == nil || gw.Gate.Policy == nil {
		return 0
	}
	return gw.Gate.Policy.Debounce
}

func pendingKey(repo string, pr int) string { return fmt.Sprintf("%s#%d", repo, pr) }

// debouncePush records a synchronize to sha and (re)starts the PR's timer.
func (gw *Gateway) debouncePush(repo string, pr int, sha string) {
	win := gw.debounceWindow()
	k := pendingKey(repo, pr)
	gw.pmu.Lock()
	p := gw.pending[k]
	if p == nil {
		p = &pendingPush{}
		gw.pending[k] = p
		p.timer = time.AfterFunc(win, func() { gw.firePending(repo, pr, p) })
	} else {
		p.timer.Reset(win)
	}
	if sha != "" && (len(p.shas) == 0 || p.shas[len(p.shas)-1] != sha) {
		p.shas = append(p.shas, sha)
	}
	gw.pmu.Unlock()
	if gw.GitHub != nil && sha != "" && !gw.Gate.Policy.Shadow() {
		go gw.postPendingStatus(repo, sha)
	}
}

// firePending submits the PR's last pushed head once the window closed without another push.
func (gw *Gateway) firePending(repo string, pr int, p *pendingPush) {
	gw.pmu.Lock()
	if gw.pending[pendingKey(repo, pr)] != p {
		gw.pmu.Unlock()
		return
	}
	delete(gw.pending, pendingKey(repo, pr))
	shas := append([]string(nil), p.shas...)
	gw.pmu.Unlock()
	if len(shas) == 0 {
		return
	}
	head := shas[len(shas)-1]
	gw.Submit(repo, pr, head, "webhook", map[string]any{"debounced_shas": shas[:len(shas)-1]})
}

// flushPending cancels the PR's timer (another event decides it now) and returns the heads it was holding.
func (gw *Gateway) flushPending(repo string, pr int) []string {
	gw.pmu.Lock()
	defer gw.pmu.Unlock()
	p := gw.pending[pendingKey(repo, pr)]
	if p == nil {
		return nil
	}
	p.timer.Stop()
	delete(gw.pending, pendingKey(repo, pr))
	return append([]string(nil), p.shas...)
}

// stopPending drops every timer (gateway stop); the heads are re-triggered by the next push.
func (gw *Gateway) stopPending() {
	gw.pmu.Lock()
	defer gw.pmu.Unlock()
	for k, p := range gw.pending {
		p.timer.Stop()
		delete(gw.pending, k)
	}
}

// postPendingStatus marks sha as waiting. The status context is the actions layer's (default "mergeone"),
// so the decision's own status replaces it on the head that runs.
func (gw *Gateway) postPendingStatus(repo, sha string) {
	ctx := gw.Gate.Policy.Actions.StatusContext
	if ctx == "" {
		ctx = "mergeone"
	}
	body := map[string]any{"state": "pending", "context": ctx, "description": PendingStatusDescription}
	if _, code, err := gw.GitHub.do("POST", fmt.Sprintf("%s/repos/%s/statuses/%s", gw.GitHub.API, repo, sha), "application/vnd.github+json", body); err != nil || code != 201 {
		log.Printf("debounce: pending status %s@%s: HTTP %d %v", repo, sha, code, err)
	}
}

// settleLegacyStatus replaces the pending status on the head that ran when the job came through the debounce
// and actions are off (the legacy write-back is a check-run, which would leave the pending status behind).
// It mirrors the check-run: pass and hold (neutral) are success, block is failure.
func (gw *Gateway) settleLegacyStatus(j *store.Job, repo, sha string, res engine.Result) {
	if _, ok := j.Payload["debounced_shas"]; !ok || gw.GitHub == nil || sha == "" {
		return
	}
	state := "success"
	if res.Verdict == "block" {
		state = "failure"
	}
	body := map[string]any{"state": state, "context": "mergeone", "description": StatusDescription(res)}
	if u := gw.verdictURL(repo, sha); u != "" {
		body["target_url"] = u
	}
	_, _, _ = gw.GitHub.do("POST", fmt.Sprintf("%s/repos/%s/statuses/%s", gw.GitHub.API, repo, sha), "application/vnd.github+json", body)
}

// payloadStrings reads a job payload list ([]string in process, []any after JSON).
func payloadStrings(v any) []string {
	switch x := v.(type) {
	case []string:
		return x
	case []any:
		out := make([]string, 0, len(x))
		for _, e := range x {
			out = append(out, fmt.Sprint(e))
		}
		return out
	}
	return nil
}
