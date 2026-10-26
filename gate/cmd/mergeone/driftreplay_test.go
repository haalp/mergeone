package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
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

// R8-H5's slow-signal half: drift-replay --labels reproduces scripts/drift_slow.py (results/run8/drift_slow.json)
// on the committed predictions — the same flags, n and every AUC to the 4 decimals the Python wrote. The Python
// reference reads every request mode, last row per PR (on the run-6A file that is the single-question pass), so
// the replay runs with --mode any. Labels come from data/run3/dataset.jsonl when it is on disk (it is not
// committed), else from the reference prediction rows, which carry the same labels.
func TestDriftReplaySlowMatchesPython(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	r := func(p string) string { return filepath.Join(root, "results", p) }
	b, err := os.ReadFile(r("run8/drift_slow.json"))
	if err != nil {
		t.Skip("results/run8/drift_slow.json not on disk")
	}
	var py struct {
		Comparisons map[string]struct {
			N       int      `json:"n"`
			Flagged []string `json:"flagged"`
			AUC     map[string]struct {
				Ref   float64 `json:"ref_auc"`
				Cur   float64 `json:"cur_auc"`
				Delta float64 `json:"delta"`
			} `json:"auc"`
		} `json:"comparisons"`
	}
	if err := json.Unmarshal(b, &py); err != nil {
		t.Fatal(err)
	}
	cases := map[string][2]string{ // scripts/drift_slow.py CASES
		"pplx-flash_val_run5_to_run7r0": {r("run5/predictions_pplx-flash_val_all.jsonl"),
			r("run7/tune_r0/predictions_pplx-flash_val_all.jsonl") + "," + r("run7/tune_r0_iter/predictions_pplx-flash_val_all.jsonl")},
		"pplx-flash_test_run5_to_run7control":      {r("run5/predictions_pplx-flash_test_all.jsonl"), r("run7/control/predictions_pplx-flash_test_all.jsonl")},
		"jev_test_run5_to_run7control":             {r("run5/predictions_jev_test_all.jsonl"), r("run7/control/predictions_jev_test_all.jsonl")},
		"pplx-flash_test_run5hosted_to_run6Alocal": {r("run5/predictions_pplx-flash_test_all.jsonl"), r("run6/predictions_pplx-flash-local_test_all.jsonl")},
	}
	if len(py.Comparisons) != len(cases) {
		t.Fatalf("drift_slow.json has %d comparisons, the test knows %d", len(py.Comparisons), len(cases))
	}
	dataset := filepath.Join(root, "data", "run3", "dataset.jsonl")
	for name, c := range cases {
		want, ok := py.Comparisons[name]
		if !ok {
			t.Fatalf("%s missing from drift_slow.json", name)
		}
		labels := dataset
		if _, err := os.Stat(dataset); err != nil {
			labels = strings.Split(c[0], ",")[0]
		}
		md := replayJSON(t, "--mode", "any", "--predictions", c[0], "--predictions", c[1], "--labels", labels, "--label", "iterated")
		sd := md.Slow
		if sd == nil || !reflect.DeepEqual(sd.Degraded, want.Flagged) {
			t.Errorf("%s: slow flags %v, Python %v", name, sd, want.Flagged)
			continue
		}
		for q, pa := range want.AUC {
			g := sd.Questions[q]
			if g.N != want.N || g.AUC == nil || g.RefAUC == nil ||
				math.Abs(*g.RefAUC-pa.Ref) > 5.1e-5 || math.Abs(*g.AUC-pa.Cur) > 5.1e-5 {
				t.Errorf("%s %s: go n=%d ref=%v cur=%v, python n=%d %+v", name, q, g.N, g.RefAUC, g.AUC, want.N, pa)
			}
		}
	}
	// The pre-registered flags (R8-H5): jev tests_cover_change; the run-6A row's three questions.
	if f := py.Comparisons["jev_test_run5_to_run7control"].Flagged; !reflect.DeepEqual(f, []string{"tests_cover_change"}) {
		t.Fatalf("jev reference flags %v", f)
	}
}

func TestAUCMatchesRankFormula(t *testing.T) {
	y := []bool{false, false, true, true, false, true}
	p := []float64{0.1, 0.4, 0.35, 0.8, 0.4, 0.4}
	// pairs (pos, neg): 0.35>{0.1}=1, 0.35<0.4 ×2 → 1/3; 0.8 > all 3 → 3; 0.4: >0.1, tie 0.4 ×2 → 1+0.5+0.5 = 2 → 6/9
	if a, ok := engine.AUC(y, p); !ok || math.Abs(a-6.0/9) > 1e-12 {
		t.Fatalf("auc %v", a)
	}
	if _, ok := engine.AUC([]bool{true, true}, []float64{0.1, 0.2}); ok {
		t.Fatal("one class has no AUC")
	}
}
