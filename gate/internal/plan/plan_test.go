package plan

import (
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/store"
)

func TestWaves(t *testing.T) {
	now := time.Now()
	prs := []store.OpenPR{{PR: 1, Paths: []string{"x.go"}, Updated: now}, {PR: 2, Paths: []string{"x.go"}, Updated: now.Add(time.Second)}, {PR: 3, Paths: []string{"y.go"}, Updated: now.Add(2 * time.Second)}}
	p := Landing("r", prs, nil, 0, 0.6)
	if len(p.Waves) != 2 || len(p.Waves[0]) != 2 || p.Waves[1][0] != 2 || p.ConflictEdges[0].Kind != "file" {
		t.Fatalf("got %+v", p)
	}
}
