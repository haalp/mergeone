package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/calibrate"
	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// replayChange is one recorded decision whose verdict the current policy would change.
type replayChange struct {
	Repo    string    `json:"repo"`
	PR      int       `json:"pr,omitempty"`
	HeadSHA string    `json:"head_sha"`
	TS      time.Time `json:"ts"`
	Was     string    `json:"was"`      // verdict/route/rule as recorded
	Now     string    `json:"now"`      // verdict/route/rule under the current policy
	WasRule string    `json:"was_rule"` // for grouping
	NowRule string    `json:"now_rule"`
}

// replayReport is `mergeone replay --gatelog`'s output: the recorded decisions re-run through the policy and
// calibration the gateway would use now, with no model call.
type replayReport struct {
	Gatelog     string                    `json:"gatelog"`
	Policy      string                    `json:"policy"`
	PolicyHash  string                    `json:"policy_hash"`
	Window      string                    `json:"window,omitempty"`
	Records     int                       `json:"records"`      // decisions read in the window
	Replayed    int                       `json:"replayed"`     // decisions with probabilities (the rest never reached the model)
	Skipped     map[string]int            `json:"skipped"`      // why a record was not replayed: on_error, always_human_paths, max_rounds, no_probs
	Same        int                       `json:"same"`         // same verdict, route and rule
	Changed     int                       `json:"changed"`      // any of the three differs
	VerdictFlip int                       `json:"verdict_flip"` // the verdict itself differs
	Transitions map[string]int            `json:"transitions"`  // "pass→hold" counts (verdict flips only)
	ByRule      map[string]map[string]int `json:"by_rule"`      // was_rule → now_rule → count (changed only)
	Changes     []replayChange            `json:"changes"`
	Verdicts    map[string]map[string]int `json:"verdicts"` // {"recorded": {...}, "replayed": {...}}
}

// replayGatelog re-runs every recorded decision's raw probabilities through g's calibration and rules.
func replayGatelog(g *engine.Gate, r io.Reader, since time.Time, policyPath, logPath string, maxChanges int) (*replayReport, error) {
	rep := &replayReport{Gatelog: logPath, Policy: policyPath, PolicyHash: g.PolicyHash(), Skipped: map[string]int{},
		Transitions: map[string]int{}, ByRule: map[string]map[string]int{}, Verdicts: map[string]map[string]int{"recorded": {}, "replayed": {}}}
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var rec store.Record
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if !since.IsZero() && rec.TS.Before(since) {
			continue
		}
		rep.Records++
		switch {
		case rec.Result.Error != "" || rec.Result.Rule == "on_error":
			rep.Skipped["on_error"]++
			continue
		case rec.Result.Rule == "always_human_paths":
			rep.Skipped["always_human_paths"]++
			continue
		case rec.Result.CappedRule != "": // max_rounds replaced the rules' verdict; the pre-cap verdict was not recorded
			rep.Skipped["max_rounds"]++
			continue
		case len(rec.Result.RawProbs) == 0:
			rep.Skipped["no_probs"]++
			continue
		}
		rep.Replayed++
		rule, out := g.MatchPaths(g.Cal.Apply(rec.Result.RawProbs), rec.Result.ChangedPaths)
		wasRule := rec.Result.Rule
		was, now := rec.Result.Verdict+"/"+rec.Result.Route+"/"+wasRule, out.Verdict+"/"+out.Route+"/"+rule
		rep.Verdicts["recorded"][rec.Result.Verdict]++
		rep.Verdicts["replayed"][out.Verdict]++
		if was == now {
			rep.Same++
			continue
		}
		rep.Changed++
		if rec.Result.Verdict != out.Verdict {
			rep.VerdictFlip++
			rep.Transitions[rec.Result.Verdict+"→"+out.Verdict]++
		}
		if rep.ByRule[wasRule] == nil {
			rep.ByRule[wasRule] = map[string]int{}
		}
		rep.ByRule[wasRule][rule]++
		if maxChanges <= 0 || len(rep.Changes) < maxChanges {
			rep.Changes = append(rep.Changes, replayChange{Repo: rec.Repo, PR: rec.PR, HeadSHA: rec.HeadSHA, TS: rec.TS, Was: was, Now: now, WasRule: wasRule, NowRule: rule})
		}
	}
	return rep, sc.Err()
}

func (rep *replayReport) print(w io.Writer) {
	fmt.Fprintf(w, "replayed %d of %d recorded decisions through %s (policy_hash %.12s)", rep.Replayed, rep.Records, rep.Policy, rep.PolicyHash)
	if rep.Window != "" {
		fmt.Fprintf(w, ", last %s", rep.Window)
	}
	fmt.Fprintln(w)
	if len(rep.Skipped) > 0 {
		parts := make([]string, 0, len(rep.Skipped))
		for _, k := range sortedKeysInt(rep.Skipped) {
			parts = append(parts, fmt.Sprintf("%s %d", k, rep.Skipped[k]))
		}
		fmt.Fprintf(w, "not replayed (never reached the model): %s\n", strings.Join(parts, ", "))
	}
	if rep.Replayed == 0 {
		return
	}
	fmt.Fprintf(w, "same %d (%.1f%%), changed %d, verdict flips %d\n", rep.Same, 100*float64(rep.Same)/float64(rep.Replayed), rep.Changed, rep.VerdictFlip)
	for _, k := range sortedKeysInt(rep.Transitions) {
		fmt.Fprintf(w, "  %-12s %d\n", k, rep.Transitions[k])
	}
	if rep.Changed > 0 {
		fmt.Fprintln(w, "rule changes (recorded → now):")
		was := make([]string, 0, len(rep.ByRule))
		for k := range rep.ByRule {
			was = append(was, k)
		}
		sort.Strings(was)
		for _, a := range was {
			for _, b := range sortedKeysInt(rep.ByRule[a]) {
				fmt.Fprintf(w, "  %-24s → %-24s %d\n", a, b, rep.ByRule[a][b])
			}
		}
		fmt.Fprintln(w, "examples:")
		for _, c := range rep.Changes {
			id := c.Repo
			if c.PR > 0 {
				id += fmt.Sprintf("#%d", c.PR)
			}
			fmt.Fprintf(w, "  %s@%.8s  %s  →  %s\n", id, c.HeadSHA, c.Was, c.Now)
		}
		if rep.Changed > len(rep.Changes) {
			fmt.Fprintf(w, "  … %d more (--json lists --max of them)\n", rep.Changed-len(rep.Changes))
		}
	}
	fmt.Fprintf(w, "verdicts recorded %s → replayed %s\n", fmtCounts(rep.Verdicts["recorded"]), fmtCounts(rep.Verdicts["replayed"]))
}

func sortedKeysInt(m map[string]int) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func fmtCounts(m map[string]int) string {
	parts := make([]string, 0, len(m))
	for _, k := range []string{"pass", "hold", "block"} {
		if v, ok := m[k]; ok {
			parts = append(parts, fmt.Sprintf("%s %d", k, v))
		}
	}
	return strings.Join(parts, " · ")
}

// cmdReplayGatelog is `mergeone replay --gatelog <path>`: the policy regression check. Every decision in the
// gatelog is re-run through the policy file and calibration map the gateway would load now, on the recorded
// raw probabilities — no model call, no GitHub call — and the report says which verdicts would differ.
func cmdReplayGatelog(policyPath, logPath, assets, since string, asJSON bool, maxChanges int) int {
	g, err := buildGateOpts(policyPath, true, assets, false)
	if err != nil {
		return fail(err)
	}
	f, err := os.Open(logPath)
	if err != nil {
		return fail(err)
	}
	defer f.Close()
	var t0 time.Time
	if since != "" {
		d, err := calibrate.ParseAge(since)
		if err != nil {
			return fail(fmt.Errorf("--since: %w", err))
		}
		t0 = time.Now().Add(-d)
	}
	rep, err := replayGatelog(g, f, t0, policyPath, logPath, maxChanges)
	if err != nil {
		return fail(err)
	}
	rep.Window = since
	if asJSON {
		b, _ := json.MarshalIndent(rep, "", "  ")
		fmt.Fprintln(stdout, string(b))
	} else {
		rep.print(stdout)
	}
	if rep.VerdictFlip > 0 {
		return 1 // a CI step can fail on "this policy change flips verdicts"
	}
	return 0
}
