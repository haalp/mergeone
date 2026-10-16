package memory

import (
	"github.com/haalp/mergeone/gate/internal/engine"
)

// Candidates implements engine.Memory: retrieval + pair-state rendering for the gate.
func (s *Store) Candidates(meta engine.PRMeta, aState string, k int) []engine.Candidate {
	r := s.Repo(meta.Repo)
	if r.Len() == 0 {
		return nil
	}
	cs := r.Candidates(meta.Number, meta.Title, meta.Body, meta.Paths, meta.Author, meta.CreatedAt, k)
	out := make([]engine.Candidate, 0, len(cs))
	for _, c := range cs {
		out = append(out, engine.Candidate{Number: c.Number, Score: c.Score, State: c.State, Title: c.Title, PairState: PairState(aState, c)})
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
