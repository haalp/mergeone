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
//	                      [--labels data/run3/dataset.jsonl --label iterated]
//
// The first --predictions is the reference, the second the current window; a comma joins files into one
// side (later rows win on a repeated PR). Per question, both sides are restricted to the PRs both answered
// (identical rows), then summed and compared with engine.CompareDrift, the code path /stats uses.
// With --labels (any JSONL whose rows carry pr_number and labels.<label>: the run-3 dataset, or a prediction
// file) the slow signal is replayed too: per question, ROC-AUC against the label on the PRs both sides
// answered and the label covers, reference vs current, through engine.CompareSlow (scripts/drift_slow.py's
// computation).
func cmdDriftReplay(args []string) int {
	fs := flag.NewFlagSet("drift-replay", flag.ContinueOnError)
	var preds multiFlag
	fs.Var(&preds, "predictions", "prediction file(s): first = reference, second = current (comma joins files)")
	qset := fs.String("qset", "v3", "question set to read (rows of other sets are ignored)")
	split := fs.String("split", "", "only rows of this split (default: all)")
	mode := fs.String("mode", "joint", `rows of this request mode only; "any" reads every mode, last row per PR wins (scripts/drift_slow.py's selection)`)
	asJSON := fs.Bool("json", false, "print the report as JSON")
	labelsPath := fs.String("labels", "", "JSONL with pr_number + labels (e.g. data/run3/dataset.jsonl): also replay the slow AUC signal")
	label := fs.String("label", engine.R8SlowLabel, "with --labels: the outcome label the AUC is taken against")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if len(preds) != 2 {
		fmt.Fprintln(stderr, "drift-replay: need exactly two --predictions (reference, then current)")
		return 2
	}
	var sides [2]map[int64]map[string]float64
	for i, p := range preds {
		rows, err := loadPredictions(strings.Split(p, ","), *qset, *split, *mode)
		if err != nil {
			return fail(err)
		}
		sides[i] = rows
	}
	rep := replayDrift(sides[0], sides[1])
	if *labelsPath != "" {
		labels, err := loadLabels(*labelsPath, *label)
		if err != nil {
			return fail(err)
		}
		sd := replaySlow(sides[0], sides[1], labels, *label)
		rep.Slow = &sd
	}
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
	if sd := rep.Slow; sd != nil {
		fmt.Fprintf(stdout, "\nslow signal: ROC-AUC vs %s\n%-22s %5s %7s %7s %7s  %s\n", sd.Label, "question", "n", "ref", "now", "delta", "degraded")
		qs = qs[:0]
		for q := range sd.Questions {
			qs = append(qs, q)
		}
		sort.Strings(qs)
		f := func(p *float64) string {
			if p == nil {
				return "      -"
			}
			return fmt.Sprintf("%7.4f", *p)
		}
		for _, q := range qs {
			d := sd.Questions[q]
			fmt.Fprintf(stdout, "%-22s %5d %s %s %s  %v\n", q, d.N, f(d.RefAUC), f(d.AUC), f(d.Delta), d.Degraded)
		}
		fmt.Fprintf(stdout, "flagged (AUC fell > %.2f, n >= %d): %v\n", engine.R8SlowDrop, engine.R8DriftMinN, sd.Degraded)
	}
	return 0
}

// replaySlow is the slow signal over two prediction sides: per question, the PRs both sides answered and the
// labels cover, AUC of each side against the label.
func replaySlow(ref, cur map[int64]map[string]float64, labels map[int64]bool, label string) engine.SlowDrift {
	refL, curL := map[string]engine.Labelled{}, map[string]engine.Labelled{}
	for pr, c := range cur {
		r, ok := ref[pr]
		y, labelled := labels[pr]
		if !ok || !labelled {
			continue
		}
		for q, pc := range c {
			pr, ok := r[q]
			if !ok {
				continue
			}
			a, b := refL[q], curL[q]
			a.Add(y, pr)
			b.Add(y, pc)
			refL[q], curL[q] = a, b
		}
	}
	return engine.SlowReport(curL, refL, label, "replay")
}

// loadLabels reads {pr_number: labels[label]} from JSONL rows (bool, or a number: non-zero is true). Rows
// without the label are skipped; a later row wins on a repeated PR.
func loadLabels(path, label string) (map[int64]bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	out := map[int64]bool{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		if len(strings.TrimSpace(sc.Text())) == 0 {
			continue
		}
		var r struct {
			PR     int64          `json:"pr_number"`
			Labels map[string]any `json:"labels"`
		}
		if err := json.Unmarshal(nonFinite.ReplaceAll(sc.Bytes(), []byte(": null")), &r); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		switch v := r.Labels[label].(type) {
		case bool:
			out[r.PR] = v
		case float64:
			out[r.PR] = v != 0
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("%s: no row carries labels.%s", path, label)
	}
	return out, nil
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

// loadPredictions reads `mergeone predict` rows: error-free rows of one question set (a row without qset is
// v3, as gatebench writes it) and one request mode (rows without a mode are joint; "any" takes every mode),
// last row per PR.
func loadPredictions(paths []string, qset, split, mode string) (map[int64]map[string]float64, error) {
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
			if r.Qset == "" {
				r.Qset = "v3"
			}
			if r.Mode == "" {
				r.Mode = "joint"
			}
			if r.Qset != qset || (mode != "any" && r.Mode != mode) || r.Decision.Error != nil || (split != "" && r.Split != split) {
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
