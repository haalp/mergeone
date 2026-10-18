package memory

import (
	"encoding/json"
	"fmt"
	"math"
	"math/rand"
	"os"
	"sort"
	"testing"
	"time"
)

type v2Fixture struct {
	Weights map[string]float64 `json:"weights"`
	Window  int                `json:"window_days"`
	Cases   []struct {
		Repo  string `json:"repo"`
		Query struct {
			Number    int      `json:"number"`
			Author    string   `json:"author"`
			CreatedAt string   `json:"created_at"`
			Title     string   `json:"title"`
			Body      string   `json:"body"`
			Paths     []string `json:"paths"`
		} `json:"query"`
		Memory []json.RawMessage `json:"memory"`
		Expect []struct {
			Number int     `json:"number"`
			Score  float64 `json:"score"`
			Issue  float64 `json:"issue"`
			Dir    float64 `json:"dir"`
			File   float64 `json:"file"`
			Text   float64 `json:"text"`
			Author float64 `json:"author"`
			Rank   int     `json:"rank"`
		} `json:"expect"`
	} `json:"cases"`
	Refs []struct {
		Text string   `json:"text"`
		Repo string   `json:"repo"`
		Self int      `json:"self"`
		Refs []string `json:"refs"`
	} `json:"refs"`
	StateParse []struct {
		State string   `json:"state"`
		Title string   `json:"title"`
		Body  string   `json:"body"`
		Paths []string `json:"paths"`
	} `json:"state_parse"`
}

func b2f(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

// TestV2Fixture: Python (mergeone/memory/retrieval_v2.py) and Go agree to 1e-6 on every candidate score and feature.
func TestV2Fixture(t *testing.T) {
	raw, err := os.ReadFile("testdata/v2_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var fx v2Fixture
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	w := V2Weights()
	if fx.Weights["issue"] != w[0] || fx.Weights["dir"] != w[1] || fx.Weights["file"] != w[2] || fx.Weights["text"] != w[3] || fx.Weights["author"] != w[4] || fx.Window != V2WindowDays {
		t.Fatalf("frozen weights/window differ from Python: %v %d vs %v %d", fx.Weights, fx.Window, w, V2WindowDays)
	}
	pairs := 0
	for ci, c := range fx.Cases {
		s, _ := Open("")
		r := s.Repo(c.Repo)
		for _, m := range c.Memory {
			var e Entry
			if err := json.Unmarshal(m, &e); err != nil {
				t.Fatal(err)
			}
			_ = r.Upsert(e)
		}
		ts, err := time.Parse(time.RFC3339, c.Query.CreatedAt)
		if err != nil {
			t.Fatal(err)
		}
		q := NewQueryV2(c.Repo, c.Query.Number, c.Query.Title, c.Query.Body, c.Query.Paths, c.Query.Author, ts)
		for _, impl := range []struct {
			name string
			got  []CandidateV2
		}{{"postings", r.CandidatesV2(q, 1000)}, {"scan", r.candidatesV2Scan(q, 1000)}} {
			got := impl.got
			if len(got) != len(c.Expect) {
				t.Fatalf("case %d (%s) %s: %d candidates, Python %d", ci, c.Repo, impl.name, len(got), len(c.Expect))
			}
			for i, e := range c.Expect {
				g := got[i]
				if g.Number != e.Number || g.Rank != e.Rank {
					t.Fatalf("case %d %s rank %d: Go #%d, Python #%d", ci, impl.name, i+1, g.Number, e.Number)
				}
				for _, d := range [][3]any{{"score", g.Score, e.Score}, {"issue", b2f(g.IssueRef), e.Issue}, {"dir", g.DirJaccard, e.Dir},
					{"file", g.Jaccard, e.File}, {"text", g.BM25, e.Text}, {"author", b2f(g.SameAuthor), e.Author}} {
					if math.Abs(d[1].(float64)-d[2].(float64)) > 1e-6 {
						t.Fatalf("case %d %s #%d %s: Go %.12f Python %.12f", ci, impl.name, g.Number, d[0], d[1], d[2])
					}
				}
				if impl.name == "postings" {
					pairs++
				}
			}
		}
	}
	if pairs < 150 {
		t.Fatalf("fixture too small: %d pairs", pairs)
	}
	for _, rc := range fx.Refs {
		got := []string{}
		for k := range V2Refs(rc.Text, rc.Repo, rc.Self) {
			got = append(got, k)
		}
		sort.Strings(got)
		if fmt.Sprint(got) != fmt.Sprint(rc.Refs) {
			t.Fatalf("refs(%q): Go %v Python %v", rc.Text, got, rc.Refs)
		}
	}
	for _, sp := range fx.StateParse {
		ps := ParseState(sp.State)
		if ps.Title != sp.Title || ps.Body != sp.Body || fmt.Sprint(ps.Paths) != fmt.Sprint(sp.Paths) {
			t.Fatalf("ParseState differs from Python: %q / %v", ps.Title, ps.Paths)
		}
	}
	t.Logf("%d query/candidate pairs agree to 1e-6", pairs)
}

// TestV2PostingsEqualsScan: the postings path returns exactly the reference scan on random memories
// (exercises extra docs merged inside the window, zero-score fill, self exclusion, unknown query terms).
func TestV2PostingsEqualsScan(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	words := []string{"retry", "client", "parser", "cache", "token", "auth", "router", "model", "proxy", "config", "logging", "schema"}
	dirs := []string{"src/a", "src/b", "lib", "docs", "tests/x"}
	base := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	for trial := 0; trial < 30; trial++ {
		s, _ := Open("")
		r := s.Repo("o/r")
		n := 5 + rng.Intn(80)
		for i := 1; i <= n; i++ {
			c := base.AddDate(0, 0, rng.Intn(400)-200)
			e := Entry{Number: i, Title: words[rng.Intn(len(words))] + " " + words[rng.Intn(len(words))], Author: []string{"a", "B", "c[bot]"}[rng.Intn(3)],
				Body: fmt.Sprintf("refs #%d and %s", rng.Intn(10), words[rng.Intn(len(words))]), State: "MERGED", CreatedAt: c.Format(time.RFC3339)}
			if rng.Intn(3) > 0 {
				e.MergedAt = c.AddDate(0, 0, rng.Intn(200)).Format(time.RFC3339)
			}
			for j := rng.Intn(4); j > 0; j-- {
				e.Files = append(e.Files, FileStat{Path: fmt.Sprintf("%s/f%d.go", dirs[rng.Intn(len(dirs))], rng.Intn(5))})
			}
			_ = r.Upsert(e)
		}
		for qi := 0; qi < 10; qi++ {
			q := NewQueryV2("o/r", rng.Intn(n+3), words[rng.Intn(len(words))]+" unknownterm", fmt.Sprintf("see #%d", rng.Intn(10)),
				[]string{fmt.Sprintf("%s/f%d.go", dirs[rng.Intn(len(dirs))], rng.Intn(5))}, "c", base.AddDate(0, 0, rng.Intn(200)-100))
			for _, k := range []int{3, 20} {
				a, b := r.CandidatesV2(q, k), r.candidatesV2Scan(q, k)
				if len(a) != len(b) {
					t.Fatalf("trial %d: postings %d vs scan %d", trial, len(a), len(b))
				}
				for i := range a {
					if a[i] != b[i] {
						t.Fatalf("trial %d rank %d: postings %+v vs scan %+v", trial, i+1, a[i], b[i])
					}
				}
			}
		}
	}
}
