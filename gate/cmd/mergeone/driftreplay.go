package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"regexp"
	"sort"
	"strings"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// multiFlag collects a repeated flag.
type multiFlag []string

func (m *multiFlag) String() string     { return strings.Join(*m, " ") }
func (m *multiFlag) Set(v string) error { *m = append(*m, v); return nil }

// cmdDriftReplay runs the drift monitor's fast signal over two prediction files (PROTOCOL.md R8-H5):
//
//	mergeone drift-replay --predictions then.jsonl[,more.jsonl] --predictions now.jsonl[,more.jsonl] [--qset v3] [--json]
//
// The first --predictions is the reference, the second the current window; a comma joins files into one
// side (later rows win on a repeated PR). Per question, both sides are restricted to the PRs both answered
// (identical rows), then summed and compared with engine.CompareDrift, the code path /stats uses.
func cmdDriftReplay(args []string) int {
	fs := flag.NewFlagSet("drift-replay", flag.ContinueOnError)
	var preds multiFlag
	fs.Var(&preds, "predictions", "prediction file(s): first = reference, second = current (comma joins files)")
	qset := fs.String("qset", "v3", "question set to read (rows of other sets are ignored)")
	split := fs.String("split", "", "only rows of this split (default: all)")
	asJSON := fs.Bool("json", false, "print the report as JSON")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if len(preds) != 2 {
		fmt.Fprintln(stderr, "drift-replay: need exactly two --predictions (reference, then current)")
		return 2
	}
	var sides [2]map[int64]map[string]float64
	for i, p := range preds {
		rows, err := loadPredictions(strings.Split(p, ","), *qset, *split)
		if err != nil {
			return fail(err)
		}
		sides[i] = rows
	}
	rep := replayDrift(sides[0], sides[1])
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", " ")
		_ = enc.Encode(rep)
		return 0
	}
	qs := make([]string, 0, len(rep.Questions))
	for q := range rep.Questions {
		qs = append(qs, q)
	}
	sort.Strings(qs)
	fmt.Fprintf(stdout, "%-22s %5s %7s %7s %7s %7s %7s  %s\n", "question", "n", "ref", "now", "shift", "sd_ref", "sd_now", "degraded")
	for _, q := range qs {
		d := rep.Questions[q]
		fmt.Fprintf(stdout, "%-22s %5d %7.3f %7.3f %7.3f %7.3f %7.3f  %v\n", q, d.N, d.RefMeanRaw, d.MeanRaw, d.Shift, d.RefSpread, d.Spread, d.Degraded)
	}
	fmt.Fprintf(stdout, "flagged (shift > %.2f or spread halved, n >= %d): %v\n", engine.R8DriftShift, engine.R8DriftMinN, rep.Degraded)
	return 0
}

// replayDrift compares two {pr: {question: raw}} sides on the PRs both answered, question by question.
func replayDrift(ref, cur map[int64]map[string]float64) engine.ModelDrift {
	refAgg, curAgg := map[string]engine.DriftAgg{}, map[string]engine.DriftAgg{}
	for pr, c := range cur {
		r, ok := ref[pr]
		if !ok {
			continue
		}
		for q, pc := range c {
			pr, ok := r[q]
			if !ok {
				continue
			}
			a, b := refAgg[q], curAgg[q]
			a.Add(pr)
			b.Add(pc)
			refAgg[q], curAgg[q] = a, b
		}
	}
	refS, curS := map[string]engine.QStats{}, map[string]engine.QStats{}
	for q := range curAgg {
		refS[q], curS[q] = refAgg[q].Stats(), curAgg[q].Stats()
	}
	return engine.DriftReport(curS, refS, "replay")
}

// nonFinite matches the NaN / Infinity values Python's json writes (not JSON; read as missing answers).
var nonFinite = regexp.MustCompile(`:\s*-?(?:NaN|Infinity)\b`)

// loadPredictions reads `mergeone predict` rows: error-free joint rows of one question set, last row per PR.
func loadPredictions(paths []string, qset, split string) (map[int64]map[string]float64, error) {
	out := map[int64]map[string]float64{}
	for _, p := range paths {
		f, err := os.Open(p)
		if err != nil {
			return nil, err
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 64<<20)
		for sc.Scan() {
			var r struct {
				PR       int64  `json:"pr_number"`
				Split    string `json:"split"`
				Qset     string `json:"qset"`
				Mode     string `json:"mode"`
				Decision struct {
					Probs map[string]*float64 `json:"probs"`
					Error *string             `json:"error"`
				} `json:"decision"`
			}
			if len(strings.TrimSpace(sc.Text())) == 0 {
				continue
			}
			if err := json.Unmarshal(nonFinite.ReplaceAll(sc.Bytes(), []byte(": null")), &r); err != nil {
				f.Close()
				return nil, fmt.Errorf("%s: %w", p, err)
			}
			if r.Qset != qset || (r.Mode != "" && r.Mode != "joint") || r.Decision.Error != nil || (split != "" && r.Split != split) {
				continue
			}
			probs := map[string]float64{}
			for q, v := range r.Decision.Probs {
				if v != nil && !math.IsNaN(*v) {
					probs[q] = *v
				}
			}
			out[r.PR] = probs
		}
		err = sc.Err()
		f.Close()
		if err != nil {
			return nil, fmt.Errorf("%s: %w", p, err)
		}
	}
	return out, nil
}
