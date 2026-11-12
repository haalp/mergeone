package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// Human-readable verdicts (v0.7): what `mergeone pr` prints on a terminal and what `mergeone explain` prints for a
// recorded decision. JSON stays the output for pipes, CI and `--json`, unchanged.

// stdoutIsTTY reports whether the process's real stdout is a terminal (false in tests, pipes and CI).
func stdoutIsTTY() bool {
	if stdout != io.Writer(os.Stdout) {
		return false
	}
	st, err := os.Stdout.Stat()
	return err == nil && st.Mode()&os.ModeCharDevice != 0
}

func fmtP(p float64) string { return strconv.FormatFloat(math.Round(p*100)/100, 'f', 2, 64) }

func condText(c engine.CondTrace) string {
	if c.Missing {
		return fmt.Sprintf("%s (no answer) %s %s", c.Question, c.Op, fmtP(c.Value))
	}
	return fmt.Sprintf("%s %s %s %s", c.Question, fmtP(c.P), c.Op, fmtP(c.Value))
}

// effectiveProbs is the probability set the rules see for a recorded result under gate g: the raw answers through
// g's calibration map, plus the non-model signals the record carries (silent_days, duplicate_of, …).
func effectiveProbs(g *engine.Gate, res engine.Result) map[string]float64 {
	out := map[string]float64{}
	if len(res.RawProbs) > 0 {
		for q, p := range g.Cal.Apply(res.RawProbs) {
			out[q] = p
		}
	}
	for q, p := range res.Probs {
		if _, raw := res.RawProbs[q]; !raw {
			out[q] = p
		}
	}
	return out
}

// printVerdict writes the verdict block. trace may be nil (a result that never reached the rules).
func printVerdict(w io.Writer, res engine.Result, trace []engine.RuleTrace, repo string, pr int, sha string) {
	id := repo
	if pr > 0 {
		id += "#" + strconv.Itoa(pr)
	}
	if sha != "" {
		if id != "" {
			id += " @ "
		}
		id += fmt.Sprintf("%.10s", sha)
	}
	head := strings.ToUpper(res.Verdict)
	if res.Route != "" {
		head += " → " + res.Route
	}
	fmt.Fprintf(w, "%-22s %s\n", head, id)
	fmt.Fprintf(w, "  rule     %s", res.Rule)
	if res.Reason != "" {
		fmt.Fprintf(w, "  (%s)", res.Reason)
	}
	fmt.Fprintln(w)
	if res.Error != "" {
		fmt.Fprintf(w, "  error    %s\n", res.Error)
	}
	var fired *engine.RuleTrace
	for i := range trace {
		if trace[i].Fired {
			fired = &trace[i]
		}
	}
	if fired != nil {
		var why []string
		for _, c := range fired.Conds {
			why = append(why, fmt.Sprintf("%s (by %s)", condText(c), fmtP(c.Margin)))
		}
		if fired.PathNote != "" {
			why = append(why, fired.PathNote)
		}
		if len(why) > 0 {
			fmt.Fprintf(w, "  because  %s\n", strings.Join(why, "; "))
		}
	}
	// the nearest rule that did not fire and sits above the fired one (or anywhere, for the default)
	if near := nearestMiss(trace); near != nil {
		var miss []string
		for _, c := range near.Conds {
			if !c.OK {
				miss = append(miss, condText(c)+" is false")
			}
		}
		if !near.PathsOK && near.PathNote != "" {
			miss = append(miss, near.PathNote)
		}
		fmt.Fprintf(w, "  nearest  %s → %s/%s: %s", near.Name, near.Verdict, near.Route, strings.Join(miss, "; "))
		if near.PathsOK && near.Nearest > 0 {
			fmt.Fprintf(w, " (%s away)", fmtP(near.Nearest))
		}
		fmt.Fprintln(w)
	}
	if len(res.Probs) > 0 {
		type kv struct {
			q string
			p float64
		}
		var ps []kv
		for q, p := range res.Probs {
			if p >= 0 && p <= 1 && !math.IsNaN(p) {
				ps = append(ps, kv{q, p})
			}
		}
		sort.Slice(ps, func(i, j int) bool {
			if ps[i].p != ps[j].p {
				return ps[i].p > ps[j].p
			}
			return ps[i].q < ps[j].q
		})
		parts := make([]string, 0, len(ps))
		for _, x := range ps {
			parts = append(parts, x.q+" "+fmtP(x.p))
		}
		fmt.Fprintf(w, "  answers  %s\n", strings.Join(parts, " · "))
	}
	if n, ok := res.Extra["duplicate_candidate"]; ok {
		fmt.Fprintf(w, "  memory   closest earlier PR #%v (duplicate_of %s)\n", n, fmtP(res.Probs["duplicate_of"]))
	}
	if len(res.Drift) > 0 {
		fmt.Fprintf(w, "  drift    degraded questions: %s\n", strings.Join(res.Drift, ", "))
	}
	if res.ModelVersion != "" || res.CostUSD > 0 {
		calls := res.ModelCalls
		line := fmt.Sprintf("  model    %s · %.0f ms · $%.6f · %d call", res.ModelVersion, res.LatencyMS, res.CostUSD, calls)
		if calls != 1 {
			line += "s"
		}
		if res.PairCacheHits > 0 {
			line += fmt.Sprintf(" · %d from the pair cache", res.PairCacheHits)
		}
		if res.Retries > 0 {
			line += fmt.Sprintf(" · %d retried", res.Retries)
		}
		fmt.Fprintln(w, line)
	}
	fmt.Fprintln(w, "  The gate never merges or closes a pull request; this is a verdict, not an action.")
}

// nearestMiss picks the unmatched rule whose `when` is closest to holding among the rules evaluated before the
// one that fired (every rule, when the default fired). Rules that failed only on their path condition count as
// distance 0 with the path note as the reason. nil when there is none.
func nearestMiss(trace []engine.RuleTrace) *engine.RuleTrace {
	var best *engine.RuleTrace
	for i := range trace {
		t := &trace[i]
		if t.Fired {
			break
		}
		if t.Matched || t.Skipped != "" {
			continue
		}
		missing := false
		for _, c := range t.Conds {
			if c.Missing {
				missing = true
			}
		}
		if missing {
			continue
		}
		if best == nil || t.Nearest < best.Nearest-1e-9 { // ties go to the earlier rule
			best = t
		}
	}
	return best
}

func printTrace(w io.Writer, trace []engine.RuleTrace, defaultFired bool, def engine.Outcome) {
	fmt.Fprintln(w, "  rules, in order (first match wins):")
	for i, t := range trace {
		mark := " "
		switch {
		case t.Fired:
			mark = "▶"
		case t.Skipped != "":
			mark = "!"
		case t.Matched:
			mark = "·" // would match, but an earlier rule fired
		}
		var parts []string
		for _, c := range t.Conds {
			s := condText(c)
			if c.OK {
				s += " ✓"
			} else {
				s += " ✗"
			}
			parts = append(parts, s)
		}
		if t.PathNote != "" {
			s := t.PathNote
			if t.PathsOK {
				s += " ✓"
			} else {
				s += " ✗"
			}
			parts = append(parts, s)
		}
		if t.Skipped != "" {
			parts = []string{"not evaluated: " + t.Skipped}
		}
		if len(parts) == 0 {
			parts = []string{"(no conditions)"}
		}
		fmt.Fprintf(w, "  %s %2d. %-22s → %-5s %s\n", mark, i+1, t.Name, t.Verdict, strings.Join(parts, " · "))
	}
	mark := " "
	if defaultFired {
		mark = "▶"
	}
	fmt.Fprintf(w, "  %s     %-22s → %-5s (no rule matched)\n", mark, "default", def.Verdict)
}

// findRecord scans a gatelog for the newest record matching ref: "" (the last record), a head-sha prefix,
// "#N" / "N" (a PR number), or "owner/name#N".
func findRecord(logPath, ref string) (*store.Record, error) {
	f, err := os.Open(logPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	repo, pr, sha := "", 0, ""
	switch {
	case ref == "":
	case strings.Contains(ref, "#"):
		i := strings.LastIndex(ref, "#")
		repo = ref[:i]
		if pr, err = strconv.Atoi(ref[i+1:]); err != nil || pr <= 0 {
			return nil, fmt.Errorf("%q: want owner/name#N, #N or a head sha", ref)
		}
	default:
		if n, err := strconv.Atoi(ref); err == nil && n > 0 && len(ref) < 7 {
			pr = n
		} else {
			sha = strings.ToLower(ref)
		}
	}
	var found *store.Record
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var rec store.Record
		if json.Unmarshal(sc.Bytes(), &rec) != nil || rec.Result.Verdict == "" {
			continue
		}
		if repo != "" && rec.Repo != repo {
			continue
		}
		if pr > 0 && rec.PR != pr {
			continue
		}
		if sha != "" && !strings.HasPrefix(strings.ToLower(rec.HeadSHA), sha) {
			continue
		}
		r := rec
		found = &r
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	if found == nil {
		if ref == "" {
			return nil, fmt.Errorf("%s holds no decisions", logPath)
		}
		return nil, fmt.Errorf("no decision for %q in %s", ref, logPath)
	}
	return found, nil
}

// cmdExplain is `mergeone explain [ref]`: why a recorded decision got its verdict — every rule in order with
// each condition's probability and margin — and which rule it came closest to. No model call.
func cmdExplain(args []string) int {
	fs := flag.NewFlagSet("explain", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file (the rules are evaluated as they are now)")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	assets := fs.String("assets", "", "assets dir")
	asJSON := fs.Bool("json", false, "print the trace as JSON")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if fs.NArg() > 1 {
		return fail(fmt.Errorf("usage: mergeone explain [head-sha | #N | owner/name#N] [flags]"))
	}
	g, err := buildGateOpts(*policy, true, *assets, false)
	if err != nil {
		return fail(err)
	}
	rec, err := findRecord(*logPath, fs.Arg(0))
	if err != nil {
		return fail(err)
	}
	res := rec.Result
	reached := res.Error == "" && res.Rule != "always_human_paths" && res.Rule != "budget" && len(res.RawProbs) > 0
	var trace []engine.RuleTrace
	nowRule, nowOut := res.Rule, engine.Outcome{Verdict: res.Verdict, Route: res.Route, Reason: res.Reason}
	if reached {
		trace, nowRule, nowOut = g.Explain(effectiveProbs(g, res), res.ChangedPaths)
	}
	recordedRule := res.Rule
	if res.CappedRule != "" {
		recordedRule = res.CappedRule
	}
	changed := reached && (nowRule != recordedRule || nowOut.Verdict != res.Verdict) && res.CappedRule == ""
	if *asJSON {
		b, _ := json.MarshalIndent(map[string]any{"repo": rec.Repo, "pr": rec.PR, "head_sha": rec.HeadSHA, "ts": rec.TS,
			"recorded": map[string]any{"verdict": res.Verdict, "route": res.Route, "rule": res.Rule, "reason": res.Reason, "policy_hash": rec.PolicyHash, "error": res.Error},
			"now":      map[string]any{"verdict": nowOut.Verdict, "route": nowOut.Route, "rule": nowRule, "policy_hash": g.PolicyHash()},
			"changed":  changed, "reached_rules": reached, "rules": trace}, "", "  ")
		fmt.Fprintln(stdout, string(b))
		return 0
	}
	printVerdict(stdout, res, trace, rec.Repo, rec.PR, rec.HeadSHA)
	fmt.Fprintf(stdout, "  decided  %s", rec.TS.UTC().Format("2006-01-02 15:04:05Z"))
	if rec.Shadow {
		fmt.Fprint(stdout, " · shadow mode (nothing was written to GitHub)")
	}
	if rec.ReusedFrom != "" {
		fmt.Fprintf(stdout, " · reused from %.10s (same patch)", rec.ReusedFrom)
	}
	fmt.Fprintln(stdout)
	switch {
	case !reached:
		fmt.Fprintln(stdout, "  This decision never reached the rules (see rule and error above), so there is nothing to trace.")
	default:
		fmt.Fprintln(stdout)
		printTrace(stdout, trace, nowRule == "default", g.Policy.Default)
		if res.CappedRule != "" {
			fmt.Fprintf(stdout, "  max_rounds then moved the route to a human (the rules said %s).\n", res.CappedRule)
		}
		if changed {
			fmt.Fprintf(stdout, "\n  Note: under the policy as it is now this decision would be %s/%s by rule %s; it was recorded as %s by %s.\n",
				nowOut.Verdict, nowOut.Route, nowRule, res.Verdict, res.Rule)
		}
	}
	return 0
}
