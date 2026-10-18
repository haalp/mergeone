package memory

import (
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// TestStoreCandidatesUsesV2: Store.Candidates returns A1's top-k order by default, rebuilds the v2 index
// after Upsert, renders PairState from the stored entry, and RetrievalVersion = 1 restores v1.
func TestStoreCandidatesUsesV2(t *testing.T) {
	s, _ := Open("")
	r := s.Repo("acme/api")
	now := time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
	ts := func(d int) string { return now.AddDate(0, 0, -d).Format(time.RFC3339) }
	// #10 shares the issue (#99) but no file and little text: v2 ranks it first (issue weight 0.5), v1 does not.
	_ = r.Upsert(Entry{Number: 10, Title: "Resolve flaky startup", Body: "Closes #99", Author: "bot-a", State: "CLOSED", CreatedAt: ts(4),
		Files: []FileStat{{"cmd/server/main.go", 4, 1}}})
	_ = r.Upsert(Entry{Number: 11, Title: "fix retry on 429 in http client", Body: "adds backoff", Author: "bot-b", State: "MERGED", CreatedAt: ts(3), MergedAt: ts(2),
		Files: []FileStat{{"pkg/http/client.go", 10, 2}, {"pkg/http/client_test.go", 20, 0}}})
	_ = r.Upsert(Entry{Number: 12, Title: "docs: README badges", Author: "human", State: "MERGED", CreatedAt: ts(5), MergedAt: ts(4), Files: []FileStat{{"README.md", 3, 3}}})
	meta := engine.PRMeta{Repo: "acme/api", Number: 20, Title: "fix retry on 429 responses", Body: "backoff for the http client. Fixes #99",
		Paths: []string{"pkg/http/client.go"}, Author: "bot-c", CreatedAt: now}

	want := func() []int {
		var ns []int
		for _, c := range r.CandidatesV2(NewQueryV2("acme/api", 20, meta.Title, meta.Body, meta.Paths, meta.Author, now), 2) {
			ns = append(ns, c.Number)
		}
		return ns
	}
	got := s.Candidates(meta, "PULL REQUEST TITLE: a", 2)
	w := want()
	if len(got) != 2 || len(w) != 2 || got[0].Number != w[0] || got[1].Number != w[1] {
		t.Fatalf("Store.Candidates %v, want v2 top-2 %v", got, w)
	}
	if got[0].Number != 10 {
		t.Fatalf("shared-issue PR should rank first under v2, got #%d", got[0].Number)
	}
	if !strings.Contains(got[0].PairState, "Closes #99") || !strings.Contains(got[0].PairState, "  cmd/server/main.go") {
		t.Fatalf("pair state not rendered from the stored entry: %q", got[0].PairState)
	}

	// Upsert resets the v2 index: a new shared-issue PR with the same file must appear.
	_ = r.Upsert(Entry{Number: 13, Title: "fix retry on 429 responses in client", Body: "Fixes #99 with backoff", Author: "bot-a", State: "OPEN", CreatedAt: ts(1),
		Files: []FileStat{{"pkg/http/client.go", 8, 1}}})
	got = s.Candidates(meta, "PULL REQUEST TITLE: a", 2)
	w = want()
	if got[0].Number != 13 || got[0].Number != w[0] || got[1].Number != w[1] {
		t.Fatalf("after Upsert: Store.Candidates %v, want v2 %v with #13 first", got, w)
	}

	s.RetrievalVersion = 1
	v1 := r.Candidates(20, meta.Title, meta.Body, meta.Paths, meta.Author, now, 2)
	got = s.Candidates(meta, "PULL REQUEST TITLE: a", 2)
	for i := range v1 {
		if got[i].Number != v1[i].Number || got[i].PairState != PairState("PULL REQUEST TITLE: a", v1[i]) {
			t.Fatalf("RetrievalVersion=1 should reproduce v1: rank %d got #%d want #%d", i+1, got[i].Number, v1[i].Number)
		}
	}
}
