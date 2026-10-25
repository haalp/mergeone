// Package portal posts gatelog records to the hosted portal (docs/portal.md): batched, async, retried
// with backoff, and never in a verdict's way. A full buffer or a batch that keeps failing is dropped and
// counted; the gatelog stays the source of truth.
package portal

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Sink batches records and POSTs them as {"records": [...]} to <URL>/ingest.
type Sink struct {
	URL, Token string
	Client     *http.Client
	BatchSize  int           // records per POST (default 100)
	Linger     time.Duration // max wait before a partial batch is sent (default 1s)
	Attempts   int           // tries per batch before it is dropped (default 5)
	Backoff    time.Duration // first retry delay, doubled per retry (default 500ms)

	in      chan any
	stop    chan struct{}
	done    chan struct{}
	once    sync.Once
	sent    atomic.Int64
	dropped atomic.Int64
	failed  atomic.Int64 // failed POST attempts
	lastErr atomic.Value // string
}

// New returns a started sink, or nil when url or token is empty (the portal block is off).
func New(url, token string) *Sink {
	if url == "" || token == "" {
		return nil
	}
	s := &Sink{URL: strings.TrimRight(url, "/"), Token: token}
	s.Start()
	return s
}

// Start applies defaults and starts the sender. New calls it; call it yourself after setting fields on
// a Sink built by hand.
func (s *Sink) Start() {
	if s.Client == nil {
		s.Client = &http.Client{Timeout: 15 * time.Second}
	}
	if s.BatchSize <= 0 {
		s.BatchSize = 100
	}
	if s.Linger <= 0 {
		s.Linger = time.Second
	}
	if s.Attempts <= 0 {
		s.Attempts = 5
	}
	if s.Backoff <= 0 {
		s.Backoff = 500 * time.Millisecond
	}
	s.in = make(chan any, 10_000)
	s.stop = make(chan struct{})
	s.done = make(chan struct{})
	go s.loop()
}

// Enqueue hands a record to the sender without blocking; a full buffer drops it (counted).
func (s *Sink) Enqueue(rec any) {
	if s == nil {
		return
	}
	select {
	case s.in <- rec:
	default:
		s.dropped.Add(1)
	}
}

// Close sends what is buffered (each batch still gets its retries, bounded by timeout) and stops.
func (s *Sink) Close(timeout time.Duration) {
	if s == nil {
		return
	}
	s.once.Do(func() { close(s.stop) })
	select {
	case <-s.done:
	case <-time.After(timeout):
	}
}

// Stats is the `portal` block of /stats.
func (s *Sink) Stats() map[string]any {
	if s == nil {
		return nil
	}
	out := map[string]any{"url": s.URL, "sent": s.sent.Load(), "dropped": s.dropped.Load(),
		"failed_attempts": s.failed.Load(), "pending": len(s.in)}
	if e, _ := s.lastErr.Load().(string); e != "" {
		out["last_error"] = e
	}
	return out
}

func (s *Sink) loop() {
	defer close(s.done)
	var batch []any
	timer := time.NewTimer(s.Linger)
	defer timer.Stop()
	flush := func() {
		if len(batch) > 0 {
			s.send(batch)
			batch = nil
		}
	}
	for {
		select {
		case r := <-s.in:
			batch = append(batch, r)
			if len(batch) >= s.BatchSize {
				flush()
			}
		case <-timer.C:
			flush()
			timer.Reset(s.Linger)
		case <-s.stop:
			for {
				select {
				case r := <-s.in:
					batch = append(batch, r)
					if len(batch) >= s.BatchSize {
						flush()
					}
				default:
					flush()
					return
				}
			}
		}
	}
}

// send POSTs one batch, retrying with exponential backoff; after Attempts failures it is dropped.
// A 4xx other than 408/429 is not retried (the portal refused the batch).
func (s *Sink) send(batch []any) {
	body, err := json.Marshal(map[string]any{"records": batch})
	if err != nil {
		s.dropped.Add(int64(len(batch)))
		return
	}
	wait := s.Backoff
	for attempt := 1; ; attempt++ {
		retry, err := s.post(body)
		if err == nil {
			s.sent.Add(int64(len(batch)))
			return
		}
		s.failed.Add(1)
		s.lastErr.Store(err.Error())
		if !retry || attempt >= s.Attempts {
			s.dropped.Add(int64(len(batch)))
			return
		}
		select {
		case <-time.After(wait):
		case <-s.stop: // shutting down: one quick last try per batch, no long sleeps
			wait = 0
		}
		wait *= 2
	}
}

func (s *Sink) post(body []byte) (retry bool, err error) {
	req, err := http.NewRequest("POST", s.URL+"/ingest", bytes.NewReader(body))
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+s.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.Client.Do(req)
	if err != nil {
		return true, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	if resp.StatusCode/100 == 2 {
		return false, nil
	}
	retry = resp.StatusCode >= 500 || resp.StatusCode == 408 || resp.StatusCode == 429
	return retry, fmt.Errorf("POST /ingest: HTTP %d", resp.StatusCode)
}
