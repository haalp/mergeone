package server

import (
	"sync"
	"time"
)

// Webhook guard (v0.5): the three checks a `POST /webhook/github` goes through after the signature.
//
//   - Signature policy: with MERGEONE_WEBHOOK_SECRET unset, a gateway bound to a non-loopback address refuses
//     every webhook (401 "webhook secret not configured") unless started with --allow-unsigned-webhooks. On a
//     loopback bind (a reverse proxy or a tunnel in front) unsigned deliveries are accepted as before.
//   - Replay: GitHub's X-GitHub-Delivery id is remembered for DeliveryTTL; a second delivery with the same id
//     (GitHub's "Redeliver" button, or a replayed capture) answers 200 {"duplicate": true} and enqueues nothing.
//     Decisions are idempotent per head sha anyway; this saves the fetch and keeps the gatelog honest.
//   - Rate limit: a token bucket per repository (RatePerMinute, burst = one minute's worth). Beyond it the
//     webhook answers 429 with Retry-After; GitHub redelivers failed webhooks from its UI, nothing is lost
//     silently, and one noisy repository cannot starve the queue for the others.
//
// All three are in memory and reset with the process.

// DeliveryTTL is how long a delivery id is remembered.
const DeliveryTTL = 24 * time.Hour

// DefaultWebhookRatePerMinute is the per-repository webhook budget when the flag is not set.
const DefaultWebhookRatePerMinute = 300

type webhookGuard struct {
	mu        sync.Mutex
	seen      map[string]time.Time // delivery id → first seen
	buckets   map[string]*bucket   // repo → tokens
	rate      float64              // tokens per second
	burst     float64
	now       func() time.Time
	lastSweep time.Time
}

type bucket struct {
	tokens float64
	last   time.Time
}

func newWebhookGuard(perMinute int) *webhookGuard {
	if perMinute <= 0 {
		perMinute = DefaultWebhookRatePerMinute
	}
	return &webhookGuard{seen: map[string]time.Time{}, buckets: map[string]*bucket{}, rate: float64(perMinute) / 60, burst: float64(perMinute), now: time.Now}
}

// duplicate reports whether delivery was already seen (and remembers it when not). An empty id is never a
// duplicate: a client that sends none (curl in a test) is not deduplicated.
func (g *webhookGuard) duplicate(delivery string) bool {
	if g == nil || delivery == "" {
		return false
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	now := g.now()
	if now.Sub(g.lastSweep) > time.Hour {
		for id, t := range g.seen {
			if now.Sub(t) > DeliveryTTL {
				delete(g.seen, id)
			}
		}
		g.lastSweep = now
	}
	if t, ok := g.seen[delivery]; ok && now.Sub(t) <= DeliveryTTL {
		return true
	}
	g.seen[delivery] = now
	return false
}

// forget drops a delivery id that was not accepted (rate limited, bad payload), so GitHub's redelivery of it is
// decided instead of being answered as a duplicate.
func (g *webhookGuard) forget(delivery string) {
	if g == nil || delivery == "" {
		return
	}
	g.mu.Lock()
	delete(g.seen, delivery)
	g.mu.Unlock()
}

// allow takes one token from repo's bucket; when the bucket is empty it returns false and the wait until the
// next token.
func (g *webhookGuard) allow(repo string) (bool, time.Duration) {
	if g == nil {
		return true, 0
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	now := g.now()
	b := g.buckets[repo]
	if b == nil {
		b = &bucket{tokens: g.burst, last: now}
		g.buckets[repo] = b
	}
	b.tokens = min(g.burst, b.tokens+now.Sub(b.last).Seconds()*g.rate)
	b.last = now
	if b.tokens >= 1 {
		b.tokens--
		return true, 0
	}
	wait := time.Duration((1 - b.tokens) / g.rate * float64(time.Second))
	return false, wait
}
