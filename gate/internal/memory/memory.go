// Package memory is the per-repository PR memory: recent pull requests and
// deterministic candidate retrieval (PROTOCOL run-4 addendum, R4.3-1).
//
// Scoring is byte-for-byte the formula in mergeone/memory/retrieval.py:
//
//	score = 0.55·jaccard(files) + 0.35·bm25/bm25_max + 0.10·[same author]
//
// restricted to PRs created or merged within ±45 days of the query. The memory
// is an append-only JSONL file per repository under <dir>/<owner>__<name>.jsonl,
// loaded into RAM on open; a repo with 20k PRs costs ~30 MB and answers in <5 ms.
package memory

import (
	"bufio"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	WindowDays = 45
	TopK       = 5
	wFiles     = 0.55
	wText      = 0.35
	wAuthor    = 0.10
)

// Entry is one remembered pull request. Field names match the Python fetcher.
type Entry struct {
	Repo      string     `json:"repo"`
	Number    int        `json:"number"`
	Title     string     `json:"title"`
	Body      string     `json:"body"`
	Author    string     `json:"author"`
	State     string     `json:"state"` // OPEN | MERGED | CLOSED
	CreatedAt string     `json:"created_at"`
	MergedAt  string     `json:"merged_at,omitempty"`
	ClosedAt  string     `json:"closed_at,omitempty"`
	UpdatedAt string     `json:"updated_at,omitempty"`
	Base      string     `json:"base,omitempty"`
	HeadRef   string     `json:"head_ref,omitempty"`
	Files     []FileStat `json:"files"`

	created time.Time
	merged  time.Time
	fileSet map[string]struct{}
	toks    map[string]int
	dl      int
}

type FileStat struct {
	Path       string `json:"path"`
	Insertions int    `json:"insertions"`
	Deletions  int    `json:"deletions"`
}

// Candidate is a retrieval hit.
type Candidate struct {
	Number     int      `json:"number"`
	Score      float64  `json:"score"`
	Jaccard    float64  `json:"jaccard"`
	BM25       float64  `json:"bm25"`
	SameAuthor bool     `json:"same_author"`
	State      string   `json:"state"`
	Title      string   `json:"title"`
	Body       string   `json:"-"`
	Author     string   `json:"author"`
	CreatedAt  string   `json:"created_at"`
	MergedAt   string   `json:"merged_at,omitempty"`
	Files      []string `json:"-"`
}

var tokenRe = regexp.MustCompile(`[a-z0-9_]{3,}`)
var stop = map[string]struct{}{}

func init() {
	for _, w := range strings.Fields("the and for with that this from into are was were have has not but use using add fix fixes fixed update updates updated remove removes change changes changed support feature bug issue pull request when then than also") {
		stop[w] = struct{}{}
	}
}

func tokens(s string) map[string]int {
	out := map[string]int{}
	for _, t := range tokenRe.FindAllString(strings.ToLower(s), -1) {
		if _, skip := stop[t]; !skip {
			out[t]++
		}
	}
	return out
}

func parseTS(s string) time.Time {
	if s == "" {
		return time.Time{}
	}
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		t, _ = time.Parse("2006-01-02T15:04:05.999999-07:00", s)
	}
	return t
}

// Repo is one repository's memory.
type Repo struct {
	mu     sync.RWMutex
	name   string
	path   string
	prs    map[int]*Entry
	df     map[string]int
	avgLen float64

	v2   *v2Index   // retrieval v2 (A1) index, built lazily; reset on Upsert
	v2mu sync.Mutex // serialises CandidatesV2 (window stamp array)
}

func (e *Entry) prep() {
	e.created = parseTS(e.CreatedAt)
	e.merged = parseTS(e.MergedAt)
	e.fileSet = make(map[string]struct{}, len(e.Files))
	for _, f := range e.Files {
		e.fileSet[f.Path] = struct{}{}
	}
	body := e.Body
	if len(body) > 2000 {
		body = body[:2000]
	}
	e.toks = tokens(e.Title + " " + e.Title + " " + body)
	e.dl = 0
	for _, c := range e.toks {
		e.dl += c
	}
}

func (r *Repo) reindex() {
	r.df = map[string]int{}
	total := 0
	for _, p := range r.prs {
		for t := range p.toks {
			r.df[t]++
		}
		total += p.dl
	}
	if len(r.prs) > 0 {
		r.avgLen = math.Max(1, float64(total)/float64(len(r.prs)))
	} else {
		r.avgLen = 1
	}
}

// Upsert adds or replaces an entry and persists it (append-only; the latest line wins on load).
func (r *Repo) Upsert(e Entry) error {
	e.Repo = r.name
	e.prep()
	r.mu.Lock()
	r.prs[e.Number] = &e
	r.reindex()
	r.v2 = nil
	r.mu.Unlock()
	if r.path == "" {
		return nil
	}
	f, err := os.OpenFile(r.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	b, _ := json.Marshal(e)
	_, err = f.Write(append(b, '\n'))
	return err
}

func (r *Repo) Len() int { r.mu.RLock(); defer r.mu.RUnlock(); return len(r.prs) }

func (r *Repo) Get(n int) (Entry, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	e, ok := r.prs[n]
	if !ok {
		return Entry{}, false
	}
	return *e, true
}

func (r *Repo) bm25(q, d map[string]int, dl int) float64 {
	const k1, b = 1.2, 0.75
	n := float64(len(r.prs))
	if n < 1 {
		n = 1
	}
	s := 0.0
	for t := range q {
		tf, ok := d[t]
		if !ok {
			continue
		}
		df := float64(r.df[t])
		idf := math.Log(1 + (n-df+0.5)/(df+0.5))
		ftf := float64(tf)
		s += idf * (ftf * (k1 + 1)) / (ftf + k1*(1-b+b*float64(dl)/r.avgLen))
	}
	return s
}

// Candidates returns the top-k memory PRs for a query that need not be in memory.
func (r *Repo) Candidates(number int, title, body string, files []string, author string, createdAt time.Time, k int) []Candidate {
	if k <= 0 {
		k = TopK
	}
	if createdAt.IsZero() {
		createdAt = time.Now().UTC()
	}
	lo, hi := createdAt.AddDate(0, 0, -WindowDays), createdAt.AddDate(0, 0, WindowDays)
	qf := make(map[string]struct{}, len(files))
	for _, f := range files {
		qf[f] = struct{}{}
	}
	if len(body) > 2000 {
		body = body[:2000]
	}
	qt := tokens(title + " " + title + " " + body)
	type scored struct {
		jac, bm float64
		e       *Entry
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	var sc []scored
	bmMax := 0.0
	for _, c := range r.prs {
		if c.Number == number {
			continue
		}
		anchor := c.merged
		if anchor.IsZero() {
			anchor = c.created
		}
		inWin := (!c.created.Before(lo) && !c.created.After(hi)) || (!anchor.IsZero() && !anchor.Before(lo) && !anchor.After(hi))
		if !inWin {
			continue
		}
		inter, union := 0, len(c.fileSet)
		for f := range qf {
			if _, ok := c.fileSet[f]; ok {
				inter++
			} else {
				union++
			}
		}
		jac := 0.0
		if union > 0 {
			jac = float64(inter) / float64(union)
		}
		bm := r.bm25(qt, c.toks, c.dl)
		if bm > bmMax {
			bmMax = bm
		}
		sc = append(sc, scored{jac, bm, c})
	}
	if bmMax == 0 {
		bmMax = 1
	}
	out := make([]Candidate, 0, len(sc))
	for _, s := range sc {
		same := author != "" && s.e.Author == author
		score := wFiles*s.jac + wText*(s.bm/bmMax)
		if same {
			score += wAuthor
		}
		paths := make([]string, 0, len(s.e.Files))
		for _, f := range s.e.Files {
			paths = append(paths, f.Path)
		}
		sort.Strings(paths)
		if len(paths) > 60 {
			paths = paths[:60]
		}
		b := s.e.Body
		if len(b) > 1500 {
			b = b[:1500]
		}
		out = append(out, Candidate{Number: s.e.Number, Score: round4(score), Jaccard: round4(s.jac), BM25: round4(s.bm / bmMax), SameAuthor: same,
			State: s.e.State, Title: s.e.Title, Body: b, Author: s.e.Author, CreatedAt: s.e.CreatedAt, MergedAt: s.e.MergedAt, Files: paths})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Score > out[j].Score })
	if len(out) > k {
		out = out[:k]
	}
	return out
}

func round4(x float64) float64 { return math.Round(x*1e4) / 1e4 }

// Store holds every repository's memory under one directory.
type Store struct {
	mu    sync.Mutex
	dir   string
	repos map[string]*Repo
}

// Open loads <dir>/*.jsonl. A missing dir is created; an empty store is valid.
func Open(dir string) (*Store, error) {
	if dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	s := &Store{dir: dir, repos: map[string]*Repo{}}
	if dir == "" {
		return s, nil
	}
	files, _ := filepath.Glob(filepath.Join(dir, "*.jsonl"))
	for _, f := range files {
		name := strings.ReplaceAll(strings.TrimSuffix(filepath.Base(f), ".jsonl"), "__", "/")
		r := &Repo{name: name, path: f, prs: map[int]*Entry{}}
		fh, err := os.Open(f)
		if err != nil {
			return nil, err
		}
		sc := bufio.NewScanner(fh)
		sc.Buffer(make([]byte, 1<<20), 64<<20)
		for sc.Scan() {
			var e Entry
			if json.Unmarshal(sc.Bytes(), &e) == nil && e.Number > 0 {
				e.prep()
				ee := e
				r.prs[e.Number] = &ee
			}
		}
		fh.Close()
		r.reindex()
		s.repos[name] = r
	}
	return s, nil
}

// Repo returns (creating if needed) one repository's memory.
func (s *Store) Repo(name string) *Repo {
	s.mu.Lock()
	defer s.mu.Unlock()
	if r, ok := s.repos[name]; ok {
		return r
	}
	path := ""
	if s.dir != "" {
		path = filepath.Join(s.dir, strings.ReplaceAll(name, "/", "__")+".jsonl")
	}
	r := &Repo{name: name, path: path, prs: map[int]*Entry{}}
	r.reindex()
	s.repos[name] = r
	return r
}

// Sizes reports entries per repository.
func (s *Store) Sizes() map[string]int {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := map[string]int{}
	for k, r := range s.repos {
		out[k] = r.Len()
	}
	return out
}

// PairState renders the two-sided input for the same_change question. Byte-equivalent to
// mergeone/eval/run4.py pair_state() + _render_b().
func PairState(aState string, c Candidate) string {
	if len(aState) > 6000 {
		aState = aState[:6000]
	}
	var sb strings.Builder
	sb.WriteString("PULL REQUEST TITLE: " + c.Title + "\n")
	sb.WriteString("MERGE MESSAGE BODY:\n")
	body := strings.TrimSpace(c.Body)
	if len(body) > 1200 {
		body = body[:1200]
	}
	sb.WriteString(body + "\n\n")
	st := "STATE: " + strings.ToLower(c.State)
	if c.MergedAt != "" {
		st += ", merged " + c.MergedAt[:10]
	} else if len(c.CreatedAt) >= 10 {
		st += ", opened " + c.CreatedAt[:10]
	}
	sb.WriteString(st + "\n")
	sb.WriteString("AUTHOR: " + c.Author + "\n\nFILES CHANGED:\n")
	if len(c.Files) == 0 {
		sb.WriteString("  (file list unavailable)\n")
	}
	for i, p := range c.Files {
		if i >= 60 {
			break
		}
		sb.WriteString("  " + p + "\n")
	}
	b := strings.TrimRight(sb.String(), "\n")
	if len(b) > 3000 {
		b = b[:3000]
	}
	return "=== PULL REQUEST A ===\n" + aState + "\n\n=== PULL REQUEST B ===\n" + b + "\n\nHow does A relate to B?"
}
