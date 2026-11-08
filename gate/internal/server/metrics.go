package server

import (
	"fmt"
	"io"
	"sort"
	"strings"
	"sync"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/models"
	"github.com/haalp/mergeone/gate/internal/store"
)

// metrics are the gateway's in-process counters since start, served at GET /metrics in the Prometheus text
// format (no client library: a handful of counters and one histogram). Every number here is also derivable
// from the gatelog; /metrics exists so an existing Prometheus + Grafana setup can scrape the gateway without
// reading the log. Names carry the mergeone_ prefix.
type metrics struct {
	mu        sync.Mutex
	decisions map[[3]string]int // verdict, rule, source
	errors    map[string]int    // store.ErrorKind
	webhooks  map[[2]string]int // event, action
	retries   int
	calls     int
	pairErrs  int
	tokIn     int
	tokOut    int
	reused    int
	debounced int
	shadow    int
	cost      float64
	latBucket []int // decision latency histogram counts per bucket (cumulative on output)
	latSum    float64
	latCount  int
}

// latencyBuckets are the histogram's upper bounds in seconds.
var latencyBuckets = []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300}

func newMetrics() *metrics {
	return &metrics{decisions: map[[3]string]int{}, errors: map[string]int{}, webhooks: map[[2]string]int{}, latBucket: make([]int, len(latencyBuckets)+1)}
}

// observe records one decision as it is written to the gatelog.
func (m *metrics) observe(rec store.Record) {
	if m == nil {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	r := rec.Result
	m.decisions[[3]string{r.Verdict, r.Rule, rec.Source}]++
	if r.Error != "" {
		m.errors[store.ErrorKind(r.Error)]++
	}
	m.retries += r.Retries
	m.calls += r.ModelCalls
	m.pairErrs += r.PairErrors
	m.tokIn += r.InputTokens
	m.tokOut += r.OutputTokens
	m.cost += r.CostUSD
	if rec.ReusedFrom != "" {
		m.reused++
	}
	m.debounced += len(rec.DebouncedSHAs)
	if rec.Shadow {
		m.shadow++
	}
	if r.LatencyMS > 0 && rec.ReusedFrom == "" {
		s := r.LatencyMS / 1000
		i := sort.SearchFloat64s(latencyBuckets, s) // first bucket with bound >= s
		m.latBucket[i]++
		m.latSum += s
		m.latCount++
	}
}

func (m *metrics) webhook(event, action string) {
	if m == nil {
		return
	}
	m.mu.Lock()
	m.webhooks[[2]string{event, action}]++
	m.mu.Unlock()
}

// write renders the exposition. queue and breaker are read live from the gateway.
func (m *metrics) write(w io.Writer, queue map[string]int, breaker *models.BreakerStats, mode string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	p("# HELP mergeone_decisions_total Decisions recorded, by verdict, rule and source.\n# TYPE mergeone_decisions_total counter\n")
	for _, k := range sortedKeys3(m.decisions) {
		p("mergeone_decisions_total{verdict=%q,rule=%q,source=%q} %d\n", k[0], k[1], k[2], m.decisions[k])
	}
	p("# HELP mergeone_model_errors_total Decisions that ended in on_error, by error kind.\n# TYPE mergeone_model_errors_total counter\n")
	for _, k := range sortedKeys(m.errors) {
		p("mergeone_model_errors_total{kind=%q} %d\n", k, m.errors[k])
	}
	p("# HELP mergeone_model_calls_total Model calls that answered (main and pair calls).\n# TYPE mergeone_model_calls_total counter\nmergeone_model_calls_total %d\n", m.calls)
	p("# HELP mergeone_model_retries_total Extra model calls made by the resilience layer.\n# TYPE mergeone_model_retries_total counter\nmergeone_model_retries_total %d\n", m.retries)
	p("# HELP mergeone_pair_call_errors_total Pair calls that failed (the candidate counts as not a duplicate).\n# TYPE mergeone_pair_call_errors_total counter\nmergeone_pair_call_errors_total %d\n", m.pairErrs)
	p("# HELP mergeone_model_tokens_total Tokens the endpoints reported, by direction.\n# TYPE mergeone_model_tokens_total counter\nmergeone_model_tokens_total{direction=\"input\"} %d\nmergeone_model_tokens_total{direction=\"output\"} %d\n", m.tokIn, m.tokOut)
	p("# HELP mergeone_model_cost_usd_total Model cost at list price.\n# TYPE mergeone_model_cost_usd_total counter\nmergeone_model_cost_usd_total %.6f\n", m.cost)
	p("# HELP mergeone_verdicts_reused_total Verdicts copied from an earlier head with the same patch id (no model call).\n# TYPE mergeone_verdicts_reused_total counter\nmergeone_verdicts_reused_total %d\n", m.reused)
	p("# HELP mergeone_pushes_debounced_total Heads superseded inside the debounce window and never decided.\n# TYPE mergeone_pushes_debounced_total counter\nmergeone_pushes_debounced_total %d\n", m.debounced)
	p("# HELP mergeone_shadow_decisions_total Decisions made under mode: shadow.\n# TYPE mergeone_shadow_decisions_total counter\nmergeone_shadow_decisions_total %d\n", m.shadow)
	p("# HELP mergeone_webhooks_total GitHub webhook deliveries received, by event and action.\n# TYPE mergeone_webhooks_total counter\n")
	for _, k := range sortedKeys2(m.webhooks) {
		p("mergeone_webhooks_total{event=%q,action=%q} %d\n", k[0], k[1], m.webhooks[k])
	}
	p("# HELP mergeone_decision_latency_seconds Wall clock of a decision that called the model.\n# TYPE mergeone_decision_latency_seconds histogram\n")
	cum := 0
	for i, b := range latencyBuckets {
		cum += m.latBucket[i]
		p("mergeone_decision_latency_seconds_bucket{le=\"%g\"} %d\n", b, cum)
	}
	cum += m.latBucket[len(latencyBuckets)]
	p("mergeone_decision_latency_seconds_bucket{le=\"+Inf\"} %d\nmergeone_decision_latency_seconds_sum %.3f\nmergeone_decision_latency_seconds_count %d\n", cum, m.latSum, m.latCount)
	p("# HELP mergeone_queue_jobs Jobs in the decision queue, by status.\n# TYPE mergeone_queue_jobs gauge\n")
	for _, k := range []string{"queued", "running", "done", "failed"} {
		p("mergeone_queue_jobs{status=%q} %d\n", k, queue[k])
	}
	p("# HELP mergeone_mode_shadow 1 when the policy is in shadow mode.\n# TYPE mergeone_mode_shadow gauge\nmergeone_mode_shadow %d\n", b2i(mode == engine.ModeShadow))
	if breaker != nil {
		p("# HELP mergeone_breaker_open 1 while the circuit breaker is open or half-open.\n# TYPE mergeone_breaker_open gauge\nmergeone_breaker_open %d\n", b2i(breaker.State != "closed"))
		p("# HELP mergeone_breaker_opened_total Times the breaker opened.\n# TYPE mergeone_breaker_opened_total counter\nmergeone_breaker_opened_total %d\n", breaker.Opened)
		p("# HELP mergeone_breaker_short_circuited_total Calls refused while the breaker was open.\n# TYPE mergeone_breaker_short_circuited_total counter\nmergeone_breaker_short_circuited_total %d\n", breaker.ShortCircuited)
	}
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func sortedKeys(m map[string]int) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func sortedKeys2(m map[[2]string]int) [][2]string {
	ks := make([][2]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Slice(ks, func(i, j int) bool { return strings.Join(ks[i][:], "\x00") < strings.Join(ks[j][:], "\x00") })
	return ks
}

func sortedKeys3(m map[[3]string]int) [][3]string {
	ks := make([][3]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Slice(ks, func(i, j int) bool { return strings.Join(ks[i][:], "\x00") < strings.Join(ks[j][:], "\x00") })
	return ks
}
