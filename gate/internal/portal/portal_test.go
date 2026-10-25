package portal

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type fakePortal struct {
	mu      sync.Mutex
	records int
	posts   int
	fail    int // fail this many POSTs with 503 first
	auth    string
}

func (f *fakePortal) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.posts++
	f.auth = r.Header.Get("Authorization")
	if r.URL.Path != "/ingest" {
		w.WriteHeader(404)
		return
	}
	if f.fail > 0 {
		f.fail--
		w.WriteHeader(503)
		return
	}
	var body struct {
		Records []map[string]any `json:"records"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)
	f.records += len(body.Records)
	w.WriteHeader(202)
}

func newSink(t *testing.T, f *fakePortal) *Sink {
	srv := httptest.NewServer(f)
	t.Cleanup(srv.Close)
	s := &Sink{URL: srv.URL, Token: "pt", BatchSize: 3, Linger: 10 * time.Millisecond, Attempts: 3, Backoff: time.Millisecond}
	s.Start()
	return s
}

func TestSinkBatchesAndRetries(t *testing.T) {
	f := &fakePortal{fail: 2}
	s := newSink(t, f)
	for i := 0; i < 7; i++ {
		s.Enqueue(map[string]any{"i": i})
	}
	s.Close(5 * time.Second)
	st := s.Stats()
	if f.records != 7 || st["sent"].(int64) != 7 || st["dropped"].(int64) != 0 || st["failed_attempts"].(int64) != 2 {
		t.Fatalf("records=%d stats=%v", f.records, st)
	}
	if f.auth != "Bearer pt" {
		t.Fatalf("auth %q", f.auth)
	}
}

func TestSinkDropsAfterAttempts(t *testing.T) {
	f := &fakePortal{fail: 100}
	s := newSink(t, f)
	s.Enqueue(1)
	s.Enqueue(2)
	s.Close(5 * time.Second)
	st := s.Stats()
	if st["dropped"].(int64) != 2 || st["sent"].(int64) != 0 || f.posts != 3 || st["last_error"] == nil {
		t.Fatalf("posts=%d stats=%v", f.posts, st)
	}
}

func TestNilSinkIsOff(t *testing.T) {
	var s *Sink
	s.Enqueue(1)
	s.Close(time.Millisecond)
	if New("", "x") != nil || New("http://x", "") != nil || s.Stats() != nil {
		t.Fatal("empty url or token must give a nil sink")
	}
}
