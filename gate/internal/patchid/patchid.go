// Package patchid computes a stable id for the change a diff makes, in the spirit of `git patch-id --stable`:
// the same patch rebased onto a different base (new head sha, new blob ids, shifted line numbers) gets the
// same id; any changed added or removed line gets a different one.
//
// Normalisation, per file section (`diff --git …`):
//   - `index …` lines are dropped (blob ids change on every rebase);
//   - hunk headers (`@@ -a,b +c,d @@ context`) are dropped (line numbers move with the base);
//   - context lines (leading space) have all whitespace removed (a whitespace-only change in the
//     surrounding code is not a change of this patch);
//   - added and removed lines keep their sign and text, with trailing whitespace and CR trimmed;
//   - every other line (paths, ---/+++, mode, rename, "\ No newline") is kept as is, CR trimmed.
//
// Each file section is hashed on its own and the sorted file hashes are hashed again, so the order GitHub
// lists the files in does not matter (the --stable property). The id is hex SHA-256; "" for an empty diff.
package patchid

import (
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"strings"
	"unicode"
)

// Diff returns the patch id of a unified diff (git format), or "" when it has no file section.
func Diff(diff string) string {
	var files []string
	var cur *strings.Builder
	for _, line := range strings.Split(diff, "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.HasPrefix(line, "diff --git ") {
			if cur != nil {
				files = append(files, cur.String())
			}
			cur = &strings.Builder{}
		}
		if cur == nil { // preamble (format-patch headers, a commit message): not part of the change
			continue
		}
		if n, keep := normLine(line); keep {
			cur.WriteString(n)
			cur.WriteByte('\n')
		}
	}
	if cur != nil {
		files = append(files, cur.String())
	}
	if len(files) == 0 {
		return ""
	}
	sums := make([]string, len(files))
	for i, f := range files {
		s := sha256.Sum256([]byte(f))
		sums[i] = hex.EncodeToString(s[:])
	}
	sort.Strings(sums)
	all := sha256.Sum256([]byte(strings.Join(sums, "\n")))
	return hex.EncodeToString(all[:])
}

func normLine(line string) (string, bool) {
	switch {
	case strings.HasPrefix(line, "index "), strings.HasPrefix(line, "@@"):
		return "", false
	case strings.HasPrefix(line, "+++ "), strings.HasPrefix(line, "--- "):
		return line, true
	case strings.HasPrefix(line, "+"), strings.HasPrefix(line, "-"):
		return strings.TrimRightFunc(line, unicode.IsSpace), true
	case strings.HasPrefix(line, " "), line == "":
		s := strings.Map(func(r rune) rune {
			if unicode.IsSpace(r) {
				return -1
			}
			return r
		}, line)
		if s == "" {
			return "", false // blank context line, or the split's trailing empty string
		}
		return " " + s, true
	}
	return line, true
}

// stateDiffMarker starts the diff section of a render_state text (server.RenderState).
const stateDiffMarker = "\nDIFF (first "

// State returns the patch id of a rendered state (the /decide and MCP paths, where the gateway sees only the
// state): the FILES CHANGED block (path +ins -del, which covers the part of the diff past the 4000-char cut)
// plus the diff section, normalised as in Diff. "" when the state is not a render_state text with a diff.
func State(state string) string {
	i := strings.Index(state, stateDiffMarker)
	if i < 0 {
		return ""
	}
	nl := strings.IndexByte(state[i+1:], '\n')
	if nl < 0 {
		return ""
	}
	d := Diff(state[i+1+nl+1:])
	if d == "" {
		return ""
	}
	files := ""
	if j := strings.Index(state, "FILES CHANGED"); j >= 0 && j < i {
		files = state[j:i]
	}
	s := sha256.Sum256([]byte(files + "\x00" + d))
	return hex.EncodeToString(s[:])
}
