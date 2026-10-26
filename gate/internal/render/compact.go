// Package render holds the state renderings the gateway sends to a decision model beyond the full
// render_state text: the compact rendering frozen in PROTOCOL.md §R8 C1 (run 7 C1 rules) that run 8
// made the default for the PR-memory pair call.
//
// Every function is a port of mergeone/data/features.py and produces the identical text: the same
// truncation points, the same markers, and Python's string semantics (lengths and cuts count
// characters, not bytes). gate/testdata/compact_fixture.json is the golden output of the Python
// functions; render_test.go checks the port against it.
package render

import (
	"errors"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// The C1 constants (features.py COMPACT_*). Frozen with PROTOCOL.md §R8; do not tune.
const (
	CompactDiffChars = 2000
	CompactBodyChars = 600
	CompactMaxFiles  = 20
	// PairACap and PairBCap are mergeone/eval/run4.py pair_state's caps, re-applied after compaction.
	PairACap = 6000
	PairBCap = 3000
)

const (
	bodyHdr   = "PULL REQUEST TITLE: "
	bodyMark  = "\nMERGE MESSAGE BODY:\n"
	filesMark = "\nFILES CHANGED (path +ins -del):\n"
	pairA     = "=== PULL REQUEST A ===\n"
	pairB     = "\n\n=== PULL REQUEST B ===\n"
	pairQ     = "\n\nHow does A relate to B?"
	bFiles    = "\nFILES CHANGED:\n"
)

var (
	summaryRE = regexp.MustCompile(`\n\nSUMMARY: \d+ files changed, \+\d+ / -\d+ lines, \d+ top-level areas touched\.(?:\n|$)`)
	moreRE    = regexp.MustCompile(`^  \.\.\. and (\d+) more files$`)
	diffRE    = regexp.MustCompile(`\nDIFF \(first (\d+) chars(, truncated)?\):\n`)
)

// ErrNotState is returned for text that does not carry the render_state markers (Python raises ValueError).
var ErrNotState = errors.New("not a render_state string")

// ErrNotPair is returned for text that is not a run-4 pair state or candidate rendering.
var ErrNotPair = errors.New("not a run-4 pair state")

// Chars cuts s to its first n characters (Python s[:n]); a string of n characters or fewer is returned as is.
func Chars(s string, n int) string {
	if len(s) <= n { // bytes ≥ characters
		return s
	}
	i := 0
	for k := 0; k < n; k++ {
		_, w := utf8.DecodeRuneInString(s[i:])
		i += w
		if i >= len(s) {
			return s
		}
	}
	return s[:i]
}

// CharLen is Python len(s).
func CharLen(s string) int { return utf8.RuneCountInString(s) }

// index is Python s.find(sub, start) for byte offsets (every marker is ASCII, so byte and character
// positions find the same occurrence).
func index(s, sub string, start int) int {
	if start < 0 {
		start = 0
	}
	if start > len(s) {
		return -1
	}
	i := strings.Index(s[start:], sub)
	if i < 0 {
		return -1
	}
	return start + i
}

// CompactState applies the C1 compact rules to a render_state string (full or --words rendering):
// body cut to 600 characters, the first 20 listed files kept with the "... and N more files" line
// recomputed from the listed count plus the stored N, diff cut to 2,000 characters with the header
// rewritten to "first <shown> chars" (", truncated" if the stored header had it or characters were
// removed). Title, SUMMARY and IN WORDS are kept verbatim. Port of features.compact_state.
func CompactState(state string) (string, error) {
	if !strings.HasPrefix(state, bodyHdr) {
		return "", ErrNotState
	}
	b0 := index(state, bodyMark, 0)
	sumFrom := 0
	if b0 >= 0 {
		sumFrom = b0 + 1
	}
	var mSum []int
	if loc := summaryRE.FindStringIndex(state[sumFrom:]); loc != nil {
		mSum = []int{sumFrom + loc[0], sumFrom + loc[1]}
	}
	filesFrom := 0
	if mSum != nil {
		filesFrom = mSum[1] - 1
	}
	f0 := index(state, filesMark, filesFrom)
	diffFrom := 0
	if f0 >= 0 {
		diffFrom = f0 + 1
	}
	var mDiff []int
	if loc := diffRE.FindStringSubmatchIndex(state[diffFrom:]); loc != nil {
		mDiff = make([]int, len(loc))
		for i, v := range loc {
			if v >= 0 {
				v += diffFrom
			}
			mDiff[i] = v
		}
	}
	if b0 < 0 || mSum == nil || f0 < 0 || mDiff == nil {
		return "", ErrNotState
	}
	head := state[:b0+len(bodyMark)]
	body := state[b0+len(bodyMark) : mSum[0]]
	middle := state[mSum[0] : f0+len(filesMark)]
	fileLines := strings.Split(state[f0+len(filesMark):mDiff[0]], "\n")
	if n := len(fileLines); n > 0 && fileLines[n-1] == "" {
		fileLines = fileLines[:n-1]
	}
	more := 0
	var listed []string
	for _, ln := range fileLines {
		if m := moreRE.FindStringSubmatch(ln); m != nil {
			k, _ := strconv.Atoi(m[1])
			more += k
		} else {
			listed = append(listed, ln)
		}
	}
	total := len(listed) + more
	kept := listed
	if len(kept) > CompactMaxFiles {
		kept = kept[:CompactMaxFiles]
	}
	kept = append([]string(nil), kept...)
	if total > CompactMaxFiles {
		kept = append(kept, "  ... and "+strconv.Itoa(total-CompactMaxFiles)+" more files")
	}
	diff := state[mDiff[1]:]
	truncated := mDiff[4] >= 0
	if CharLen(diff) > CompactDiffChars {
		diff, truncated = Chars(diff, CompactDiffChars), true
	}
	tr := ""
	if truncated {
		tr = ", truncated"
	}
	var sb strings.Builder
	sb.Grow(len(head) + len(middle) + 4096)
	sb.WriteString(head)
	sb.WriteString(Chars(body, CompactBodyChars))
	sb.WriteString(middle)
	sb.WriteString(strings.Join(append(kept, ""), "\n"))
	sb.WriteString("\nDIFF (first " + strconv.Itoa(CharLen(diff)) + " chars" + tr + "):\n")
	sb.WriteString(diff)
	return sb.String(), nil
}

// CompactBState applies C1 to the run-4 candidate rendering (title, body ≤ 1,200, STATE, AUTHOR,
// FILES CHANGED path-only, no diff): body cut to 600 characters, at most 20 files plus an
// "... and N more files" line. Port of features.compact_b_state.
func CompactBState(b string) (string, error) {
	i := index(b, bodyMark, 0)
	jFrom := 0
	if i >= 0 {
		jFrom = i + 1
	}
	j := index(b, "\n\nSTATE: ", jFrom)
	kFrom := 0
	if j >= 0 {
		kFrom = j + 1
	}
	k := index(b, bFiles, kFrom)
	if i < 0 || j < 0 || k < 0 {
		return "", ErrNotPair
	}
	files := strings.Split(b[k+len(bFiles):], "\n")
	kept := files
	if len(kept) > CompactMaxFiles {
		kept = append(append([]string(nil), files[:CompactMaxFiles]...), "  ... and "+strconv.Itoa(len(files)-CompactMaxFiles)+" more files")
	}
	return b[:i+len(bodyMark)] + Chars(b[i+len(bodyMark):j], CompactBodyChars) + b[j:k] + bFiles + strings.Join(kept, "\n"), nil
}

// CompactPairState applies C1 to a run-4 pair state ("=== PULL REQUEST A ===" … "How does A relate to B?").
// A is CompactState(aFull) when the full A state is given (the pair state holds only A's first 6,000
// characters, which can cut the diff without a "truncated" mark), else CompactState of the A part as
// stored; B is CompactBState. The 6,000 / 3,000 character caps of run4.pair_state are re-applied.
// Port of features.compact_pair_state; aFull "" means not given.
func CompactPairState(pair, aFull string) (string, error) {
	if !strings.HasPrefix(pair, pairA) || !strings.Contains(pair, pairB) || !strings.HasSuffix(pair, pairQ) {
		return "", ErrNotPair
	}
	aPart, rest, _ := strings.Cut(pair[len(pairA):], pairB)
	if len(rest) < len(pairQ) {
		return "", ErrNotPair
	}
	bPart := rest[:len(rest)-len(pairQ)]
	src := aPart
	if aFull != "" {
		src = aFull
	}
	a, err := CompactState(src)
	if err != nil {
		return "", err
	}
	b, err := CompactBState(bPart)
	if err != nil {
		return "", err
	}
	return pairA + Chars(a, PairACap) + pairB + Chars(b, PairBCap) + pairQ, nil
}
