package engine

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
)

// IsotonicFit + the 101-point grid reproduce sklearn's IsotonicRegression (gate/testdata/isotonic_fixture.json).
func TestIsotonicMatchesSklearn(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "testdata", "isotonic_fixture.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []struct {
			Seed  int       `json:"seed"`
			X     []float64 `json:"x"`
			Y     []int     `json:"y"`
			GridY []float64 `json:"grid_y"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		var l Labelled
		for i, x := range c.X {
			l.Add(c.Y[i] == 1, x)
		}
		m, ok := FitQuestionMap(l)
		if !ok || len(m.Y) != 101 || m.N != len(c.X) {
			t.Fatalf("seed %d: fit %v n %d", c.Seed, ok, m.N)
		}
		for i, want := range c.GridY {
			if math.Abs(m.Y[i]-want) > 1e-9 {
				t.Fatalf("seed %d grid %d (x=%.2f): go %.12f sklearn %.12f", c.Seed, i, m.X[i], m.Y[i], want)
			}
		}
	}
	if _, ok := FitQuestionMap(Labelled{Y: []bool{true, true}, P: []float64{0.1, 0.2}}); ok {
		t.Fatal("one class must be skipped")
	}
}
