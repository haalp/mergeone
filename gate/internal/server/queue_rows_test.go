package server

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func TestQueueRowsAreCompactAndAViewSeesWhatAnotherWriterAppended(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gatelog.jsonl")
	w, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	state := func(title string) string {
		return "PULL REQUEST TITLE: " + title + "\nSUMMARY: 3 files changed, +40 / -2 lines\nDIFF: SECRET-DIFF"
	}
	add := func(pr int, sha, title string, res engine.Result, shadow bool) {
		res.TS = now
		if err := w.Record(store.Record{TS: now.Add(time.Duration(pr) * time.Second), Repo: "o/r", PR: pr, HeadSHA: sha, State: state(title), Result: res, Shadow: shadow}); err != nil {
			t.Fatal(err)
		}
	}
	add(12, "s12", "Add retry to fetch", engine.Result{Verdict: "pass", Route: "auto-merge", Rule: "default"}, false)
	// a reader that started before the next writes: its own indexes do not have them
	early := store.View(path)
	add(13, "s13", "Retry fetch on 5xx", engine.Result{Verdict: "hold", Route: "dedup", Rule: "duplicate-of", Reason: "same change",
		Probs: map[string]float64{"duplicate_of": 0.91}, Extra: map[string]any{"duplicate_candidate": 12}}, true)
	add(14, "s14", "Rewrite scheduler", engine.Result{Verdict: "block", Route: "human", Rule: "weak-tests", Reason: "no tests"}, false)
	add(15, "s15", "Docs", engine.Result{Verdict: "hold", Route: "domain-review", Rule: "x"}, false) // waits for nobody in particular

	if rows := QueueRows(early, ""); len(rows) != 0 {
		t.Fatalf("the early view should not see later writes: %+v", rows)
	}
	rows := QueueRows(store.View(path), "o/r")
	if len(rows) != 2 || rows[0].PR != 13 || rows[1].PR != 14 {
		t.Fatalf("rows: %+v", rows)
	}
	d := rows[0]
	if d.Title != "Retry fetch on 5xx" || !d.Shadow || d.Files != 3 || d.Additions != 40 || d.Duplicate == nil || d.Duplicate.PR != 12 ||
		d.Duplicate.Title != "Add retry to fetch" || d.Duplicate.Probability != 0.91 || d.Duplicate.Verdict != "pass" {
		t.Errorf("duplicate row: %+v %+v", d, d.Duplicate)
	}
	if rows[1].Duplicate != nil || rows[1].Reason != "no tests" {
		t.Errorf("block row: %+v", rows[1])
	}
	b, _ := json.Marshal(rows)
	if strings.Contains(string(b), "SECRET-DIFF") || strings.Contains(string(b), "PULL REQUEST TITLE") {
		t.Fatal("a queue row carries the rendered state")
	}
	if got := QueueRows(store.View(path), "o/other"); len(got) != 0 {
		t.Errorf("other repo: %+v", got)
	}
	if got := QueueRows(store.View(filepath.Join(t.TempDir(), "missing.jsonl")), ""); got == nil || len(got) != 0 {
		t.Errorf("a missing log is an empty list, not nil: %#v", got)
	}
	// a view cannot write
	if err := store.View(path).Record(store.Record{Repo: "o/r", PR: 99, HeadSHA: "x", Result: engine.Result{Verdict: "pass"}}); err == nil {
		t.Error("Record on a view must fail")
	}
}
