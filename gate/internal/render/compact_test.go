package render

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// Fixture is gate/testdata/compact_fixture.json, written by gate/testdata/gen_compact_fixture.py from the
// Python compact_state / compact_pair_state on three run-3 PRs.
type Fixture struct {
	Source string `json:"source"`
	Cases  []struct {
		Row               int    `json:"row"`
		AState            string `json:"a_state"`
		AStateWords       string `json:"a_state_words"`
		PairState         string `json:"pair_state"`
		CompactState      string `json:"compact_state"`
		CompactStateWords string `json:"compact_state_words"`
		CompactPairState  string `json:"compact_pair_state"`
		B                 struct {
			Title     string   `json:"title"`
			Body      string   `json:"body"`
			State     string   `json:"state"`
			Author    string   `json:"author"`
			CreatedAt string   `json:"created_at"`
			MergedAt  string   `json:"merged_at"`
			Files     []string `json:"files"`
		} `json:"b"`
	} `json:"cases"`
}

// LoadFixture reads the golden fixture (shared with the memory package's end-to-end test).
func LoadFixture(t *testing.T, root string) Fixture {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(root, "testdata", "compact_fixture.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f Fixture
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Cases) != 3 {
		t.Fatalf("fixture has %d cases, want 3", len(f.Cases))
	}
	return f
}

func firstDiff(a, b string) string {
	n := min(len(a), len(b))
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			lo := max(0, i-40)
			return "byte " + itoa(i) + ": go …" + a[lo:min(len(a), i+40)] + "… python …" + b[lo:min(len(b), i+40)] + "…"
		}
	}
	return "length go " + itoa(len(a)) + " python " + itoa(len(b))
}

func itoa(i int) string { return strconv.Itoa(i) }

// The golden test: the Go port reproduces the Python output byte for byte.
func TestCompactGolden(t *testing.T) {
	f := LoadFixture(t, filepath.Join("..", ".."))
	for _, c := range f.Cases {
		got, err := CompactState(c.AState)
		if err != nil || got != c.CompactState {
			t.Errorf("row %d compact_state: err %v; %s", c.Row, err, firstDiff(got, c.CompactState))
		}
		got, err = CompactState(c.AStateWords)
		if err != nil || got != c.CompactStateWords {
			t.Errorf("row %d compact_state (words): err %v; %s", c.Row, err, firstDiff(got, c.CompactStateWords))
		}
		got, err = CompactPairState(c.PairState, c.AState)
		if err != nil || got != c.CompactPairState {
			t.Errorf("row %d compact_pair_state: err %v; %s", c.Row, err, firstDiff(got, c.CompactPairState))
		}
	}
}

func TestCompactRejectsOtherText(t *testing.T) {
	if _, err := CompactState("PULL REQUEST TITLE: x\nMERGE MESSAGE BODY:\nb\n\nFILES CHANGED:\n  a.go\n"); err == nil {
		t.Fatal("a state without SUMMARY/DIFF must be refused, as Python raises ValueError")
	}
	if _, err := CompactPairState("hello", ""); err == nil {
		t.Fatal("not a pair state")
	}
}

func TestChars(t *testing.T) {
	if Chars("héllo", 2) != "hé" || Chars("abc", 5) != "abc" || Chars("ab", 2) != "ab" || CharLen("hé") != 2 {
		t.Fatal("character cuts")
	}
}
