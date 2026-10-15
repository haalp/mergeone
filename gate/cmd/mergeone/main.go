// mergeone: one decision per merge, at swarm scale.
//
//	mergeone serve   --policy .mergeone.yaml [--log .mergeone/gatelog.jsonl] [--workers 16] [--addr :8787] [--mock]
//	mergeone pr      --policy .mergeone.yaml [--repo .] [--base main] [--head HEAD]
//	mergeone decide  --policy .mergeone.yaml --state file.txt [--paths a.go,b.go]
//	mergeone replay  --policy .mergeone.yaml --predictions results/run2/predictions_jev_test_all.jsonl
//	mergeone loadtest --url http://localhost:8787 --rate 35 --n 1200
//	mergeone mcp     --policy .mergeone.yaml [--mock]
//
// Exit codes for pr/decide: 0 pass, 1 hold, 2 block, 3 error.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/mcp"
	"github.com/haalp/mergeone/gate/internal/models"
	"github.com/haalp/mergeone/gate/internal/server"
	"github.com/haalp/mergeone/gate/internal/store"
)

var version = "dev"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "serve":
		err = cmdServe(os.Args[2:])
	case "pr":
		err = cmdPR(os.Args[2:])
	case "decide":
		err = cmdDecide(os.Args[2:])
	case "replay":
		err = cmdReplay(os.Args[2:])
	case "loadtest":
		err = cmdLoadtest(os.Args[2:])
	case "mcp":
		err = cmdMCP(os.Args[2:])
	case "version", "--version", "-v":
		fmt.Println("mergeone", version)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(3)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: mergeone <serve|pr|decide|replay|loadtest|mcp|version> [flags]")
}

// buildGate wires policy + model + calibration. promptsDir holds jev_v2.json / haiku_v2.md;
// calDir holds calibration_<model>_<qset>.json (both default to the directory of the policy file).
func buildGate(policyPath string, mock bool, assetsDir string) (*engine.Gate, error) {
	p, err := engine.LoadPolicy(policyPath)
	if err != nil {
		return nil, err
	}
	if assetsDir == "" {
		assetsDir = filepath.Dir(policyPath)
	}
	var m engine.Model
	switch {
	case mock || p.Model == "mock":
		m = &models.Mock{LatencyMS: 300, JitterMS: 60}
	case p.Model == "jev":
		m, err = models.NewJev(findAsset(assetsDir, "jev_"+p.QuestionSet+".json"))
	case p.Model == "haiku":
		m, err = models.NewHaiku(findAsset(assetsDir, "haiku_"+p.QuestionSet+".md"))
	default:
		err = fmt.Errorf("unknown model %q", p.Model)
	}
	if err != nil {
		return nil, err
	}
	calPath := ""
	switch p.Calibration {
	case "", "auto":
		calPath = findAsset(assetsDir, fmt.Sprintf("calibration_%s_%s.json", p.Model, p.QuestionSet))
	case "none":
	default:
		calPath = p.Calibration
	}
	cal, err := engine.LoadCalibration(calPath)
	if err != nil {
		return nil, err
	}
	return &engine.Gate{Policy: p, Model: m, Cal: cal}, nil
}

// findAsset looks in dir, dir/.mergeone, dir/prompts, dir/mergeone/gate and the executable's dir.
func findAsset(dir, name string) string {
	exe, _ := os.Executable()
	home, _ := os.UserHomeDir()
	for _, d := range []string{dir, filepath.Join(dir, ".mergeone"), filepath.Join(dir, "prompts"), filepath.Join(dir, "mergeone", "gate"), filepath.Join(dir, "assets"), filepath.Dir(exe), filepath.Join(filepath.Dir(exe), "assets"), filepath.Join(home, ".mergeone")} {
		p := filepath.Join(d, name)
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return filepath.Join(dir, name) // may not exist; callers handle
}

func cmdServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	workers := fs.Int("workers", 16, "async workers")
	addr := fs.String("addr", ":8787", "listen address")
	mock := fs.Bool("mock", false, "use the mock model (no API key)")
	assets := fs.String("assets", "", "directory with prompts and calibration files (default: policy dir)")
	public := fs.String("public-url", os.Getenv("MERGEONE_PUBLIC_URL"), "public base URL for check-run details links")
	fs.Parse(args)
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return err
	}
	st, err := store.Open(*logPath)
	if err != nil {
		return err
	}
	gw := server.New(g, st, server.NewGitHub(os.Getenv("GITHUB_TOKEN")), *workers, *public)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return gw.Serve(ctx, *addr, os.Getenv("MERGEONE_WEBHOOK_SECRET"))
}

func stateFromGit(repo, base, head string) (string, []string, error) {
	git := func(a ...string) (string, error) {
		out, err := exec.Command("git", append([]string{"-C", repo}, a...)...).Output()
		return string(out), err
	}
	title, err := git("log", "-1", "--format=%s", head)
	if err != nil {
		return "", nil, fmt.Errorf("git log: %w", err)
	}
	body, _ := git("log", "-1", "--format=%b", head)
	numstat, _ := git("diff", "--numstat", base+"..."+head)
	var files []server.FileStat
	var paths []string
	for _, line := range strings.Split(strings.TrimSpace(numstat), "\n") {
		parts := strings.Split(line, "\t")
		if len(parts) != 3 {
			continue
		}
		ins, _ := strconv.Atoi(parts[0])
		del, _ := strconv.Atoi(parts[1])
		files = append(files, server.FileStat{Path: parts[2], Insertions: ins, Deletions: del})
		paths = append(paths, parts[2])
	}
	diff, _ := git("diff", base+"..."+head, "--no-color")
	return server.RenderState(strings.TrimSpace(title), body, files, diff), paths, nil
}

func exitFor(res engine.Result) {
	b, _ := json.MarshalIndent(res, "", "  ")
	fmt.Println(string(b))
	switch res.Verdict {
	case "pass":
		os.Exit(0)
	case "block":
		os.Exit(2)
	default:
		os.Exit(1)
	}
}

func cmdPR(args []string) error {
	fs := flag.NewFlagSet("pr", flag.ExitOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	repo := fs.String("repo", ".", "git repository")
	base := fs.String("base", "main", "base ref")
	head := fs.String("head", "HEAD", "head ref")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	fs.Parse(args)
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return err
	}
	state, paths, err := stateFromGit(*repo, *base, *head)
	if err != nil {
		return err
	}
	res := g.Decide(state, paths)
	if st, err := store.Open(*logPath); err == nil {
		_ = st.Record(store.Record{Repo: *repo, HeadSHA: *head, Source: "cli", Result: res, State: state})
	}
	exitFor(res)
	return nil
}

func cmdDecide(args []string) error {
	fs := flag.NewFlagSet("decide", flag.ExitOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	stateFile := fs.String("state", "", "file with the rendered PR state")
	pathsCSV := fs.String("paths", "", "comma-separated changed paths")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	fs.Parse(args)
	if *stateFile == "" {
		return fmt.Errorf("--state is required")
	}
	b, err := os.ReadFile(*stateFile)
	if err != nil {
		return err
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return err
	}
	var paths []string
	if *pathsCSV != "" {
		paths = strings.Split(*pathsCSV, ",")
	}
	exitFor(g.Decide(string(b), paths))
	return nil
}

// replay dry-runs a policy over saved gatebench predictions: no API calls.
func cmdReplay(args []string) error {
	fs := flag.NewFlagSet("replay", flag.ExitOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	preds := fs.String("predictions", "", "gatebench predictions_*.jsonl")
	assets := fs.String("assets", "", "assets dir")
	fs.Parse(args)
	if *preds == "" {
		return fmt.Errorf("--predictions is required")
	}
	g, err := buildGate(*policy, true, *assets)
	if err != nil {
		return err
	}
	f, err := os.Open(*preds)
	if err != nil {
		return err
	}
	defer f.Close()
	type key struct{ v, r, rule string }
	counts := map[key]int{}
	total := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var row struct {
			Decision struct {
				Probs map[string]float64 `json:"probs"`
				Error *string            `json:"error"`
			} `json:"decision"`
		}
		if json.Unmarshal(sc.Bytes(), &row) != nil || row.Decision.Error != nil {
			continue
		}
		rule, out := g.Match(g.Cal.Apply(row.Decision.Probs))
		counts[key{out.Verdict, out.Route, rule}]++
		total++
	}
	keys := make([]key, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return counts[keys[i]] > counts[keys[j]] })
	fmt.Printf("%d PRs\n", total)
	for _, k := range keys {
		fmt.Printf("  %6d  %5.1f%%  %-5s  %-22s  %s\n", counts[k], 100*float64(counts[k])/float64(total), k.v, k.r, k.rule)
	}
	return nil
}

func cmdLoadtest(args []string) error {
	fs := flag.NewFlagSet("loadtest", flag.ExitOnError)
	url := fs.String("url", "http://localhost:8787", "gateway URL")
	rate := fs.Float64("rate", 35, "decisions per second (30k/day = 0.35)")
	n := fs.Int("n", 1200, "requests")
	fs.Parse(args)
	return server.Loadtest(*url, *rate, *n)
}

func cmdMCP(args []string) error {
	fs := flag.NewFlagSet("mcp", flag.ExitOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	fs.Parse(args)
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return err
	}
	st, err := store.Open(*logPath)
	if err != nil {
		return err
	}
	gw := server.New(g, st, server.NewGitHub(os.Getenv("GITHUB_TOKEN")), 4, "")
	defer gw.Stop()
	return mcp.Serve(gw, stateFromGit)
}

var _ = time.Now
