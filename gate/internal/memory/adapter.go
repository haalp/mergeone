package memory

import (
	"sort"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/render"
)

// Candidates implements engine.Memory: retrieval + pair-state rendering for the gate.
// Retrieval v2 (A1, run 5) is the default; Store.RetrievalVersion = 1 selects the run-4 v1 formula.
func (s *Store) Candidates(meta engine.PRMeta, aState string, k int) []engine.Candidate {
	r := s.Repo(meta.Repo)
	if r.Len() == 0 {
		return nil
	}
	if k <= 0 {
		k = TopK // default top_k stays 5 for both versions
	}
	var cs []Candidate
	if s.RetrievalVersion == 1 {
		cs = r.Candidates(meta.Number, meta.Title, meta.Body, meta.Paths, meta.Author, meta.CreatedAt, k)
	} else {
		cs = r.candidatesV2AsV1(meta, k)
	}
	out := make([]engine.Candidate, 0, len(cs))
	for _, c := range cs {
		out = append(out, engine.Candidate{Number: c.Number, Score: c.Score, State: c.State, Title: c.Title, PairState: PairState(aState, c)})
	}
	return out
}

// candidatesV2AsV1 runs A1 and fills the v1 Candidate struct (body cut at 1,500, sorted paths ≤ 60, as v1)
// from the stored Entry, so PairState renders exactly as before.
func (r *Repo) candidatesV2AsV1(meta engine.PRMeta, k int) []Candidate {
	created := meta.CreatedAt
	if created.IsZero() {
		created = time.Now().UTC()
	}
	q := NewQueryV2(r.name, meta.Number, meta.Title, meta.Body, meta.Paths, meta.Author, created)
	hits := r.CandidatesV2(q, k)
	out := make([]Candidate, 0, len(hits))
	for _, h := range hits {
		e, ok := r.Get(h.Number)
		if !ok {
			continue
		}
		paths := make([]string, 0, len(e.Files))
		for _, f := range e.Files {
			paths = append(paths, f.Path)
		}
		sort.Strings(paths)
		if len(paths) > 60 {
			paths = paths[:60]
		}
		b := render.Chars(e.Body, 1500)
		out = append(out, Candidate{Number: h.Number, Score: round4(h.Score), Jaccard: round4(h.Jaccard), BM25: round4(h.BM25), SameAuthor: h.SameAuthor,
			State: e.State, Title: e.Title, Body: b, Author: e.Author, CreatedAt: e.CreatedAt, MergedAt: e.MergedAt, Files: paths})
	}
	return out
}

// EntryFromPR builds a memory entry from the fields the gateway already fetches for a decision.
func EntryFromPR(meta engine.PRMeta, state string, files []FileStat) Entry {
	e := Entry{Number: meta.Number, Title: meta.Title, Body: meta.Body, Author: meta.Author, State: state, Files: files}
	if !meta.CreatedAt.IsZero() {
		e.CreatedAt = meta.CreatedAt.UTC().Format("2006-01-02T15:04:05Z")
	}
	if !meta.UpdatedAt.IsZero() {
		e.UpdatedAt = meta.UpdatedAt.UTC().Format("2006-01-02T15:04:05Z")
	}
	return e
}
