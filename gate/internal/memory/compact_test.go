package memory

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/haalp/mergeone/gate/internal/render"
)

// End to end on the golden fixture (gate/testdata/compact_fixture.json, from the Python reference):
// PairState renders the same full pair as run4.pair_state(A, _render_b(B)), and the compact pair the gateway
// sends by default (memory.pair_state: compact) equals features.compact_pair_state on it.
func TestPairStateAndCompactMatchPython(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "testdata", "compact_fixture.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []struct {
			Row              int    `json:"row"`
			AState           string `json:"a_state"`
			PairState        string `json:"pair_state"`
			CompactPairState string `json:"compact_pair_state"`
			B                struct {
				Title, Body, State, Author string
				CreatedAt                  string   `json:"created_at"`
				MergedAt                   string   `json:"merged_at"`
				Files                      []string `json:"files"`
			} `json:"b"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		cand := Candidate{Title: c.B.Title, Body: c.B.Body, State: c.B.State, Author: c.B.Author, CreatedAt: c.B.CreatedAt,
			MergedAt: c.B.MergedAt, Files: c.B.Files}
		full := PairState(c.AState, cand)
		if full != c.PairState {
			t.Errorf("row %d: PairState differs from run4.pair_state", c.Row)
		}
		got, err := render.CompactPairState(full, c.AState)
		if err != nil || got != c.CompactPairState {
			t.Errorf("row %d: compact pair differs from features.compact_pair_state (err %v)", c.Row, err)
		}
	}
}
