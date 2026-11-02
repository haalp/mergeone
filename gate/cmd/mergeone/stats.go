package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/calibrate"
	"github.com/haalp/mergeone/gate/internal/store"
)

// cmdStats prints the gatelog summary GET /stats returns:
//
//	mergeone stats [--log .mergeone/gatelog.jsonl] [--window 24h]
//	mergeone stats --url http://localhost:8787 [--window 24h]
//
// With --url it asks a running gateway; otherwise it reads the gatelog file.
func cmdStats(args []string) int {
	fs := flag.NewFlagSet("stats", flag.ContinueOnError)
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path (ignored with --url)")
	url := fs.String("url", "", "a running gateway (GET <url>/stats) instead of the gatelog file")
	winS := fs.String("window", "24h", "trailing window (24h, 7d, seconds)")
	asJSON := fs.Bool("json", false, "print the raw /stats JSON")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	win, err := calibrate.ParseAge(*winS)
	if err != nil {
		return fail(fmt.Errorf("--window: %w", err))
	}
	var st map[string]any
	if *url != "" {
		resp, err := (&http.Client{Timeout: 15 * time.Second}).Get(fmt.Sprintf("%s/stats?window=%d", strings.TrimRight(*url, "/"), int(win.Seconds())))
		if err != nil {
			return fail(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			return fail(fmt.Errorf("GET /stats: HTTP %d", resp.StatusCode))
		}
		if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
			return fail(err)
		}
	} else {
		s, err := store.Open(*logPath)
		if err != nil {
			return fail(err)
		}
		b, _ := json.Marshal(s.Stats(win)) // same shape as over HTTP
		_ = json.Unmarshal(b, &st)
	}
	if *asJSON {
		b, _ := json.MarshalIndent(st, "", "  ")
		fmt.Fprintln(stdout, string(b))
		return 0
	}
	num := func(k string) float64 { f, _ := st[k].(float64); return f }
	n := num("decisions")
	fmt.Fprintf(stdout, "window %s: %d decisions (%.1f/h), error rate %.3f, cost $%.4f\n", *winS, int(n), num("per_hour"), num("error_rate"), num("cost_usd"))
	share := 0.0
	if n > 0 {
		share = num("reused") / n
	}
	fmt.Fprintf(stdout, "reused   %d (verdict copied from an earlier head with the same patch_id; no model call; %.1f%% of decisions)\n", int(num("reused")), 100*share)
	fmt.Fprintf(stdout, "debounced %d (heads superseded by a later push inside the debounce window; never decided)\n", int(num("debounced")))
	for _, k := range []string{"verdicts", "routes", "rules"} {
		m, _ := st[k].(map[string]any)
		keys := make([]string, 0, len(m))
		for v := range m {
			keys = append(keys, v)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, v := range keys {
			parts = append(parts, fmt.Sprintf("%s %v", v, m[v]))
		}
		fmt.Fprintf(stdout, "%-9s %s\n", k, strings.Join(parts, " · "))
	}
	if p50, ok := st["p50_ms"].(float64); ok {
		fmt.Fprintf(stdout, "latency  p50 %.0f ms · p99 %.0f ms\n", p50, num("p99_ms"))
	}
	return 0
}
