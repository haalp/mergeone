package memory

// Retrieval v2, stage A1 (PROTOCOL run-5 addendum, R5.1-A1). The run-4 retrieval (Candidates) is unchanged.
//
// Scores are the formula in mergeone/memory/retrieval_v2.py, computed with the same operation order so that
// Python and Go agree to 1e-6 on every candidate score (asserted by TestV2Fixture):
//
//	score = wIssue·[shared issue/PR ref] + wDir·jaccard(dirs, depth 2) + wFile·jaccard(files)
//	      + wText·bm25/bm25_max + wAuthor·[same author]
//
// over memory PRs created or merged within ±90 days of the query, top-20 by score desc, then number asc.
//
// Index: per repository, entries are interned into sorted term / file / dir / ref id lists (term ids are
// assigned in byte order of the term, so a merge join visits query terms in the same sorted order the Python
// BM25 sum uses), and two time-sorted arrays (created, anchor) prune the ±90-day window by binary search.

import (
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// Frozen A1 weights — must equal mergeone/memory/retrieval_v2.py (fit on val gold pairs, 2026-10-17T23:18:08Z).
const (
	V2WindowDays = 90
	V2TopK       = 20
	v2WIssue     = 0.5
	v2WDir       = 0.1
	v2WFile      = 0.2
	v2WText      = 0.2
	v2WAuthor    = 0.0
	v2BodyChars  = 2000
)

// V2Weights returns the frozen weights in feature order (issue, dir, file, text, author).
func V2Weights() [5]float64 { return [5]float64{v2WIssue, v2WDir, v2WFile, v2WText, v2WAuthor} }

var (
	refHash = regexp.MustCompile(`(?:^|[^\w/.\-])#(\d{1,9})\b`)
	refGH   = regexp.MustCompile(`(?:^|[^\w/.\-])[Gg][Hh]-(\d{1,9})\b`)
	refRepo = regexp.MustCompile(`(?:^|[^\w/.\-])([\w.\-]+/[\w.\-]+)#(\d{1,9})\b`)
	// ASCII-only case-insensitivity, as Python re.ASCII|re.IGNORECASE (Go's (?i) would also fold ſ and K).
	refURL = regexp.MustCompile(`[Hh][Tt][Tt][Pp][Ss]?://(?:[Ww][Ww][Ww]\.)?[Gg][Ii][Tt][Hh][Uu][Bb]\.[Cc][Oo][Mm]/([\w.\-]+/[\w.\-]+)/(?:[Ii][Ss][Ss][Uu][Ee][Ss]|[Pp][Uu][Ll][Ll]|[Pp][Uu][Ll][Ll][Ss])/(\d{1,9})\b`)
)

// pyLower lower-cases like Python's str.lower() for every rune whose lower case is ASCII (U+0130 → "i̇").
func pyLower(s string) string {
	ascii := true
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			ascii = false
			break
		}
	}
	if ascii {
		return strings.ToLower(s)
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		if r == 0x130 {
			b.WriteString("i̇")
			continue
		}
		b.WriteRune(unicode.ToLower(r))
	}
	return b.String()
}

// truncRunes is Python's s[:n] (code points).
func truncRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	i := 0
	for j := range s {
		if i == n {
			return s[:j]
		}
		i++
	}
	return s
}

func pyIsSpace(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }

func pyStrip(s string) string { return strings.TrimFunc(s, pyIsSpace) }

// V2Tokens is the v1 tokeniser applied to title twice + body[:2000] (code points).
// Equivalent to tokenRe ([a-z0-9_]{3,}, leftmost-first) on the lower-cased text: maximal runs of the class.
func V2Tokens(title, body string) map[string]int {
	s := pyLower(title + " " + title + " " + truncRunes(body, v2BodyChars))
	out := map[string]int{}
	start := -1
	for i := 0; i <= len(s); i++ {
		in := i < len(s) && (s[i] >= 'a' && s[i] <= 'z' || s[i] >= '0' && s[i] <= '9' || s[i] == '_')
		if in {
			if start < 0 {
				start = i
			}
			continue
		}
		if start >= 0 && i-start >= 3 {
			t := s[start:i]
			if _, skip := stop[t]; !skip {
				out[t]++
			}
		}
		start = -1
	}
	return out
}

// V2Refs returns issue/PR references as "owner/repo#N" (lower-cased repo), excluding the PR's own number.
func V2Refs(text, repo string, self int) map[string]struct{} {
	me := strings.ToLower(repo)
	out := map[string]struct{}{}
	add := func(r, n string) {
		v, err := strconv.Atoi(n)
		if err != nil {
			return
		}
		out[r+"#"+strconv.Itoa(v)] = struct{}{}
	}
	hasHash := strings.IndexByte(text, '#') >= 0
	if hasHash {
		for _, m := range refHash.FindAllStringSubmatch(text, -1) {
			add(me, m[1])
		}
		for _, m := range refRepo.FindAllStringSubmatch(text, -1) {
			add(pyLower(m[1]), m[2])
		}
	}
	if strings.IndexByte(text, '-') >= 0 {
		for _, m := range refGH.FindAllStringSubmatch(text, -1) {
			add(me, m[1])
		}
	}
	if strings.Contains(text, "://") {
		for _, m := range refURL.FindAllStringSubmatch(text, -1) {
			add(pyLower(m[1]), m[2])
		}
	}
	delete(out, me+"#"+strconv.Itoa(self))
	return out
}

// V2Dirs maps each path to its directory at depth 2 ("." for root files).
func V2Dirs(paths []string) map[string]struct{} {
	out := map[string]struct{}{}
	for _, p := range paths {
		parts := strings.Split(p, "/")
		parts = parts[:len(parts)-1]
		if len(parts) > 2 {
			parts = parts[:2]
		}
		d := strings.Join(parts, "/")
		if d == "" {
			d = "."
		}
		out[d] = struct{}{}
	}
	return out
}

// NormAuthor lower-cases a login and strips a trailing "[bot]".
func NormAuthor(a string) string {
	a = strings.TrimSuffix(pyLower(a), "[bot]")
	return a
}

// ParsedState is a run-3 rendered state split into the parts retrieval uses.
type ParsedState struct {
	Title string
	Body  string
	Paths []string
	Diff  string
}

// ParseState is mergeone/memory/retrieval_v2.parse_state.
func ParseState(state string) ParsedState {
	var ps ParsedState
	first := state
	if i := strings.IndexByte(state, '\n'); i >= 0 {
		first = state[:i]
	}
	ps.Title = strings.TrimPrefix(first, "PULL REQUEST TITLE: ")
	if i := strings.Index(state, "MERGE MESSAGE BODY:\n"); i >= 0 {
		s := i + len("MERGE MESSAGE BODY:\n")
		if j := strings.Index(state[s:], "\n\nSUMMARY:"); j >= 0 {
			ps.Body = state[s : s+j]
		} else {
			ps.Body = state[s:]
		}
	}
	if k := strings.Index(state, "\nDIFF ("); k >= 0 {
		if e := strings.IndexByte(state[k+1:], '\n'); e >= 0 {
			ps.Diff = state[k+1+e+1:]
		}
	}
	on := false
	for _, line := range strings.Split(state, "\n") {
		if strings.HasPrefix(line, "FILES CHANGED") {
			on = true
			continue
		}
		if on {
			if !strings.HasPrefix(line, "  ") || strings.HasPrefix(line, "  ...") {
				if pyStrip(line) == "" || strings.HasPrefix(line, "DIFF") {
					break
				}
				continue
			}
			ps.Paths = append(ps.Paths, strings.SplitN(pyStrip(line), " ", 2)[0])
		}
	}
	return ps
}

// ---------------------------------------------------------------- index
//
// Documents are numbered in (created, number) order, so the "created within the window" part of the window is a
// contiguous doc range [a, b). Every posting list is in doc order, so each list is cut to [a, b) by binary search.
// Documents whose anchor (merged, else created) is in the window but whose created time is not ("extra" docs,
// typically PRs opened earlier and merged inside the window) are scored one by one by the reference scan.
// Documents that no posting touches have every feature 0 (weights ≥ 0, and the author feature is posted when its
// weight is > 0) and only matter if fewer than k touched documents exist, in which case they fill by number.

type v2Doc struct {
	number  int
	created int64 // unix nanos
	anchor  int64
	terms   []int32 // sorted term ids
	tfs     []int32
	dl      int
	norm    float64 // k1·(1 − b + b·dl/avg)
	files   []int32
	dirs    []int32
	refs    []int32
	author  string
	entry   *Entry
}

type posting struct{ d, tf int32 }

type v2Index struct {
	docs       []v2Doc // in (created, number) order
	createdK   []int64 // docs[i].created
	byAnchor   []int32 // doc indices in (anchor, number) order
	anchorK    []int64
	termID     map[string]int32
	idf        []float64 // by term id
	termPost   [][]posting
	fileID     map[string]int32
	filePost   [][]int32
	dirID      map[string]int32
	dirPost    [][]int32
	refID      map[string]int32
	refPost    [][]int32
	authorPost map[string][]int32

	// per-query scratch (guarded by Repo.v2mu)
	stamp   []uint32
	gen     uint32
	accBM   []float64
	accFile []int32
	accDir  []int32
	accIss  []bool
}

// runtime variables (not constants) so k1+1 etc. are rounded exactly as Python computes them
var bmK1, bmB = 1.2, 0.75

func internSorted(set map[string]struct{}, ids map[string]int32, grow bool) []int32 {
	out := make([]int32, 0, len(set))
	for s := range set {
		id, ok := ids[s]
		if !ok {
			if !grow {
				continue
			}
			id = int32(len(ids))
			ids[s] = id
		}
		out = append(out, id)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

func buildV2(name string, prs map[int]*Entry) *v2Index {
	ix := &v2Index{fileID: map[string]int32{}, dirID: map[string]int32{}, refID: map[string]int32{}, authorPost: map[string][]int32{}}
	es := make([]*Entry, 0, len(prs))
	for _, e := range prs {
		es = append(es, e)
	}
	sort.Slice(es, func(i, j int) bool {
		ci, cj := es[i].created.UnixNano(), es[j].created.UnixNano()
		if ci != cj {
			return ci < cj
		}
		return es[i].Number < es[j].Number
	})
	n := len(es)
	toks := make([]map[string]int, n)
	df := map[string]int{}
	total := 0
	for i, e := range es {
		toks[i] = V2Tokens(e.Title, e.Body)
		for t, c := range toks[i] {
			df[t]++
			total += c
		}
	}
	vocab := make([]string, 0, len(df))
	for t := range df {
		vocab = append(vocab, t)
	}
	sort.Strings(vocab) // ids increase with byte order = Python's sorted() on ASCII tokens
	ix.termID = make(map[string]int32, len(vocab))
	ix.idf = make([]float64, len(vocab))
	N := n
	if N < 1 {
		N = 1
	}
	avg := 1.0
	if n > 0 {
		avg = math.Max(1, float64(total)/float64(n))
	}
	for i, t := range vocab {
		ix.termID[t] = int32(i)
		d := float64(df[t])
		ix.idf[i] = math.Log(1 + (float64(N)-d+0.5)/(d+0.5))
	}
	ix.termPost = make([][]posting, len(vocab))
	ix.docs = make([]v2Doc, n)
	ix.createdK = make([]int64, n)
	for i, e := range es {
		d := v2Doc{number: e.Number, entry: e, author: NormAuthor(e.Author)}
		d.created = e.created.UnixNano()
		d.anchor = d.created
		if !e.merged.IsZero() {
			d.anchor = e.merged.UnixNano()
		}
		ids := make([]int32, 0, len(toks[i]))
		for t := range toks[i] {
			ids = append(ids, ix.termID[t])
		}
		sort.Slice(ids, func(a, b int) bool { return ids[a] < ids[b] })
		d.terms = ids
		d.tfs = make([]int32, len(ids))
		for j, id := range ids {
			c := toks[i][vocab[id]]
			d.tfs[j] = int32(c)
			d.dl += c
			ix.termPost[id] = append(ix.termPost[id], posting{int32(i), int32(c)})
		}
		d.norm = bmK1 * (1 - bmB + bmB*float64(d.dl)/avg)
		paths := make(map[string]struct{}, len(e.Files))
		pl := make([]string, 0, len(e.Files))
		for _, f := range e.Files {
			if _, ok := paths[f.Path]; !ok {
				paths[f.Path] = struct{}{}
				pl = append(pl, f.Path)
			}
		}
		d.files = internSorted(paths, ix.fileID, true)
		d.dirs = internSorted(V2Dirs(pl), ix.dirID, true)
		d.refs = internSorted(V2Refs(e.Title+"\n"+e.Body, name, e.Number), ix.refID, true)
		if d.author != "" {
			ix.authorPost[d.author] = append(ix.authorPost[d.author], int32(i))
		}
		ix.docs[i] = d
		ix.createdK[i] = d.created
	}
	ix.filePost = make([][]int32, len(ix.fileID))
	ix.dirPost = make([][]int32, len(ix.dirID))
	ix.refPost = make([][]int32, len(ix.refID))
	for i := range ix.docs {
		d := &ix.docs[i]
		for _, f := range d.files {
			ix.filePost[f] = append(ix.filePost[f], int32(i))
		}
		for _, f := range d.dirs {
			ix.dirPost[f] = append(ix.dirPost[f], int32(i))
		}
		for _, f := range d.refs {
			ix.refPost[f] = append(ix.refPost[f], int32(i))
		}
	}
	ix.byAnchor = make([]int32, n)
	for i := range ix.byAnchor {
		ix.byAnchor[i] = int32(i)
	}
	sort.SliceStable(ix.byAnchor, func(a, b int) bool { return ix.docs[ix.byAnchor[a]].anchor < ix.docs[ix.byAnchor[b]].anchor })
	ix.anchorK = make([]int64, n)
	for i, d := range ix.byAnchor {
		ix.anchorK[i] = ix.docs[d].anchor
	}
	ix.stamp = make([]uint32, n)
	ix.accBM = make([]float64, n)
	ix.accFile = make([]int32, n)
	ix.accDir = make([]int32, n)
	ix.accIss = make([]bool, n)
	return ix
}

// QueryV2 is a prepared A1 query; build it with NewQueryV2.
type QueryV2 struct {
	Repo      string
	Number    int
	CreatedAt time.Time
	terms     []string // sorted unique
	paths     map[string]struct{}
	dirs      map[string]struct{}
	refs      map[string]struct{}
	author    string
}

// NewQueryV2 prepares a query PR (which need not be in memory).
func NewQueryV2(repo string, number int, title, body string, paths []string, author string, createdAt time.Time) QueryV2 {
	q := QueryV2{Repo: repo, Number: number, CreatedAt: createdAt.UTC(), author: NormAuthor(author)}
	for t := range V2Tokens(title, body) {
		q.terms = append(q.terms, t)
	}
	sort.Strings(q.terms)
	q.paths = make(map[string]struct{}, len(paths))
	for _, p := range paths {
		q.paths[p] = struct{}{}
	}
	q.dirs = V2Dirs(paths)
	q.refs = V2Refs(title+"\n"+body, repo, number)
	return q
}

// CandidateV2 is one A1 hit with its features.
type CandidateV2 struct {
	Number     int     `json:"number"`
	Score      float64 `json:"score"`
	Rank       int     `json:"a1_rank"`
	IssueRef   bool    `json:"issue_ref"`
	DirJaccard float64 `json:"dir_jaccard"`
	Jaccard    float64 `json:"jaccard"`
	BM25       float64 `json:"bm25"`
	SameAuthor bool    `json:"same_author"`
	State      string  `json:"state"`
	Title      string  `json:"title"`
	Author     string  `json:"author"`
	CreatedAt  string  `json:"created_at"`
	MergedAt   string  `json:"merged_at,omitempty"`
}

func interCount(a, b []int32) int {
	i, j, n := 0, 0, 0
	for i < len(a) && j < len(b) {
		switch {
		case a[i] == b[j]:
			n++
			i++
			j++
		case a[i] < b[j]:
			i++
		default:
			j++
		}
	}
	return n
}

func jacc(inter, nq, nc int) float64 {
	union := nq + nc - inter
	if union == 0 {
		return 0
	}
	return float64(inter) / float64(union)
}

func (r *Repo) v2index() *v2Index {
	r.mu.RLock()
	ix := r.v2
	r.mu.RUnlock()
	if ix != nil {
		return ix
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.v2 == nil {
		r.v2 = buildV2(r.name, r.prs)
	}
	return r.v2
}

// BuildV2 builds the A1 index now (otherwise it is built on first use).
func (r *Repo) BuildV2() { r.v2index() }

type qIDs struct {
	terms, files, dirs, refs []int32
	nFiles, nDirs            int
}

func (ix *v2Index) prepare(q QueryV2) qIDs {
	var x qIDs
	x.terms = make([]int32, 0, len(q.terms))
	for _, t := range q.terms { // sorted strings → increasing ids
		if id, ok := ix.termID[t]; ok {
			x.terms = append(x.terms, id)
		}
	}
	x.files = internSorted(q.paths, ix.fileID, false)
	x.dirs = internSorted(q.dirs, ix.dirID, false)
	x.refs = internSorted(q.refs, ix.refID, false)
	x.nFiles, x.nDirs = len(q.paths), len(q.dirs)
	return x
}

type v2row struct {
	d                      int32
	iss, dj, fj, bm, au, s float64
}

// scanDoc computes one document's raw features (bm not yet normalised): the reference path.
func (ix *v2Index) scanDoc(q *QueryV2, x *qIDs, di int32) v2row {
	d := &ix.docs[di]
	bm := 0.0
	i, j := 0, 0
	for i < len(x.terms) && j < len(d.terms) {
		switch {
		case x.terms[i] == d.terms[j]:
			tf := float64(d.tfs[j])
			bm += ix.idf[x.terms[i]] * (tf * (bmK1 + 1)) / (tf + d.norm)
			i++
			j++
		case x.terms[i] < d.terms[j]:
			i++
		default:
			j++
		}
	}
	r := v2row{d: di, bm: bm, dj: jacc(interCount(x.dirs, d.dirs), x.nDirs, len(d.dirs)), fj: jacc(interCount(x.files, d.files), x.nFiles, len(d.files))}
	if len(x.refs) > 0 && interCount(x.refs, d.refs) > 0 {
		r.iss = 1
	}
	if q.author != "" && d.author == q.author {
		r.au = 1
	}
	return r
}

func (ix *v2Index) window(q QueryV2) (lo, hi int64, a, b int) {
	win := time.Duration(V2WindowDays) * 24 * time.Hour
	lo, hi = q.CreatedAt.Add(-win).UnixNano(), q.CreatedAt.Add(win).UnixNano()
	a = sort.Search(len(ix.createdK), func(i int) bool { return ix.createdK[i] >= lo })
	b = sort.Search(len(ix.createdK), func(i int) bool { return ix.createdK[i] > hi })
	return
}

// extraDocs are in the window by anchor but not by created time.
func (ix *v2Index) extraDocs(lo, hi int64, a, b int) []int32 {
	s := sort.Search(len(ix.anchorK), func(i int) bool { return ix.anchorK[i] >= lo })
	var out []int32
	for i := s; i < len(ix.anchorK) && ix.anchorK[i] <= hi; i++ {
		d := ix.byAnchor[i]
		if int(d) < a || int(d) >= b {
			out = append(out, d)
		}
	}
	return out
}

func cut32(p []int32, a, b int) []int32 {
	s := sort.Search(len(p), func(i int) bool { return int(p[i]) >= a })
	e := sort.Search(len(p), func(i int) bool { return int(p[i]) >= b })
	return p[s:e]
}

func less2(ix *v2Index, a, b *v2row) bool {
	if a.s != b.s {
		return a.s > b.s
	}
	return ix.docs[a.d].number < ix.docs[b.d].number
}

func (ix *v2Index) finish(rows []v2row, bmMax float64, k int) []CandidateV2 {
	if bmMax == 0 {
		bmMax = 1
	}
	for i := range rows {
		x := &rows[i]
		x.bm = x.bm / bmMax
		x.s = float64(v2WIssue*x.iss) + float64(v2WDir*x.dj)
		x.s = x.s + float64(v2WFile*x.fj)
		x.s = x.s + float64(v2WText*x.bm)
		x.s = x.s + float64(v2WAuthor*x.au)
	}
	if len(rows) > k {
		top := make([]v2row, 0, k+1)
		for i := range rows {
			x := rows[i]
			if len(top) == k && !less2(ix, &x, &top[k-1]) {
				continue
			}
			p := sort.Search(len(top), func(j int) bool { return less2(ix, &x, &top[j]) })
			top = append(top, v2row{})
			copy(top[p+1:], top[p:])
			top[p] = x
			if len(top) > k {
				top = top[:k]
			}
		}
		rows = top
	} else {
		sort.Slice(rows, func(i, j int) bool { return less2(ix, &rows[i], &rows[j]) })
	}
	out := make([]CandidateV2, len(rows))
	for i, x := range rows {
		e := ix.docs[x.d].entry
		out[i] = CandidateV2{Number: e.Number, Score: x.s, Rank: i + 1, IssueRef: x.iss == 1, DirJaccard: x.dj, Jaccard: x.fj, BM25: x.bm,
			SameAuthor: x.au == 1, State: e.State, Title: e.Title, Author: e.Author, CreatedAt: e.CreatedAt, MergedAt: e.MergedAt}
	}
	return out
}

// candidatesV2Scan is the reference implementation: every in-window document scored one by one.
func (r *Repo) candidatesV2Scan(q QueryV2, k int) []CandidateV2 {
	if k <= 0 {
		k = V2TopK
	}
	ix := r.v2index()
	r.v2mu.Lock()
	defer r.v2mu.Unlock()
	lo, hi, a, b := ix.window(q)
	x := ix.prepare(q)
	var rows []v2row
	bmMax := 0.0
	add := func(d int32) {
		if ix.docs[d].number == q.Number {
			return
		}
		row := ix.scanDoc(&q, &x, d)
		if row.bm > bmMax {
			bmMax = row.bm
		}
		rows = append(rows, row)
	}
	for d := a; d < b; d++ {
		add(int32(d))
	}
	for _, d := range ix.extraDocs(lo, hi, a, b) {
		add(d)
	}
	if len(rows) == 0 {
		return nil
	}
	return ix.finish(rows, bmMax, k)
}

// CandidatesV2 returns the A1 top-k (k ≤ 0 → 20), scored through time-ranged postings. Identical results to
// candidatesV2Scan (asserted in tests). Calls on one Repo are serialised (per-query scratch arrays).
func (r *Repo) CandidatesV2(q QueryV2, k int) []CandidateV2 {
	if k <= 0 {
		k = V2TopK
	}
	ix := r.v2index()
	r.v2mu.Lock()
	defer r.v2mu.Unlock()
	if len(ix.docs) == 0 {
		return nil
	}
	lo, hi, a, b := ix.window(q)
	extra := ix.extraDocs(lo, hi, a, b)
	if a == b && len(extra) == 0 {
		return nil
	}
	x := ix.prepare(q)
	ix.gen++
	if ix.gen == 0 {
		for i := range ix.stamp {
			ix.stamp[i] = 0
		}
		ix.gen = 1
	}
	g := ix.gen
	touched := make([]int32, 0, 256)
	touch := func(d int32) {
		if ix.stamp[d] != g {
			ix.stamp[d] = g
			ix.accBM[d], ix.accFile[d], ix.accDir[d], ix.accIss[d] = 0, 0, 0, false
			touched = append(touched, d)
		}
	}
	for _, t := range x.terms { // increasing id = sorted term order: each doc's sum runs in the Python order
		p := ix.termPost[t]
		s := sort.Search(len(p), func(i int) bool { return int(p[i].d) >= a })
		idf := ix.idf[t]
		for _, ps := range p[s:] {
			if int(ps.d) >= b {
				break
			}
			touch(ps.d)
			tf := float64(ps.tf)
			ix.accBM[ps.d] += idf * (tf * (bmK1 + 1)) / (tf + ix.docs[ps.d].norm)
		}
	}
	for _, f := range x.files {
		for _, d := range cut32(ix.filePost[f], a, b) {
			touch(d)
			ix.accFile[d]++
		}
	}
	for _, f := range x.dirs {
		for _, d := range cut32(ix.dirPost[f], a, b) {
			touch(d)
			ix.accDir[d]++
		}
	}
	for _, f := range x.refs {
		for _, d := range cut32(ix.refPost[f], a, b) {
			touch(d)
			ix.accIss[d] = true
		}
	}
	if v2WAuthor > 0 && q.author != "" {
		for _, d := range cut32(ix.authorPost[q.author], a, b) {
			touch(d)
		}
	}
	rows := make([]v2row, 0, len(touched)+len(extra))
	bmMax := 0.0
	for _, d := range touched {
		doc := &ix.docs[d]
		if doc.number == q.Number {
			continue
		}
		row := v2row{d: d, bm: ix.accBM[d], dj: jacc(int(ix.accDir[d]), x.nDirs, len(doc.dirs)), fj: jacc(int(ix.accFile[d]), x.nFiles, len(doc.files))}
		if ix.accIss[d] {
			row.iss = 1
		}
		if q.author != "" && doc.author == q.author {
			row.au = 1
		}
		if row.bm > bmMax {
			bmMax = row.bm
		}
		rows = append(rows, row)
	}
	for _, d := range extra {
		if ix.docs[d].number == q.Number {
			continue
		}
		row := ix.scanDoc(&q, &x, d)
		if row.bm > bmMax {
			bmMax = row.bm
		}
		ix.stamp[d] = g
		rows = append(rows, row)
	}
	// untouched in-range docs have every weighted feature 0; they only matter if fewer than k docs score > 0
	pos := 0
	for i := range rows {
		r := &rows[i]
		if r.iss > 0 || r.dj > 0 || r.fj > 0 || r.bm > 0 || (v2WAuthor > 0 && r.au > 0) {
			pos++
		}
	}
	if pos < k {
		for d := a; d < b && len(rows) < (b-a)+len(extra); d++ {
			if ix.stamp[d] == g || ix.docs[d].number == q.Number {
				continue
			}
			ix.stamp[d] = g
			rows = append(rows, ix.scanDoc(&q, &x, int32(d)))
		}
	}
	if len(rows) == 0 {
		return nil
	}
	return ix.finish(rows, bmMax, k)
}
