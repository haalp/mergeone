package engine

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// recorder answers 0.5 to every question and keeps the list it was asked.
type recorder struct{ asked []string }

func (r *recorder) Name() string { return "rec" }
func (r *recorder) Decide(state string, qs []string) Decision {
	r.asked = append([]string(nil), qs...)
	p := map[string]float64{}
	for _, q := range qs {
		p[q] = 0.5
	}
	return Decision{Probs: p}
}

func sorted(xs []string) []string {
	out := append([]string(nil), xs...)
	sort.Strings(out)
	return out
}

// QuestionsV4 is exactly the question set of prompts/jev_v4.json (the wording the model is sent).
func TestQuestionsV4MatchPrompt(t *testing.T) {
	b, err := os.ReadFile("../../../prompts/jev_v4.json")
	if err != nil {
		t.Fatal(err)
	}
	var qs map[string]json.RawMessage
	if err := json.Unmarshal(b, &qs); err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(qs))
	for k := range qs {
		keys = append(keys, k)
	}
	if len(QuestionsV4) != 8 || strings.Join(sorted(keys), ",") != strings.Join(sorted(QuestionsV4), ",") {
		t.Fatalf("QuestionsV4 %v != prompts/jev_v4.json %v", QuestionsV4, keys)
	}
}

// question_set: v4 asks the eight v4 questions and applies calibration_<model>_v4.json (maps + fit_stats).
func TestQuestionSetV4(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "p.yaml")
	os.WriteFile(pp, []byte("model: jev\nquestion_set: v4\n"), 0o644)
	p, err := LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range []string{"jev", "pplx-flash", "haiku"} {
		cal, err := LoadCalibration(filepath.Join("../../../mergeone/gate", "calibration_"+m+"_"+p.QuestionSet+".json"))
		if err != nil || cal.Path == "" {
			t.Fatalf("%s: %v (path %q)", m, err, cal.Path)
		}
		if len(cal.maps) != 8 || len(cal.FitStats) != 8 {
			t.Fatalf("%s: %d maps, %d fit_stats; want 8", m, len(cal.maps), len(cal.FitStats))
		}
		for _, q := range QuestionsV4 {
			if _, ok := cal.maps[q]; !ok {
				t.Fatalf("%s: no map for %s", m, q)
			}
			if cal.FitStats[q].N == 0 {
				t.Fatalf("%s: fit_stats %s has n = 0", m, q)
			}
		}
		rec := &recorder{}
		g := &Gate{Policy: p, Model: rec, Cal: cal}
		r := g.Decide("state", nil)
		if strings.Join(rec.asked, ",") != strings.Join(QuestionsV4, ",") {
			t.Fatalf("%s: asked %v", m, rec.asked)
		}
		if len(r.Probs) != 8 || r.Probs["needs_human_review"] == 0.5 {
			t.Fatalf("%s: probs not calibrated: %v", m, r.Probs)
		}
	}
}

// The default question set stays v2.
func TestQuestionSetDefaultV2(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "p.yaml")
	os.WriteFile(pp, []byte("model: jev\n"), 0o644)
	p, err := LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	rec := &recorder{}
	(&Gate{Policy: p, Model: rec, Cal: &Calibrator{}}).Decide("state", nil)
	if p.QuestionSet != "v2" || strings.Join(rec.asked, ",") != strings.Join(QuestionsV2, ",") {
		t.Fatalf("set %q asked %v", p.QuestionSet, rec.asked)
	}
}

// QuestionsV3 is exactly prompts/jev_v3.json, in the order of mergeone/models/base.py DECISIONS_V3 (v2 + five).
func TestQuestionsV3MatchPrompt(t *testing.T) {
	b, err := os.ReadFile("../../../prompts/jev_v3.json")
	if err != nil {
		t.Fatal(err)
	}
	var qs map[string]json.RawMessage
	if err := json.Unmarshal(b, &qs); err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(qs))
	for k := range qs {
		keys = append(keys, k)
	}
	if len(QuestionsV3) != 10 || strings.Join(sorted(keys), ",") != strings.Join(sorted(QuestionsV3), ",") {
		t.Fatalf("QuestionsV3 %v != prompts/jev_v3.json %v", QuestionsV3, keys)
	}
	if strings.Join(QuestionsV3[:5], ",") != strings.Join(QuestionsV2, ",") {
		t.Fatalf("QuestionsV3 must start with the v2 questions: %v", QuestionsV3)
	}
	for set, n := range map[string]int{"": 5, "v2": 5, "v3": 10, "v4": 8} {
		if len(Questions(set)) != n {
			t.Fatalf("Questions(%q) = %d questions, want %d", set, len(Questions(set)), n)
		}
	}
}

// CheckQuestionSet: v3 needs an existing map; v2 and v4 never refuse here.
func TestCheckQuestionSet(t *testing.T) {
	dir := t.TempDir()
	m := filepath.Join(dir, "calibration_jev_v3.json")
	for _, c := range []struct {
		set, path string
		ok        bool
	}{{"v2", "", true}, {"v4", "", true}, {"v3", "", false}, {"v3", m, false}, {"v3", dir, false}} {
		if err := CheckQuestionSet(c.set, c.path); (err == nil) != c.ok {
			t.Fatalf("%s %q: err %v", c.set, c.path, err)
		}
	}
	os.WriteFile(m, []byte(`{}`), 0o644)
	if err := CheckQuestionSet("v3", m); err != nil {
		t.Fatal(err)
	}
	if err := CheckQuestionSet("v3", ""); err == nil || !strings.Contains(err.Error(), "no calibration map for v3; use v2 or v4") {
		t.Fatalf("message: %v", err)
	}
}

// memory.top_k defaults to 10 when the key is absent (v0.3, run 6B); an explicit value is kept.
func TestTopKDefault(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "p.yaml")
	for yml, want := range map[string]int{"memory: {enabled: true}\n": 10, "": 10, "memory: {enabled: true, top_k: 5}\n": 5} {
		os.WriteFile(pp, []byte(yml), 0o644)
		p, err := LoadPolicy(pp)
		if err != nil {
			t.Fatal(err)
		}
		if p.Memory.TopK != want {
			t.Fatalf("%q: top_k %d, want %d", yml, p.Memory.TopK, want)
		}
	}
}
