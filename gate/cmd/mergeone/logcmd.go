package main

// `mergeone log`: the gatelog as a list a person can read. One line per decision, newest last.
//
//	mergeone log                       the last 20 decisions
//	mergeone log -n 50 --verdict hold  the last 50 holds
//	mergeone log --pr 4123             every head of one PR
//	mergeone log --since 24h --json    records of the last day, one JSON object per line (state left out)
//
// It reads the file and prints; no model, no network, nothing written. `mergeone explain <sha>` opens one line.

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/store"
)

func cmdLog(args []string) int {
	fs := flag.NewFlagSet("log", flag.ContinueOnError)
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	n := fs.Int("n", 20, "how many decisions to show, newest last (0 = all that match)")
	verdict := fs.String("verdict", "", "only pass | hold | block")
	repo := fs.String("repo", "", "only this owner/name")
	pr := fs.Int("pr", 0, "only this pull request")
	since := fs.Duration("since", 0, "only decisions newer than this (e.g. 24h, 90m)")
	asJSON := fs.Bool("json", false, "one JSON record per line, without the rendered state")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	switch *verdict {
	case "", "pass", "hold", "block":
	default:
		fmt.Fprintf(stderr, "error: --verdict %q: want pass, hold or block\n", *verdict)
		return 2
	}
	f, err := os.Open(*logPath)
	if err != nil {
		fmt.Fprintf(stderr, "error: %v\n", err)
		return 2
	}
	defer f.Close()
	var cut time.Time
	if *since > 0 {
		cut = time.Now().Add(-*since)
	}
	var recs []store.Record
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var rec store.Record
		if json.Unmarshal(sc.Bytes(), &rec) != nil || rec.Result.Verdict == "" {
			continue
		}
		if (*verdict != "" && rec.Result.Verdict != *verdict) || (*repo != "" && rec.Repo != *repo) || (*pr > 0 && rec.PR != *pr) ||
			(!cut.IsZero() && rec.TS.Before(cut)) {
			continue
		}
		rec.State = ""
		recs = append(recs, rec)
		if *n > 0 && len(recs) > *n { // keep a window: the log can be long
			recs = recs[len(recs)-*n:]
		}
	}
	if err := sc.Err(); err != nil {
		fmt.Fprintf(stderr, "error: reading %s: %v\n", *logPath, err)
		return 2
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		for _, r := range recs {
			_ = enc.Encode(r)
		}
		return 0
	}
	if len(recs) == 0 {
		fmt.Fprintln(stdout, "no decisions match")
		return 0
	}
	for _, r := range recs {
		fmt.Fprintln(stdout, logLine(r))
	}
	return 0
}

// logLine is one decision on one line: when, the verdict and its route, the PR and head, the rule, and what made it
// cheap or unusual (a reused verdict, shadow mode, a model error).
func logLine(r store.Record) string {
	ref := r.Repo
	if r.PR > 0 {
		ref = fmt.Sprintf("%s#%d", r.Repo, r.PR)
	}
	sha := r.HeadSHA
	if len(sha) > 10 {
		sha = sha[:10]
	}
	rule := r.Result.Rule
	if rule == "" {
		rule = "(no rule)"
	}
	var notes []string
	if r.ReusedFrom != "" {
		notes = append(notes, "reused")
	}
	if r.Shadow {
		notes = append(notes, "shadow")
	}
	if r.Result.Error != "" {
		notes = append(notes, "error: "+store.ErrorKind(r.Result.Error))
	}
	if len(r.DebouncedSHAs) > 0 {
		notes = append(notes, fmt.Sprintf("%d debounced", len(r.DebouncedSHAs)))
	}
	note := ""
	if len(notes) > 0 {
		note = "  [" + strings.Join(notes, ", ") + "]"
	}
	return fmt.Sprintf("%s  %-5s → %-10s %s @ %s  %s%s", r.TS.UTC().Format("2006-01-02 15:04"), r.Result.Verdict, r.Result.Route, ref, sha, rule, note)
}
