package main

import (
	"errors"
	"flag"
	"fmt"
	"path/filepath"
	"sort"

	"github.com/haalp/mergeone/gate/internal/calibrate"
	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// cmdCalibrate refits the calibration maps from the gatelog (docs/drift-monitor.md "Recalibration"):
//
//	mergeone calibrate --from-gatelog [--since 28d] [--model m] [--label iterated] [--out calibration_<m>_<qset>.json] [--force]
//
// Isotonic per question (the fit `gate fit-calibration` makes) on the raw answers of PRs with an outcome,
// written with fit_stats and fit_meta. Refuses under 200 labelled decisions. Nothing refits automatically;
// the new map is used once the gateway loads it (restart).
func cmdCalibrate(args []string) int {
	fs := flag.NewFlagSet("calibrate", flag.ContinueOnError)
	fromLog := fs.Bool("from-gatelog", false, "refit from the gatelog's decisions with outcomes (the only source)")
	policy := fs.String("policy", ".mergeone.yaml", "policy file (names the default output: calibration_<model>_<question_set>.json beside it)")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	sinceS := fs.String("since", "28d", "decisions made in this trailing window (28d, 36h, seconds)")
	model := fs.String("model", "", "model version (or registry name matching <name>-*); default: the newest labelled decision's")
	label := fs.String("label", engine.R8SlowLabel, "outcome label: iterated | merged | reverted")
	out := fs.String("out", "", "output file (default: calibration_<policy model>_<question_set>.json beside the policy)")
	force := fs.Bool("force", false, "replace an existing output file")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if !*fromLog {
		fmt.Fprintln(stderr, "calibrate: --from-gatelog is required (refitting from the gatelog is the only source; benchmark maps come from `gate fit-calibration`)")
		return 2
	}
	since, err := calibrate.ParseAge(*sinceS)
	if err != nil {
		return fail(fmt.Errorf("--since: %w", err))
	}
	if *out == "" {
		p, err := engine.LoadPolicy(*policy)
		if err != nil {
			return fail(fmt.Errorf("%w (or pass --out)", err))
		}
		*out = filepath.Join(filepath.Dir(*policy), fmt.Sprintf("calibration_%s_%s.json", p.Model, p.QuestionSet))
	}
	st, err := store.Open(*logPath)
	if err != nil {
		return fail(err)
	}
	f, sum, err := calibrate.Fit(st, calibrate.Options{Since: since, Model: *model, Label: *label})
	if err != nil {
		if errors.Is(err, calibrate.ErrTooFew) {
			fmt.Fprintln(stderr, "calibrate: refused:", err)
			return 1
		}
		return fail(err)
	}
	if err := calibrate.Write(*out, f, *force); err != nil {
		return fail(err)
	}
	printSummary(sum)
	fmt.Fprintf(stdout, "wrote %s (%d maps, fit_stats for %d questions); the gateway uses it after a restart\n", *out, len(f.Maps), len(f.FitStats))
	return 0
}

func printSummary(s calibrate.Summary) {
	fmt.Fprintf(stdout, "%d decisions of %v since %s with %q (%d positive)\n", s.N, s.ModelVersions, s.Since.Format("2006-01-02"), s.Label, s.Positives)
	qs := make([]string, 0, len(s.Questions))
	for q := range s.Questions {
		qs = append(qs, q)
	}
	sort.Strings(qs)
	fmt.Fprintf(stdout, "%-22s %5s %5s %7s %7s %7s  %s\n", "question", "n", "pos", "mean", "yes", "spread", "map")
	for _, q := range qs {
		d := s.Questions[q]
		m := "fitted"
		if !d.Fitted {
			m = "skipped (one class)"
		}
		fmt.Fprintf(stdout, "%-22s %5d %5d %7.3f %7.3f %7.3f  %s\n", q, d.N, d.Positives, d.Mean, d.YesRate, d.Spread, m)
	}
}
