package models

import (
	"context"
	"fmt"
	"math/rand"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// Resilience is the policy's `resilience:` block: how a decision call behaves when the model endpoint
// fails. Retries are for transient failures only (connection errors, timeouts, HTTP 429 and 5xx); a 4xx
// other than 429 and a malformed reply are returned at once. The breaker opens after BreakerFailures
// consecutive failed decisions (after retries) and, while open, every call fails immediately with
// "circuit open" — the engine's on_error outcome (hold → human by default) without waiting on a dead
// endpoint. After BreakerCooldown one probe call is let through; success closes the breaker.
type Resilience struct {
	Retries         int           // extra attempts after the first; default 2
	Backoff         time.Duration // first wait; doubled per attempt, ±25 % jitter, capped at MaxBackoff; default 500ms
	MaxBackoff      time.Duration // default 8s
	BreakerFailures int           // consecutive failed decisions that open the breaker; 0 disables; default 5
	BreakerCooldown time.Duration // default 30s
}

// DefaultResilience is what a policy without a `resilience:` block gets.
func DefaultResilience() Resilience {
	return Resilience{Retries: 2, Backoff: 500 * time.Millisecond, MaxBackoff: 8 * time.Second, BreakerFailures: 5, BreakerCooldown: 30 * time.Second}
}

func (r Resilience) withDefaults() Resilience {
	d := DefaultResilience()
	if r.Retries < 0 {
		r.Retries = 0
	}
	if r.Backoff <= 0 {
		r.Backoff = d.Backoff
	}
	if r.MaxBackoff <= 0 {
		r.MaxBackoff = d.MaxBackoff
	}
	if r.BreakerCooldown <= 0 {
		r.BreakerCooldown = d.BreakerCooldown
	}
	return r
}

// Resilient wraps any engine.Model with retries and a circuit breaker. Pair calls share the wrapper with
// the main call, so one dead endpoint opens one breaker for the whole gate.
type Resilient struct {
	Inner engine.Model
	Opts  Resilience
	Sleep func(time.Duration) // test hook; nil → time.Sleep
	Now   func() time.Time    // test hook; nil → time.Now

	mu          sync.Mutex
	consecutive int       // failed decisions in a row (after retries)
	openUntil   time.Time // zero → closed
	probing     bool      // half-open: one call in flight
	calls       int
	failures    int
	retries     int
	opened      int
	shortCirc   int
}

// WithResilience wraps m; a nil m is returned as is.
func WithResilience(m engine.Model, o Resilience) *Resilient {
	return &Resilient{Inner: m, Opts: o.withDefaults()}
}

func (r *Resilient) Name() string { return r.Inner.Name() }

// ErrCircuitOpen is the prefix of the error a short-circuited call carries.
const ErrCircuitOpen = "CircuitOpen"

var httpStatusRe = regexp.MustCompile(`^HTTP (\d{3})`)

// Retryable reports whether a Decision error is transient: connection errors (which include client
// timeouts), HTTP 429 and HTTP 5xx. Anything else is a request the endpoint rejected or a reply we
// cannot read; retrying it would repeat the same failure.
func Retryable(errStr string) bool {
	if errStr == "" {
		return false
	}
	if strings.HasPrefix(errStr, "ConnectError") {
		return true
	}
	if m := httpStatusRe.FindStringSubmatch(errStr); m != nil {
		code, _ := strconv.Atoi(m[1])
		return code == 429 || code >= 500
	}
	return false
}

func (r *Resilient) now() time.Time {
	if r.Now != nil {
		return r.Now()
	}
	return time.Now()
}

func (r *Resilient) sleep(d time.Duration) {
	if r.Sleep != nil {
		r.Sleep(d)
		return
	}
	time.Sleep(d)
}

// backoff is the wait before attempt n (1-based): Backoff·2^(n-1) with ±25 % jitter, capped.
func (r *Resilient) backoff(n int) time.Duration {
	d := r.Opts.Backoff << uint(n-1)
	if d > r.Opts.MaxBackoff || d <= 0 {
		d = r.Opts.MaxBackoff
	}
	j := 0.75 + rand.Float64()*0.5
	return time.Duration(float64(d) * j)
}

// admit decides whether a call may go to the endpoint now. Closed → yes. Open and cooled down → one
// probe. Open → no, with the time the breaker is retried.
func (r *Resilient) admit() (ok bool, retryAt time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls++
	if r.openUntil.IsZero() {
		return true, time.Time{}
	}
	if r.now().Before(r.openUntil) || r.probing {
		r.shortCirc++
		return false, r.openUntil
	}
	r.probing = true
	return true, time.Time{}
}

func (r *Resilient) settle(ok bool, attempts int, counts bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.retries += attempts - 1
	r.probing = false
	if ok {
		r.consecutive, r.openUntil = 0, time.Time{}
		return
	}
	r.failures++
	if !counts {
		if !r.openUntil.IsZero() && !r.now().Before(r.openUntil) { // a failed probe reopens whatever call it was
			r.openUntil = r.now().Add(r.Opts.BreakerCooldown)
			r.opened++
		}
		return
	}
	r.consecutive++
	if r.Opts.BreakerFailures > 0 && r.consecutive >= r.Opts.BreakerFailures {
		if r.openUntil.IsZero() || !r.now().Before(r.openUntil) {
			r.opened++
		}
		r.openUntil = r.now().Add(r.Opts.BreakerCooldown)
	}
}

// release ends a call that was admitted but neither succeeded nor failed (cancelled).
func (r *Resilient) release() {
	r.mu.Lock()
	r.probing = false
	r.mu.Unlock()
}

// Decide calls the inner model with retries; a short-circuited call returns at once with ErrCircuitOpen.
func (r *Resilient) Decide(state string, questions []string) engine.Decision {
	return r.decide(context.Background(), state, questions, true)
}

// DecideCtx is Decide with a context: no attempt is started after ctx is cancelled, and a cancelled call is
// neither a success nor a failure for the breaker.
func (r *Resilient) DecideCtx(ctx context.Context, state string, questions []string) engine.Decision {
	return r.decide(ctx, state, questions, true)
}

// DecidePairCtx is DecidePair with a context.
func (r *Resilient) DecidePairCtx(ctx context.Context, state string, questions []string) engine.Decision {
	return r.decide(ctx, state, questions, false)
}

// DecidePair is Decide for a pair call: retried the same way and refused while the breaker is open, but a
// failure does not count toward opening it — one PR's ten pair calls failing together is one bad moment, not ten
// failed decisions; the main call is what the breaker watches. A success still closes it.
func (r *Resilient) DecidePair(state string, questions []string) engine.Decision {
	return r.decide(context.Background(), state, questions, false)
}

// MaxRetryAfter caps how long an endpoint's Retry-After is honoured.
const MaxRetryAfter = 30 * time.Second

func (r *Resilient) decide(ctx context.Context, state string, questions []string, counts bool) engine.Decision {
	ok, retryAt := r.admit()
	if !ok {
		return engine.Decision{Probs: nan(questions), ModelVersion: "",
			Error: fmt.Sprintf("%s: %d consecutive failures, retrying at %s", ErrCircuitOpen, r.Opts.BreakerFailures, retryAt.UTC().Format(time.RFC3339))}
	}
	var d engine.Decision
	attempts := 0
	for {
		attempts++
		if cd, ok := r.Inner.(engine.ContextDecider); ok {
			d = cd.DecideCtx(ctx, state, questions)
		} else {
			d = r.Inner.Decide(state, questions)
		}
		if ctx.Err() != nil { // superseded: not this endpoint's failure, and nothing to retry
			d.Error, d.Attempts = engine.ErrCanceled, attempts
			r.release()
			return d
		}
		if d.Error == "" || !Retryable(d.Error) || attempts > r.Opts.Retries {
			break
		}
		wait := r.backoff(attempts)
		if ra := min(d.RetryAfter, MaxRetryAfter); ra > wait {
			wait = ra
		}
		r.sleep(wait)
	}
	d.Attempts = attempts
	r.settle(d.Error == "", attempts, counts)
	return d
}

// BreakerStats is the breaker's state for /healthz and /stats.
type BreakerStats struct {
	State               string `json:"state"` // closed | open | half_open
	ConsecutiveFailures int    `json:"consecutive_failures"`
	RetryAt             string `json:"retry_at,omitempty"`
	Calls               int    `json:"calls"`
	Failures            int    `json:"failures"`
	Retries             int    `json:"retries"`
	Opened              int    `json:"opened"`
	ShortCircuited      int    `json:"short_circuited"`
}

// Stats snapshots the breaker.
func (r *Resilient) Stats() BreakerStats {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := BreakerStats{State: "closed", ConsecutiveFailures: r.consecutive, Calls: r.calls, Failures: r.failures, Retries: r.retries, Opened: r.opened, ShortCircuited: r.shortCirc}
	if !r.openUntil.IsZero() {
		s.State = "open"
		s.RetryAt = r.openUntil.UTC().Format(time.RFC3339)
		if r.probing || !r.now().Before(r.openUntil) {
			s.State = "half_open"
		}
	}
	return s
}
