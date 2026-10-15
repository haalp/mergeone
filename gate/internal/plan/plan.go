// Package plan builds landing plans: waves of non-conflicting open PRs.
package plan

import (
	"fmt"
	"path"
	"sort"
	"strings"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// Edge is one conflict between two PRs.
type Edge struct {
	A        int     `json:"a"`
	B        int     `json:"b"`
	Kind     string  `json:"kind"` // file | semantic
	P        float64 `json:"p,omitempty"`
	Evidence string  `json:"evidence"`
}

// Plan is the output of Landing.
type Plan struct {
	Repo          string  `json:"repo"`
	OpenPRs       int     `json:"open_prs"`
	Waves         [][]int `json:"waves"`
	Parallelism   int     `json:"parallelism"`
	ConflictEdges []Edge  `json:"conflict_edges"`
	SemanticAsked int     `json:"semantic_pairs_asked"`
	Advice        string  `json:"advice"`
}

// SemanticQuestion is the one typed question asked per candidate pair.
const SemanticQuestion = "same_behaviour"

// Landing computes waves. model may be nil (mechanical conflicts only).
func Landing(repo string, prs []store.OpenPR, model engine.Model, maxPairs int, threshold float64) Plan {
	sort.Slice(prs, func(i, j int) bool { return prs[i].Updated.Before(prs[j].Updated) })
	conf := map[int]map[int]bool{}
	add := func(a, b int) {
		if conf[a] == nil {
			conf[a] = map[int]bool{}
		}
		if conf[b] == nil {
			conf[b] = map[int]bool{}
		}
		conf[a][b], conf[b][a] = true, true
	}
	var edges []Edge
	owners := map[string][]int{}
	for _, p := range prs {
		for _, f := range p.Paths {
			owners[f] = append(owners[f], p.PR)
		}
	}
	files := make([]string, 0, len(owners))
	for f := range owners {
		files = append(files, f)
	}
	sort.Strings(files)
	for _, f := range files {
		o := owners[f]
		for i := 0; i < len(o); i++ {
			for j := i + 1; j < len(o); j++ {
				a, b := o[i], o[j]
				if a > b {
					a, b = b, a
				}
				if !conf[a][b] {
					add(a, b)
					edges = append(edges, Edge{A: a, B: b, Kind: "file", Evidence: f})
				}
			}
		}
	}
	asked := 0
	if model != nil {
		byPR := map[int]store.OpenPR{}
		for _, p := range prs {
			byPR[p.PR] = p
		}
		for i := 0; i < len(prs) && asked < maxPairs; i++ {
			for j := i + 1; j < len(prs) && asked < maxPairs; j++ {
				a, b := prs[i].PR, prs[j].PR
				if conf[a][b] {
					continue
				}
				shared := sharedDirs(prs[i].Paths, prs[j].Paths)
				if len(shared) == 0 {
					continue
				}
				state := fmt.Sprintf("PR A #%d changes files:\n  %s\n\nPR B #%d changes files:\n  %s", a, strings.Join(head(prs[i].Paths, 40), "\n  "), b, strings.Join(head(prs[j].Paths, 40), "\n  "))
				d := model.Decide(state, []string{SemanticQuestion})
				asked++
				if p, ok := d.Probs[SemanticQuestion]; ok && d.Error == "" && p >= threshold {
					add(a, b)
					edges = append(edges, Edge{A: a, B: b, Kind: "semantic", P: p, Evidence: strings.Join(head(shared, 3), ", ")})
				}
			}
		}
	}
	var waves [][]int
	for _, p := range prs {
		placed := false
		for wi := range waves {
			ok := true
			for _, q := range waves[wi] {
				if conf[p.PR][q] {
					ok = false
					break
				}
			}
			if ok {
				waves[wi] = append(waves[wi], p.PR)
				placed = true
				break
			}
		}
		if !placed {
			waves = append(waves, []int{p.PR})
		}
	}
	par := 0
	for _, w := range waves {
		par = max(par, len(w))
	}
	if waves == nil {
		waves = [][]int{}
	}
	if edges == nil {
		edges = []Edge{}
	}
	return Plan{Repo: repo, OpenPRs: len(prs), Waves: waves, Parallelism: par, ConflictEdges: edges, SemanticAsked: asked,
		Advice: "Land each wave in parallel; wait for CI between waves. PRs sharing an edge must be rebased after the earlier one lands."}
}

func sharedDirs(a, b []string) []string {
	da := map[string]bool{}
	for _, p := range a {
		da[path.Dir(p)] = true
	}
	seen := map[string]bool{}
	var out []string
	for _, p := range b {
		d := path.Dir(p)
		if da[d] && !seen[d] {
			seen[d] = true
			out = append(out, d)
		}
	}
	sort.Strings(out)
	return out
}

func head(s []string, n int) []string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
