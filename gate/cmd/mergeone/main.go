// mergeone: one decision per merge, at swarm scale.
//
//	mergeone init    [--model jev] [--ci] [--mcp [--write]] [--all] [--force] [--dir .] [--quiet]
//	mergeone serve   --policy .mergeone.yaml [--log .mergeone/gatelog.jsonl] [--workers 16] [--addr :8787] [--mock]
//	mergeone pr      --policy .mergeone.yaml [--repo .] [--base main] [--head HEAD] [--gh-repo o/r --pr N --head-sha SHA]
//	mergeone decide  --policy .mergeone.yaml --state file.txt [--paths a.go,b.go]
//	mergeone replay  --policy .mergeone.yaml --predictions results/run2/predictions_jev_test_all.jsonl
//	mergeone loadtest --url http://localhost:8787 --rate 35 --n 1200
//	mergeone loadtest --mock --k 5 --rate 34.7 --n 600 [--pair-concurrency 8]   (in-process, synthetic memory)
//	mergeone mcp     --policy .mergeone.yaml [--mock]
//
//	mergeone         (on a terminal) the banner, then an interactive session with the same commands
//
// Exit codes for pr/decide: 0 pass, 1 hold, 2 block, 3 error (including a bad flag).
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/goccy/go-yaml"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/initcmd"
	"github.com/haalp/mergeone/gate/internal/mcp"
	"github.com/haalp/mergeone/gate/internal/memory"
	"github.com/haalp/mergeone/gate/internal/models"
	"github.com/haalp/mergeone/gate/internal/server"
	"github.com/haalp/mergeone/gate/internal/store"
)

var version = "dev"

// The process's streams and environment. Tests replace them; handlers write through stdout/stderr
// (not fmt.Print*) so a session and its tests see the same output.
var (
	stdin  io.Reader = os.Stdin
	stdout io.Writer = os.Stdout
	stderr io.Writer = os.Stderr
	getenv           = os.Getenv
	// interactive reports whether bare `mergeone` opens a session: stdin and stdout are both TTYs.
	// Tests force it (they have no TTY).
	interactive = func() bool { return isTTY(stdin) && isTTY(stdout) }
)

func main() {
	os.Exit(run(os.Args[1:]))
}

// commands are the subcommands shared by the one-shot CLI and the session. Each returns the process
// exit code instead of exiting, so a session survives a verdict, a bad flag or an error.
var commands = map[string]func([]string) int{
	"init":     cmdInit,
	"serve":    cmdServe,
	"pr":       cmdPR,
	"decide":   cmdDecide,
	"replay":   cmdReplay,
	"loadtest": cmdLoadtest,
	"index":    cmdIndex,
	"mcp":      cmdMCP,
	"models":   cmdModels,
}

// run is the whole CLI for args (os.Args[1:]); it returns the exit code.
func run(args []string) int {
	if len(args) == 0 || (len(args) == 1 && args[0] == "--no-session") {
		initcmd.PrintBanner(stdout)
		if len(args) == 0 && interactive() {
			return runSession()
		}
		usage()
		return 2
	}
	switch args[0] {
	case "help", "-h", "--help":
		initcmd.PrintBanner(stdout)
		fmt.Fprintln(stdout, usageLine)
		return 0
	case "version", "--version", "-v":
		printVersion()
		return 0
	}
	cmd, ok := commands[args[0]]
	if !ok {
		usage()
		return 2
	}
	return cmd(args[1:])
}

const usageLine = "usage: mergeone <init|serve|pr|decide|replay|loadtest|index|models|mcp|version> [flags]"

func usage() {
	fmt.Fprintln(stderr, usageLine)
}

func printVersion() { fmt.Fprintln(stdout, "mergeone", version) }

// fail reports err the way the CLI always has ("error: …") and returns exit code 3.
func fail(err error) int {
	fmt.Fprintln(stderr, "error:", err)
	return 3
}

// parseFlags parses args into fs without exiting. It returns ok=false with the code to return:
// 0 for -h/--help, bad (2 for most commands, as flag.ExitOnError did) for anything else.
func parseFlags(fs *flag.FlagSet, args []string, bad int) (code int, ok bool) {
	fs.SetOutput(stderr)
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0, false
		}
		return bad, false
	}
	return 0, true
}

// interruptContext is cancelled by Ctrl-C or SIGTERM. While it is live the signal does not kill the
// process, which is what lets a foreground command inside a session return to the prompt.
func interruptContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}

func cmdInit(args []string) int {
	return initcmd.Run(args, stdout, stderr)
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
	if mock || p.Model == "mock" {
		m = &models.Mock{LatencyMS: 300, JitterMS: 60}
	} else {
		m, err = buildModel(p, assetsDir)
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
	g := &engine.Gate{Policy: p, Model: m, Cal: cal, PairK: p.Memory.TopK}
	if p.Memory.Enabled {
		switch mm := m.(type) {
		case *models.Jev:
			if err := mm.AddQuestions(findAsset(assetsDir, "jev_pair.json")); err != nil {
				return nil, fmt.Errorf("memory enabled but pair questions missing: %w", err)
			}
		case *models.Haiku:
			if err := mm.SetPairPrompt(findAsset(assetsDir, "haiku_pair.md")); err != nil {
				return nil, fmt.Errorf("memory enabled but pair prompt missing: %w", err)
			}
		}
		mem, err := memory.Open(filepath.Join(filepath.Dir(policyPath), ".mergeone", "memory"))
		if err != nil {
			return nil, err
		}
		g.Memory = mem
	}
	return g, nil
}

// buildModel resolves policy.model against prompts/models.yaml (+ an optional inline `provider:` block)
// and instantiates it. Any systemone/decide/anthropic-dialect model works without a code change.
func buildModel(p *engine.Policy, assetsDir string) (engine.Model, error) {
	var spec *models.Spec
	regPath := findAsset(assetsDir, "models.yaml")
	if reg, err := models.LoadRegistry(regPath); err == nil {
		spec = reg.Models[p.Model]
	} else if len(p.Provider) == 0 {
		return nil, fmt.Errorf("model %q: no models.yaml found near %s and no provider: block in the policy (%v)", p.Model, assetsDir, err)
	}
	if len(p.Provider) > 0 { // inline override / definition
		if spec == nil {
			spec = &models.Spec{Name: p.Model, Dialect: "systemone"}
		}
		b, _ := yaml.Marshal(p.Provider)
		if err := yaml.Unmarshal(b, spec); err != nil {
			return nil, fmt.Errorf("policy provider: %w", err)
		}
		spec.Endpoint, spec.ModelID = models.ExpandEnv(spec.Endpoint), models.ExpandEnv(spec.ModelID)
	}
	if spec == nil {
		return nil, fmt.Errorf("unknown model %q: add it to prompts/models.yaml or give a provider: block", p.Model)
	}
	return models.Build(spec, findAsset(assetsDir, "jev_"+p.QuestionSet+".json"), findAsset(assetsDir, "haiku_"+p.QuestionSet+".md"), p.ModelID)
}

// cmdModels prints the decision-model registry the gateway will resolve `model:` against.
func cmdModels(args []string) int {
	fs := flag.NewFlagSet("models", flag.ContinueOnError)
	assets := fs.String("assets", ".", "directory holding prompts/models.yaml (or the file's own directory)")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	path := findAsset(*assets, "models.yaml")
	reg, err := models.LoadRegistry(path)
	if err != nil {
		return fail(err)
	}
	names := reg.Names()
	sort.Strings(names)
	fmt.Fprintf(stdout, "%-16s %-10s %-28s %8s  %-34s %s\n", "name", "dialect", "model_id", "$/M in", "weights", "key")
	fmt.Fprintf(stdout, "%-16s %-10s %-28s %8s  %-34s %s\n", "mock", "builtin", "-", "0.000", "-", "-")
	for _, n := range names {
		s := reg.Models[n]
		w := s.Weights
		if w == "" {
			w = "-"
		}
		k := s.KeyEnv
		if s.KeyOptional {
			k += " (optional)"
		}
		fmt.Fprintf(stdout, "%-16s %-10s %-28.28s %8.3f  %-34.34s %s\n", n, s.Dialect, s.ModelID, s.InputPerMTok, w, k)
	}
	fmt.Fprintf(stdout, "\n(%s; recorded_on %s)\n", path, reg.RecordedOn)
	return 0
}

func memoryOf(g *engine.Gate) *memory.Store {
	if m, ok := g.Memory.(*memory.Store); ok {
		return m
	}
	return nil
}

func cmdIndex(args []string) int {
	fs := flag.NewFlagSet("index", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file (memory dir is <policy dir>/.mergeone/memory)")
	days := fs.Int("days", 180, "how far back to index")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if fs.NArg() < 1 {
		return fail(fmt.Errorf("usage: mergeone index owner/name [--days 180]"))
	}
	mem, err := memory.Open(filepath.Join(filepath.Dir(*policy), ".mergeone", "memory"))
	if err != nil {
		return fail(err)
	}
	gh := server.NewGitHub(os.Getenv("GITHUB_TOKEN"))
	if gh == nil {
		return fail(fmt.Errorf("GITHUB_TOKEN required"))
	}
	for _, repo := range fs.Args() {
		t0 := time.Now()
		n, err := gh.Index(mem, repo, time.Now().AddDate(0, 0, -*days), func(n int) {
			if n%500 == 0 {
				fmt.Fprintf(stderr, "  %s: %d PRs…\n", repo, n)
			}
		})
		if err != nil {
			return fail(fmt.Errorf("%s: %w (indexed %d)", repo, err, n))
		}
		fmt.Fprintf(stdout, "%s: %d PRs indexed in %s (memory now %d)\n", repo, n, time.Since(t0).Round(time.Second), mem.Repo(repo).Len())
	}
	return 0
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

func cmdServe(args []string) int {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	workers := fs.Int("workers", 16, "async workers")
	addr := fs.String("addr", ":8787", "listen address")
	mock := fs.Bool("mock", false, "use the mock model (no API key)")
	assets := fs.String("assets", "", "directory with prompts and calibration files (default: policy dir)")
	public := fs.String("public-url", os.Getenv("MERGEONE_PUBLIC_URL"), "public base URL for check-run details links")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return fail(err)
	}
	st, err := store.Open(*logPath)
	if err != nil {
		return fail(err)
	}
	gw := server.New(g, st, server.NewGitHub(os.Getenv("GITHUB_TOKEN")), *workers, *public)
	gw.Memory = memoryOf(g)
	ctx, cancel := interruptContext()
	defer cancel()
	if err := gw.Serve(ctx, *addr, os.Getenv("MERGEONE_WEBHOOK_SECRET")); err != nil {
		return fail(err)
	}
	return 0
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

// verdictCode prints res as JSON and returns its exit code: 0 pass, 1 hold, 2 block.
func verdictCode(res engine.Result) int {
	b, _ := json.MarshalIndent(res, "", "  ")
	fmt.Fprintln(stdout, string(b))
	switch res.Verdict {
	case "pass":
		return 0
	case "block":
		return 2
	default:
		return 1
	}
}

func cmdPR(args []string) int {
	fs := flag.NewFlagSet("pr", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	repo := fs.String("repo", ".", "git repository")
	base := fs.String("base", "main", "base ref")
	head := fs.String("head", "HEAD", "head ref")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	// CI mode: with policy actions.enabled, the verdict is written back to this PR (status, labels, comment,
	// reviewers). Defaults come from GitHub Actions (GITHUB_REPOSITORY, GITHUB_EVENT_PATH); GITHUB_TOKEN authorises.
	ghRepo := fs.String("gh-repo", os.Getenv("GITHUB_REPOSITORY"), "owner/name for verdict actions")
	prNum := fs.Int("pr", 0, "PR number for verdict actions (default: from GITHUB_EVENT_PATH)")
	headSHA := fs.String("head-sha", "", "PR head sha for verdict actions (default: from GITHUB_EVENT_PATH, else git rev-parse --head)")
	// A bad flag is an error (3), not 2: for pr/decide 2 means "block".
	if code, ok := parseFlags(fs, args, 3); !ok {
		return code
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return fail(err)
	}
	state, paths, err := stateFromGit(*repo, *base, *head)
	if err != nil {
		return fail(err)
	}
	res := g.Decide(state, paths)
	evPR, evSHA := prFromEvent(os.Getenv("GITHUB_EVENT_PATH"))
	if *prNum == 0 {
		*prNum = evPR
	}
	if *headSHA == "" {
		*headSHA = evSHA
	}
	if *headSHA == "" {
		if out, err := exec.Command("git", "-C", *repo, "rev-parse", *head).Output(); err == nil {
			*headSHA = strings.TrimSpace(string(out))
		}
	}
	res.Actions = server.ApplyActions(server.NewGitHub(os.Getenv("GITHUB_TOKEN")), g.Policy.Actions, *ghRepo, *prNum, *headSHA, res, "")
	for _, e := range res.Actions.Errors {
		fmt.Fprintln(stderr, "actions:", e)
	}
	if st, err := store.Open(*logPath); err == nil {
		_ = st.Record(store.Record{Repo: *repo, PR: *prNum, HeadSHA: *head, Source: "cli", Result: res, State: state})
	}
	return verdictCode(res)
}

// prFromEvent reads the PR number and head sha from a GitHub Actions pull_request event payload.
func prFromEvent(path string) (int, string) {
	if path == "" {
		return 0, ""
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return 0, ""
	}
	var ev struct {
		PullRequest struct {
			Number int `json:"number"`
			Head   struct {
				SHA string `json:"sha"`
			} `json:"head"`
		} `json:"pull_request"`
	}
	if json.Unmarshal(b, &ev) != nil {
		return 0, ""
	}
	return ev.PullRequest.Number, ev.PullRequest.Head.SHA
}

func cmdDecide(args []string) int {
	fs := flag.NewFlagSet("decide", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	stateFile := fs.String("state", "", "file with the rendered PR state")
	pathsCSV := fs.String("paths", "", "comma-separated changed paths")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	if code, ok := parseFlags(fs, args, 3); !ok {
		return code
	}
	if *stateFile == "" {
		return fail(fmt.Errorf("--state is required"))
	}
	b, err := os.ReadFile(*stateFile)
	if err != nil {
		return fail(err)
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return fail(err)
	}
	var paths []string
	if *pathsCSV != "" {
		paths = strings.Split(*pathsCSV, ",")
	}
	return verdictCode(g.Decide(string(b), paths))
}

// replay dry-runs a policy over saved gatebench predictions: no API calls.
func cmdReplay(args []string) int {
	fs := flag.NewFlagSet("replay", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	preds := fs.String("predictions", "", "gatebench predictions_*.jsonl")
	assets := fs.String("assets", "", "assets dir")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	if *preds == "" {
		return fail(fmt.Errorf("--predictions is required"))
	}
	g, err := buildGate(*policy, true, *assets)
	if err != nil {
		return fail(err)
	}
	f, err := os.Open(*preds)
	if err != nil {
		return fail(err)
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
	fmt.Fprintf(stdout, "%d PRs\n", total)
	for _, k := range keys {
		fmt.Fprintf(stdout, "  %6d  %5.1f%%  %-5s  %-22s  %s\n", counts[k], 100*float64(counts[k])/float64(total), k.v, k.r, k.rule)
	}
	return 0
}

func cmdLoadtest(args []string) int {
	fs := flag.NewFlagSet("loadtest", flag.ContinueOnError)
	url := fs.String("url", "http://localhost:8787", "gateway URL")
	rate := fs.Float64("rate", 35, "decisions per second (30k/day = 0.35)")
	n := fs.Int("n", 1200, "requests")
	mock := fs.Bool("mock", false, "in-process: mock model (300±60 ms) + synthetic PR memory, no gateway; prints a per-decision breakdown")
	policy := fs.String("policy", "", "policy for --mock (default: policy.yaml found near the working directory)")
	k := fs.Int("k", 5, "--mock: candidates per PR (pair calls)")
	memN := fs.Int("memory", 200, "--mock: synthetic PRs in memory")
	pairConc := fs.Int("pair-concurrency", 0, "--mock: Gate.PairConcurrency (0 = default 8, 1 = sequential)")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	// One-shot, Ctrl-C kills the load test as it always has; in a session it stops the run, prints the
	// report for the requests issued so far, and returns to the prompt.
	ctx := context.Background()
	if inSession() {
		var stop context.CancelFunc
		ctx, stop = interruptContext()
		defer stop()
	}
	var err error
	if *mock {
		pp := *policy
		if pp == "" {
			pp = findAsset(".", "policy.yaml")
		}
		err = loadtestMock(ctx, pp, *rate, *n, *k, *memN, func(g *engine.Gate) { g.PairConcurrency = *pairConc })
	} else {
		err = server.LoadtestContext(ctx, *url, *rate, *n)
	}
	if err != nil {
		return fail(err)
	}
	return 0
}

func cmdMCP(args []string) int {
	fs := flag.NewFlagSet("mcp", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	mock := fs.Bool("mock", false, "mock model")
	assets := fs.String("assets", "", "assets dir")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return fail(err)
	}
	st, err := store.Open(*logPath)
	if err != nil {
		return fail(err)
	}
	gw := server.New(g, st, server.NewGitHub(os.Getenv("GITHUB_TOKEN")), 4, "")
	gw.Memory = memoryOf(g)
	defer gw.Stop()
	if inSession() {
		// The session owns stdin: lines are forwarded to the MCP server until Ctrl-C (or Ctrl-D).
		ctx, stop := interruptContext()
		in, release := activeSession.stdinUntil(ctx)
		err = mcp.ServeIO(ctx, gw, stateFromGit, in, stdout)
		stop()
		release()
	} else {
		err = mcp.Serve(gw, stateFromGit)
	}
	if err != nil {
		return fail(err)
	}
	return 0
}
