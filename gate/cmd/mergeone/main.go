// mergeone: one decision per merge, at swarm scale.
//
//	mergeone init    [--model jev] [--ci] [--mcp [--write]] [--all] [--force] [--dir .] [--quiet]
//	mergeone serve   --policy .mergeone.yaml [--log .mergeone/gatelog.jsonl] [--workers 16] [--addr :8787] [--mock]
//	mergeone pr      --policy .mergeone.yaml [--repo .] [--base main] [--head HEAD] [--gh-repo o/r --pr N --head-sha SHA]
//	mergeone pr      o/r N [--head-sha SHA] [flags]   (= --gh-repo o/r --pr N; with GITHUB_TOKEN the PR is read from GitHub)
//	mergeone decide  --policy .mergeone.yaml --state file.txt [--paths a.go,b.go]
//	mergeone replay  --policy .mergeone.yaml --predictions results/run2/predictions_jev_test_all.jsonl
//	mergeone loadtest --url http://localhost:8787 --rate 35 --n 1200
//	mergeone loadtest --mock --k 5 --rate 34.7 --n 600 [--pair-concurrency 8]   (in-process, synthetic memory)
//	mergeone mcp     --policy .mergeone.yaml [--mock]
//	mergeone drift-replay --predictions then.jsonl[,more] --predictions now.jsonl[,more] [--qset v3] [--mode joint|any] [--json]
//	                      [--labels data/run3/dataset.jsonl --label iterated]
//	mergeone calibrate --from-gatelog [--since 28d] [--model m] [--label iterated] [--out calibration_<m>_<qset>.json] [--force]
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
	"github.com/haalp/mergeone/gate/internal/portal"
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
	"init":         cmdInit,
	"serve":        cmdServe,
	"pr":           cmdPR,
	"decide":       cmdDecide,
	"replay":       cmdReplay,
	"loadtest":     cmdLoadtest,
	"index":        cmdIndex,
	"mcp":          cmdMCP,
	"models":       cmdModels,
	"drift-replay": cmdDriftReplay,
	"calibrate":    cmdCalibrate,
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

const usageLine = "usage: mergeone <init|serve|pr|decide|replay|drift-replay|calibrate|loadtest|index|models|mcp|version> [flags]"

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
	calPath := ""
	switch p.Calibration {
	case "", "auto":
		calPath = findAsset(assetsDir, fmt.Sprintf("calibration_%s_%s.json", p.Model, p.QuestionSet))
	case "none":
	default:
		calPath = p.Calibration
	}
	if err := engine.CheckQuestionSet(p.QuestionSet, calPath); err != nil {
		return nil, fmt.Errorf("policy %s: %w", policyPath, err)
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

// stateFromGit renders base...head of a local clone. The title in the state is head's commit subject: a stand-in
// the model reads, never the PR title (see gitContext).
func stateFromGit(repo, base, head string) (string, []server.FileStat, error) {
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
	for _, line := range strings.Split(strings.TrimSpace(numstat), "\n") {
		parts := strings.Split(line, "\t")
		if len(parts) != 3 {
			continue
		}
		ins, _ := strconv.Atoi(parts[0])
		del, _ := strconv.Atoi(parts[1])
		files = append(files, server.FileStat{Path: parts[2], Insertions: ins, Deletions: del})
	}
	diff, _ := git("diff", base+"..."+head, "--no-color")
	return server.RenderState(strings.TrimSpace(title), body, files, diff), files, nil
}

// stateFromGitPaths is stateFromGit with the changed paths (the MCP tools' StateFn).
func stateFromGitPaths(repo, base, head string) (string, []string, error) {
	state, files, err := stateFromGit(repo, base, head)
	return state, pathsOf(files), err
}

func pathsOf(files []server.FileStat) []string {
	var paths []string
	for _, f := range files {
		paths = append(paths, f.Path)
	}
	return paths
}

// gitContext is Record.Context from what a clone can tell for certain: the base branch (only when base names a
// branch) and the diff size. Title, author and created_at stay empty: a commit subject or commit author is not
// the PR's, and the portal shows "unknown" rather than a wrong name.
func gitContext(repo, base string, files []server.FileStat) map[string]any {
	ref := ""
	if out, err := exec.Command("git", "-C", repo, "rev-parse", "--verify", "--quiet", "--symbolic-full-name", base).Output(); err == nil {
		ref = strings.TrimSpace(string(out))
	}
	switch {
	case strings.HasPrefix(ref, "refs/heads/"):
		ref = strings.TrimPrefix(ref, "refs/heads/")
	case strings.HasPrefix(ref, "refs/remotes/"):
		ref = strings.TrimPrefix(ref, "refs/remotes/")
		if _, b, ok := strings.Cut(ref, "/"); ok {
			ref = b
		} else {
			ref = ""
		}
	default:
		ref = ""
	}
	if files == nil {
		files = []server.FileStat{}
	}
	return server.PRContext(engine.PRMeta{Base: ref}, files)
}

// newGitHub is the CLI's GitHub client: GITHUB_TOKEN authorises, GITHUB_API_URL (set by GitHub Actions, and on
// GitHub Enterprise) overrides the API base. Nil without a token.
func newGitHub() *server.GitHub {
	gh := server.NewGitHub(getenv("GITHUB_TOKEN"))
	if gh != nil {
		if u := strings.TrimRight(getenv("GITHUB_API_URL"), "/"); u != "" {
			gh.API = u
		}
	}
	return gh
}

// prPositional takes `owner/name N` off the front of args (sugar for --gh-repo owner/name --pr N) and returns
// the remaining args; repo is "" when args do not start with that pair.
func prPositional(args []string) (repo string, pr int, rest []string) {
	if len(args) < 2 {
		return "", 0, args
	}
	a := args[0]
	n, err := strconv.Atoi(strings.TrimPrefix(args[1], "#"))
	owner, name, ok := strings.Cut(a, "/")
	if err != nil || n <= 0 || !ok || owner == "" || name == "" || strings.Contains(name, "/") || strings.HasPrefix(a, "-") {
		return "", 0, args
	}
	return a, n, args[2:]
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
	// With a PR (owner/name N, --gh-repo/--pr, or GitHub Actions' GITHUB_REPOSITORY/GITHUB_EVENT_PATH) and
	// GITHUB_TOKEN, the PR is fetched from GitHub as the webhook does: title, body, author, base, files and diff for
	// that head sha. With policy actions.enabled the verdict is also written back to the PR (status, labels,
	// comment, reviewers).
	ghRepo := fs.String("gh-repo", getenv("GITHUB_REPOSITORY"), "owner/name of the PR (also: mergeone pr owner/name N)")
	prNum := fs.Int("pr", 0, "PR number (default: from GITHUB_EVENT_PATH)")
	headSHA := fs.String("head-sha", "", "PR head sha (default: from GITHUB_EVENT_PATH, else the PR's head on GitHub, else git rev-parse --head)")
	// `mergeone pr owner/name N [flags]` or `mergeone pr [flags] owner/name N`.
	badArg := func() int {
		return fail(fmt.Errorf("unexpected argument %q (usage: mergeone pr [owner/name N] [flags])", fs.Arg(0)))
	}
	posRepo, posPR, rest := prPositional(args)
	// A bad flag is an error (3), not 2: for pr/decide 2 means "block".
	if code, ok := parseFlags(fs, rest, 3); !ok {
		return code
	}
	if fs.NArg() > 0 {
		if posRepo != "" {
			return badArg()
		}
		if posRepo, posPR, rest = prPositional(fs.Args()); posRepo == "" {
			return badArg()
		}
		if code, ok := parseFlags(fs, rest, 3); !ok {
			return code
		}
		if fs.NArg() > 0 {
			return badArg()
		}
	}
	if posRepo != "" {
		*ghRepo, *prNum = posRepo, posPR
	}
	g, err := buildGate(*policy, *mock, *assets)
	if err != nil {
		return fail(err)
	}
	evPR, evSHA := prFromEvent(getenv("GITHUB_EVENT_PATH"))
	if *prNum == 0 {
		*prNum = evPR
	}
	if *headSHA == "" && *prNum == evPR {
		*headSHA = evSHA
	}
	gh := newGitHub()
	var (
		state      string
		paths      []string
		rctx       map[string]any
		meta       engine.PRMeta
		fromGitHub bool
	)
	if *ghRepo != "" && *prNum > 0 && gh != nil {
		st, ps, sha, pm, files, err := gh.PRStateMetaAt(*ghRepo, *prNum, *headSHA)
		if err != nil {
			fmt.Fprintf(stderr, "mergeone: could not fetch %s#%d from GitHub (%v); rendering the local diff, context is partial\n", *ghRepo, *prNum, err)
		} else {
			state, paths, *headSHA, meta, fromGitHub = st, ps, sha, pm, true
			rctx = server.PRContext(pm, files)
		}
	} else if *ghRepo != "" && *prNum > 0 {
		fmt.Fprintf(stderr, "mergeone: no GITHUB_TOKEN; rendering the local diff for %s#%d, context is partial (no title, author or created_at)\n", *ghRepo, *prNum)
	}
	if !fromGitHub {
		st, files, err := stateFromGit(*repo, *base, *head)
		if err != nil {
			return fail(err)
		}
		state, paths = st, pathsOf(files)
		if *ghRepo != "" && *prNum > 0 { // a named PR: a context that says what git knows (no title rather than a wrong one)
			rctx = gitContext(*repo, *base, files)
		}
		if *headSHA == "" {
			if out, err := exec.Command("git", "-C", *repo, "rev-parse", *head).Output(); err == nil {
				*headSHA = strings.TrimSpace(string(out))
			}
		}
	}
	// With a known PR the gatelog is keyed by (owner/name, PR, head sha) so max_rounds can count rounds.
	logRepo, logSHA := *repo, *head
	knownPR := *ghRepo != "" && *prNum > 0 && *headSHA != ""
	if knownPR {
		logRepo, logSHA = *ghRepo, *headSHA
	}
	var res engine.Result
	if fromGitHub {
		res = g.DecideFor(state, meta) // as the webhook decides a fetched PR (memory, silent_days)
	} else {
		res = g.Decide(state, paths)
	}
	st, stErr := store.Open(*logPath)
	if stErr == nil && knownPR && g.Policy.MaxRounds > 0 {
		g.ApplyMaxRounds(&res, st.NonPassRounds(logRepo, *prNum, logSHA))
	}
	res.Actions = server.ApplyActions(gh, g.Policy.Actions, *ghRepo, *prNum, *headSHA, res, "")
	for _, e := range res.Actions.Errors {
		fmt.Fprintln(stderr, "actions:", e)
	}
	if stErr == nil {
		// policy portal: {url, token} → the record is also posted to the portal (bounded wait before exit).
		if sink := portal.New(g.Policy.Portal.URL, g.Policy.Portal.Token); sink != nil {
			st.SetOnRecord(func(r store.Record) { sink.Enqueue(r) })
			defer sink.Close(10 * time.Second)
		}
		_ = st.Record(store.Record{Repo: logRepo, PR: *prNum, HeadSHA: logSHA, Source: "cli", Result: res, Context: rctx, State: state})
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
		err = mcp.ServeIO(ctx, gw, stateFromGitPaths, in, stdout)
		stop()
		release()
	} else {
		err = mcp.Serve(gw, stateFromGitPaths)
	}
	if err != nil {
		return fail(err)
	}
	return 0
}
