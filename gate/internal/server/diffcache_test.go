package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

// TestPRStateDiffCache: a second fetch of the same head serves files+diff from the cache (only the PR
// metadata is requested again) and renders the identical state; a new head misses; InvalidateHead keeps
// only the new head; the LRU is bounded.
func TestPRStateDiffCache(t *testing.T) {
	var sha atomic.Value
	sha.Store("aaa")
	var metaN, filesN, diffN atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/files"):
			filesN.Add(1)
			fmt.Fprint(w, `[{"filename":"pkg/a/x.go","additions":3,"deletions":1}]`)
		case r.Header.Get("Accept") == "application/vnd.github.diff":
			diffN.Add(1)
			fmt.Fprintf(w, "diff --git a/pkg/a/x.go b/pkg/a/x.go\n+head %s\n", sha.Load())
		default:
			metaN.Add(1)
			fmt.Fprintf(w, `{"title":"t","body":"b","head":{"sha":%q},"user":{"login":"u"}}`, sha.Load())
		}
	}))
	defer srv.Close()
	g := NewGitHub("tok")
	g.API = srv.URL
	s1, _, h1, _, _, err := g.PRStateMeta("o/r", 7)
	if err != nil || h1 != "aaa" {
		t.Fatal(err, h1)
	}
	s2, _, _, _, _, _ := g.PRStateMeta("o/r", 7)
	if s1 != s2 || metaN.Load() != 2 || filesN.Load() != 1 || diffN.Load() != 1 {
		t.Fatalf("same head should hit: meta %d files %d diff %d same=%v", metaN.Load(), filesN.Load(), diffN.Load(), s1 == s2)
	}
	sha.Store("bbb")
	g.InvalidateHead("o/r", 7, "bbb")
	if g.diffs.len() != 0 {
		t.Fatalf("synchronize to bbb should drop aaa, %d left", g.diffs.len())
	}
	s3, _, _, _, _, _ := g.PRStateMeta("o/r", 7)
	if s3 == s1 || filesN.Load() != 2 || diffN.Load() != 2 || !strings.Contains(s3, "+head bbb") {
		t.Fatalf("new head must be fetched: files %d diff %d", filesN.Load(), diffN.Load())
	}
	c := newDiffCache(3)
	for i := 0; i < 5; i++ {
		c.put("o/r", i, "s", nil, "d")
	}
	if _, _, ok := c.get("o/r", 0, "s"); ok || c.len() != 3 {
		t.Fatalf("LRU should evict oldest, len %d", c.len())
	}
}
