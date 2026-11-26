package server

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func TestBoardAndResolveFromAView(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gatelog.jsonl")
	st, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	rec := func(pr int, sha, verdict, route string, extra map[string]any, probs map[string]float64) {
		t.Helper()
		if err := st.Record(store.Record{TS: now, Repo: "o/r", PR: pr, HeadSHA: sha, Source: "test",
			Result:  engine.Result{Verdict: verdict, Route: route, Rule: "r", Extra: extra, Probs: probs},
			Context: map[string]any{"title": "t", "files": 1}}); err != nil {
			t.Fatal(err)
		}
	}
	rec(12, "aaa", "pass", "merge", nil, nil)
	rec(13, "bbb", "hold", "dedup", map[string]any{"duplicate_candidate": 12}, map[string]float64{"duplicate_of": 0.91})

	pol := &engine.Policy{Mode: "shadow"}
	pol.Actions.RelatedContext, pol.Actions.RelatedThreshold = "mergeone/related", 0.7
	b := Board(store.View(path), pol, "", now.Add(-time.Hour), time.Hour)
	rows := b["holds"].([]QueueRow)
	if len(rows) != 1 || rows[0].PR != 13 || rows[0].Related == nil || rows[0].Related.State != "failure" {
		t.Fatalf("holds: %+v", rows)
	}
	g := b["graph"].(map[string]any)
	if n, e := g["nodes"].([]GraphNode), g["edges"].([]GraphEdge); len(n) != 2 || len(e) != 1 || e[0].Relation != "duplicate_of" {
		t.Fatalf("graph: %+v", g)
	}

	// A second process (the MCP server) resolves a head it reads from a fresh view and writes through its own store.
	other, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	gw := &Gateway{Store: other, Gate: &engine.Gate{Policy: pol}}
	if _, code, err := gw.Resolve(store.View(path), ResolveRequest{Repo: "o/r", PR: 13, HeadSHA: "zzz", Action: "approve"}, "ada"); err == nil || code != 404 {
		t.Fatalf("unknown head: code %d err %v", code, err)
	}
	res, _, err := gw.Resolve(store.View(path), ResolveRequest{Repo: "o/r", PR: 13, HeadSHA: "bbb", Action: "link_duplicate"}, "ada")
	if err != nil || res.TargetPR != 12 || res.Login != "ada" {
		t.Fatalf("resolve: %+v %v", res, err)
	}
	if rows := QueueRows(store.View(path), ""); len(rows) != 0 {
		t.Fatalf("a resolved head still waits: %+v", rows)
	}
}
