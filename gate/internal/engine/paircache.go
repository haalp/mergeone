package engine

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"sync"
	"time"
)

// ErrCanceled is the Decision.Error of a call abandoned because its context was cancelled (the head it was
// deciding was superseded by a newer push). A cancelled decision is never recorded and never acted on.
const ErrCanceled = "Canceled"

// ContextDecider is implemented by a model whose call can be abandoned: the HTTP backends and the resilience
// wrapper. A model without it is called through Decide and runs to completion.
type ContextDecider interface {
	DecideCtx(ctx context.Context, state string, questions []string) Decision
}

// PairContextDecider is PairDecider with a context.
type PairContextDecider interface {
	DecidePairCtx(ctx context.Context, state string, questions []string) Decision
}

func (g *Gate) callMain(ctx context.Context, state string) Decision {
	if ctx.Err() != nil {
		return Decision{Probs: map[string]float64{}, Error: ErrCanceled}
	}
	if cd, ok := g.Model.(ContextDecider); ok {
		return cd.DecideCtx(ctx, state, g.questions())
	}
	return g.Model.Decide(state, g.questions())
}

// PairCache remembers same_change answers by the exact pair-call input (and the model that answered), so a PR
// that is pushed again asks only about the candidates whose rendered pair changed. The key is the sha256 of the
// model name and the pair state: an identical input, never a similar one. Errors are not cached. Entries live
// for TTL and the cache holds at most Max of them (oldest dropped first). In memory; empty after a restart.
type PairCache struct {
	TTL time.Duration // default 24h
	Max int           // default 20000

	mu    sync.Mutex
	m     map[string]pairEntry
	order []string
	now   func() time.Time
}

type pairEntry struct {
	p       float64
	version string
	at      time.Time
}

// NewPairCache returns a cache with the defaults.
func NewPairCache() *PairCache {
	return &PairCache{TTL: 24 * time.Hour, Max: 20000, m: map[string]pairEntry{}, now: time.Now}
}

func pairKey(model, pairState string) string {
	h := sha256.New()
	h.Write([]byte(model))
	h.Write([]byte{0})
	h.Write([]byte(pairState))
	return hex.EncodeToString(h.Sum(nil))
}

func (c *PairCache) get(key string) (pairEntry, bool) {
	if c == nil {
		return pairEntry{}, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.m[key]
	if !ok || c.now().Sub(e.at) > c.TTL {
		return pairEntry{}, false
	}
	return e, true
}

func (c *PairCache) put(key string, e pairEntry) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	e.at = c.now()
	if _, ok := c.m[key]; !ok {
		c.order = append(c.order, key)
	}
	c.m[key] = e
	for len(c.m) > c.Max && len(c.order) > 0 {
		delete(c.m, c.order[0])
		c.order = c.order[1:]
	}
}

// Len is the number of cached answers.
func (c *PairCache) Len() int {
	if c == nil {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.m)
}

// decidePair answers one pair call: from the cache when the identical input was answered before, else from the
// model. cached reports a cache hit (no call was made: no latency, tokens or cost).
func (g *Gate) decidePair(ctx context.Context, pairState string) (d Decision, cached bool) {
	if ctx.Err() != nil {
		return Decision{Probs: map[string]float64{}, Error: ErrCanceled}, false
	}
	key := ""
	if g.PairCache != nil && g.Model != nil {
		key = pairKey(g.Model.Name(), pairState)
		if e, ok := g.PairCache.get(key); ok {
			return Decision{Probs: map[string]float64{"same_change": e.p}, ModelVersion: e.version}, true
		}
	}
	qs := []string{"same_change"}
	switch m := g.Model.(type) {
	case PairContextDecider:
		d = m.DecidePairCtx(ctx, pairState, qs)
	case PairDecider:
		d = m.DecidePair(pairState, qs)
	case ContextDecider:
		d = m.DecideCtx(ctx, pairState, qs)
	default:
		d = g.Model.Decide(pairState, qs)
	}
	if key != "" && d.Error == "" {
		if p, ok := d.Probs["same_change"]; ok && p == p { // not NaN
			g.PairCache.put(key, pairEntry{p: p, version: d.ModelVersion})
		}
	}
	return d, false
}
