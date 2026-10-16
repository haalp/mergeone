package memory

import (
	"testing"
	"time"
)

func TestRetrievalPrefersFileOverlapAndText(t *testing.T) {
	s, _ := Open("")
	r := s.Repo("acme/api")
	now := time.Now().UTC()
	ts := func(d int) string { return now.AddDate(0, 0, -d).Format(time.RFC3339) }
	_ = r.Upsert(Entry{Number: 1, Title: "fix: retry on 429 in http client", Body: "adds backoff", Author: "bot-a", State: "MERGED", CreatedAt: ts(3), MergedAt: ts(2),
		Files: []FileStat{{"pkg/http/client.go", 10, 2}, {"pkg/http/client_test.go", 20, 0}}})
	_ = r.Upsert(Entry{Number: 2, Title: "docs: update README badges", Author: "human", State: "MERGED", CreatedAt: ts(5), MergedAt: ts(4), Files: []FileStat{{"README.md", 3, 3}}})
	_ = r.Upsert(Entry{Number: 3, Title: "fix: handle 429 retries in client", Author: "bot-b", State: "OPEN", CreatedAt: ts(1), Files: []FileStat{{"pkg/http/client.go", 8, 1}}})
	_ = r.Upsert(Entry{Number: 4, Title: "old unrelated change", Author: "x", State: "MERGED", CreatedAt: ts(200), MergedAt: ts(199), Files: []FileStat{{"pkg/http/client.go", 1, 1}}})
	cs := r.Candidates(0, "fix retry on 429 responses", "backoff for the http client", []string{"pkg/http/client.go"}, "bot-a", now, 5)
	if len(cs) != 3 {
		t.Fatalf("want 3 in-window candidates, got %d", len(cs))
	}
	if cs[0].Number != 1 && cs[0].Number != 3 {
		t.Fatalf("top candidate should be a client.go retry PR, got #%d", cs[0].Number)
	}
	if cs[len(cs)-1].Number != 2 {
		t.Fatalf("README change should rank last, got #%d", cs[len(cs)-1].Number)
	}
	for _, c := range cs {
		if c.Number == 4 {
			t.Fatalf("PR outside the ±45 day window must be excluded")
		}
	}
	ps := PairState("PULL REQUEST TITLE: x", cs[0])
	if len(ps) == 0 || ps[len(ps)-len("How does A relate to B?"):] != "How does A relate to B?" {
		t.Fatalf("pair state malformed: %q", ps[len(ps)-30:])
	}
}

func TestOpenEmptyDirAndPersist(t *testing.T) {
	dir := t.TempDir()
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Repo("o/n").Upsert(Entry{Number: 7, Title: "t", CreatedAt: time.Now().UTC().Format(time.RFC3339)}); err != nil {
		t.Fatal(err)
	}
	s2, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if s2.Repo("o/n").Len() != 1 {
		t.Fatalf("expected 1 persisted entry, got %d", s2.Repo("o/n").Len())
	}
}
