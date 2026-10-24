package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func writePreds(t *testing.T, path string, rows func(i int) map[string]any, n int) {
	t.Helper()
	var b strings.Builder
	for i := 0; i < n; i++ {
		probs, _ := json.Marshal(rows(i))
		fmt.Fprintf(&b, `{"pr_number": %d, "split": "test", "qset": "v3", "decision": {"probs": %s, "error": null}}`+"\n", i, probs)
	}
	b.WriteString(`{"pr_number": 0, "split": "test", "qset": "pair", "decision": {"probs": {"a": 0.0}, "error": null}}` + "\n")
	b.WriteString(`{"pr_number": 1, "split": "test", "qset": "v3", "decision": {"probs": {"a": NaN}, "error": "429"}}` + "\n")
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func replayJSON(t *testing.T, args ...string) engine.ModelDrift {
	t.Helper()
	var o, e bytes.Buffer
	oldOut, oldErr := stdout, stderr
	t.Cleanup(func() { stdout, stderr = oldOut, oldErr })
	stdout, stderr = &o, &e
	if code := run(append([]string{"drift-replay", "--json"}, args...)); code != 0 {
		t.Fatalf("exit %d: %s", code, e.String())
	}
	var md engine.ModelDrift
	if err := json.Unmarshal(o.Bytes(), &md); err != nil {
		t.Fatal(err, o.String())
	}
	return md
}

func TestDriftReplaySynthetic(t *testing.T) {
	dir := t.TempDir()
	then, now := filepath.Join(dir, "then.jsonl"), filepath.Join(dir, "now.jsonl")
	writePreds(t, then, func(i int) map[string]any { return map[string]any{"a": 0.3, "b": float64(i%2) * 0.8, "c": 0.5} }, 300)
	// a: shift 0.25; b: same mean, spread 0.4 → 0.15; c: shift 0.1; PRs ≥ 250 only on one side.
	writePreds(t, now, func(i int) map[string]any { return map[string]any{"a": 0.55, "b": 0.25 + float64(i%2)*0.3, "c": 0.6} }, 250)
	md := replayJSON(t, "--predictions", then, "--predictions", now)
	if !reflect.DeepEqual(md.Degraded, []string{"a", "b"}) || md.Questions["a"].N != 250 {
		t.Fatalf("flagged %v, %+v", md.Degraded, md.Questions)
	}
	// Fewer than 200 shared PRs never flag.
	writePreds(t, now, func(i int) map[string]any { return map[string]any{"a": 0.9} }, 150)
	if md := replayJSON(t, "--predictions", then, "--predictions", now); len(md.Degraded) != 0 {
		t.Fatalf("n < 200 flagged %v", md.Degraded)
	}
}

// R8-H5's fast-signal half on the committed predictions (results/run8prep/drift.json at 0.20).
func TestDriftReplayRun8Predictions(t *testing.T) {
	root := filepath.Join("..", "..", "..", "results")
	r := func(p string) string { return filepath.Join(root, p) }
	cases := []struct {
		name      string
		then, now string
		want      []string
	}{
		{"pplx-flash val run5→run7", r("run5/predictions_pplx-flash_val_all.jsonl"),
			r("run7/tune_r0/predictions_pplx-flash_val_all.jsonl") + "," + r("run7/tune_r0_iter/predictions_pplx-flash_val_all.jsonl"),
			[]string{"follows_conventions", "message_matches_diff"}},
		{"pplx-flash test run5→run7 control", r("run5/predictions_pplx-flash_test_all.jsonl"), r("run7/control/predictions_pplx-flash_test_all.jsonl"),
			[]string{"follows_conventions", "message_matches_diff"}},
		{"jev test run5→run7 control", r("run5/predictions_jev_test_all.jsonl"), r("run7/control/predictions_jev_test_all.jsonl"), []string{}},
		{"pplx-flash hosted→run6A local", r("run5/predictions_pplx-flash_test_all.jsonl"), r("run6/predictions_pplx-flash-local_test_all.jsonl"), []string{}},
	}
	for _, c := range cases {
		if _, err := os.Stat(c.then); err != nil {
			t.Skip("prediction files not on disk")
		}
		md := replayJSON(t, "--predictions", c.then, "--predictions", c.now)
		if !reflect.DeepEqual(md.Degraded, c.want) {
			t.Errorf("%s: flagged %v, want %v", c.name, md.Degraded, c.want)
		}
	}
}
