// Package initcmd implements `mergeone init`: detect the repository, write .mergeone.yaml (and
// optionally a CI workflow and the CLAUDE.md snippet), and print what to do next.
package initcmd

import (
	"bytes"
	"embed"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"text/template"

	"github.com/haalp/mergeone/gate/internal/models"
)

// Exit codes.
const (
	ExitOK      = 0
	ExitRefused = 1 // a target file exists and --force was not given
	ExitUsage   = 2 // bad flags or unknown model
	ExitError   = 3 // I/O or other failure
)

const (
	PolicyFile   = ".mergeone.yaml"
	WorkflowFile = ".github/workflows/mergeone.yml"
	ClaudeFile   = "CLAUDE.md"
	ClaudeMarker = "<!-- mergeone:init -->"
	ReadmeURL    = "https://github.com/haalp/mergeone#readme"
	MCPCommand   = "claude mcp add mergeone -- mergeone mcp --policy .mergeone.yaml"
)

//go:embed templates/policy.yaml.tmpl templates/workflow.yml.tmpl
var templatesFS embed.FS

var tmpl = template.Must(template.New("").Delims("[[", "]]").ParseFS(templatesFS, "templates/*.tmpl"))

// KeyEnvs are the model API keys init reports on (name → what it unlocks), in print order.
var KeyEnvs = []struct{ Env, For string }{
	{"TYPESAFE_API_KEY", "jev"},
	{"PERPLEXITY_API_KEY", "pplx, pplx-flash"},
	{"ANTHROPIC_API_KEY", "haiku"},
	{"GITHUB_TOKEN", "webhook / CI verdict actions"},
}

// fallbackRegistry is used only when no models.yaml can be found (e.g. `go install` without the release
// assets). TestFallbackMatchesRegistry keeps it equal to prompts/models.yaml.
var fallbackRegistry = map[string]models.Spec{
	"jev":              {KeyEnv: "TYPESAFE_API_KEY"},
	"pplx":             {KeyEnv: "PERPLEXITY_API_KEY"},
	"pplx-flash":       {KeyEnv: "PERPLEXITY_API_KEY"},
	"haiku":            {KeyEnv: "ANTHROPIC_API_KEY"},
	"clef":             {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"clef-flash":       {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"open-jev-27b":     {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"open-jev-9b":      {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"h2o-lightning-4b": {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"laya":             {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"pplx-flash-local": {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
	"local":            {KeyEnv: "MERGEONE_LOCAL_KEY", KeyOptional: true},
}

// Env is everything Run reads from the outside world, so tests can replace it.
type Env struct {
	Stdout, Stderr io.Writer
	Getenv         func(string) string
	Color          ColorMode
	// RegistryPath is the models.yaml to validate --model against; "" searches the usual places.
	RegistryPath string
}

// Options are the parsed flags.
type Options struct {
	Model                             string
	CI, MCP, Write, All, Force, Quiet bool
	Dir                               string
	Portal, PortalURL                 string // --portal <token> writes the portal: block
}

// DefaultPortalURL is the hosted portal (docs/portal.md).
const DefaultPortalURL = "https://app.mergeone.dev"

// Run is `mergeone init` for the real process: stdout/stderr, the process environment, TTY detection.
func Run(args []string, stdout, stderr io.Writer) int {
	return RunEnv(args, Env{Stdout: stdout, Stderr: stderr, Getenv: os.Getenv, Color: DetectColor(IsTerminal(stdout), os.Getenv)})
}

// ParseFlags parses init's flags. --all sets --ci --mcp --write; --write implies --mcp.
func ParseFlags(args []string, stderr io.Writer) (Options, error) {
	var o Options
	fs := flag.NewFlagSet("init", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&o.Model, "model", "jev", "decision model: any key in prompts/models.yaml (mergeone models)")
	fs.BoolVar(&o.CI, "ci", false, "also write "+WorkflowFile)
	fs.BoolVar(&o.MCP, "mcp", false, "print the MCP setup command and the CLAUDE.md snippet")
	fs.BoolVar(&o.Write, "write", false, "with --mcp: append the snippet to CLAUDE.md (idempotent)")
	fs.BoolVar(&o.All, "all", false, "= --ci --mcp --write")
	fs.BoolVar(&o.Force, "force", false, "overwrite existing files")
	fs.StringVar(&o.Dir, "dir", ".", "repository directory to initialise")
	fs.BoolVar(&o.Quiet, "quiet", false, "no banner")
	fs.StringVar(&o.Portal, "portal", "", "portal token: write the portal: block (every verdict is also posted to the portal)")
	fs.StringVar(&o.PortalURL, "portal-url", DefaultPortalURL, "with --portal: the portal's base URL")
	fs.Usage = func() {
		fmt.Fprintln(stderr, "usage: mergeone init [--model jev] [--ci] [--mcp [--write]] [--all] [--force] [--dir .] [--quiet] [--portal TOKEN [--portal-url URL]]")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if fs.NArg() > 0 {
		fs.Usage()
		return o, fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	if o.All {
		o.CI, o.MCP, o.Write = true, true, true
	}
	if o.Write {
		o.MCP = true
	}
	if o.Portal == "" {
		o.PortalURL = ""
	} else if strings.ContainsAny(o.Portal, "\"\\\n\r\t ") {
		return o, fmt.Errorf("--portal: the token has a character a YAML string cannot hold as written")
	} else if !strings.HasPrefix(o.PortalURL, "https://") && !strings.HasPrefix(o.PortalURL, "http://") {
		return o, fmt.Errorf("--portal-url %q: want an http(s) URL", o.PortalURL)
	}
	return o, nil
}

// Detection is what init found in the repository and environment.
type Detection struct {
	GitRepo        bool
	Remote         string // owner/name, "" when there is no usable origin
	DefaultBranch  string
	BranchAssumed  bool // origin/HEAD not set: DefaultBranch is the "main" fallback
	HasPolicy      bool
	HasWorkflow    bool
	HasClaude      bool
	ClaudeHasBlock bool
	Keys           map[string]bool
}

// Detect inspects dir. It never reads key values beyond "is it set".
func Detect(dir string, getenv func(string) string) Detection {
	d := Detection{DefaultBranch: "main", BranchAssumed: true, Keys: map[string]bool{}}
	git := func(a ...string) (string, bool) {
		out, err := exec.Command("git", append([]string{"-C", dir}, a...)...).Output()
		return strings.TrimSpace(string(out)), err == nil
	}
	if out, ok := git("rev-parse", "--is-inside-work-tree"); ok && out == "true" {
		d.GitRepo = true
		if url, ok := git("remote", "get-url", "origin"); ok {
			if r, ok := ParseRemote(url); ok {
				d.Remote = r
			}
		}
		if ref, ok := git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"); ok && strings.HasPrefix(ref, "origin/") && len(ref) > len("origin/") {
			d.DefaultBranch, d.BranchAssumed = strings.TrimPrefix(ref, "origin/"), false
		}
	}
	d.HasPolicy = exists(filepath.Join(dir, PolicyFile))
	d.HasWorkflow = exists(filepath.Join(dir, WorkflowFile))
	if b, err := os.ReadFile(filepath.Join(dir, ClaudeFile)); err == nil {
		d.HasClaude, d.ClaudeHasBlock = true, bytes.Contains(b, []byte(ClaudeMarker))
	}
	for _, k := range KeyEnvs {
		d.Keys[k.Env] = getenv(k.Env) != ""
	}
	return d
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// ParseRemote turns a git remote URL into "owner/name". It accepts scp-like ssh (git@host:o/r.git),
// ssh://, git://, http(s):// (with or without credentials, port, .git suffix, trailing slash).
// Nested paths (GitLab subgroups) keep everything but the last segment as the owner.
func ParseRemote(url string) (string, bool) {
	u := strings.TrimSpace(url)
	var path string
	if i := strings.Index(u, "://"); i >= 0 {
		rest := u[i+3:]
		j := strings.Index(rest, "/")
		if j < 0 {
			return "", false
		}
		path = rest[j+1:]
	} else if i := strings.Index(u, ":"); i > 0 && !strings.Contains(u[:i], "/") {
		path = u[i+1:] // scp-like: [user@]host:owner/name
	} else {
		return "", false // a local path, not a hosted remote
	}
	path = strings.Trim(path, "/")
	path = strings.TrimSuffix(path, ".git")
	path = strings.Trim(path, "/")
	parts := strings.Split(path, "/")
	if len(parts) < 2 {
		return "", false
	}
	for _, p := range parts {
		if p == "" {
			return "", false
		}
	}
	return path, true
}

// findRegistry looks for models.yaml where the gateway looks for its assets.
func findRegistry(dir string) string {
	exe, _ := os.Executable()
	home, _ := os.UserHomeDir()
	for _, d := range []string{dir, filepath.Join(dir, ".mergeone"), filepath.Join(dir, "prompts"), filepath.Dir(exe), filepath.Join(filepath.Dir(exe), "assets"), filepath.Join(home, ".mergeone")} {
		p := filepath.Join(d, "models.yaml")
		if exists(p) {
			return p
		}
	}
	return ""
}

// LoadModels returns the registry rows by name and where they came from ("" = built-in fallback list).
func LoadModels(path string) (map[string]models.Spec, string) {
	if path != "" {
		if reg, err := models.LoadRegistry(path); err == nil {
			out := make(map[string]models.Spec, len(reg.Models))
			for k, s := range reg.Models {
				out[k] = *s
			}
			return out, path
		}
	}
	return fallbackRegistry, ""
}

// PolicyData feeds templates/policy.yaml.tmpl.
type PolicyData struct {
	Model             string
	Local             bool
	RepoOrPlaceholder string
	PortalURL         string // "" = no portal: block
	PortalToken       string
}

// RenderPolicy renders the .mergeone.yaml template.
func RenderPolicy(d PolicyData) ([]byte, error) {
	if d.RepoOrPlaceholder == "" {
		d.RepoOrPlaceholder = "owner/name"
	}
	var b bytes.Buffer
	err := tmpl.ExecuteTemplate(&b, "policy.yaml.tmpl", d)
	return b.Bytes(), err
}

// WorkflowData feeds templates/workflow.yml.tmpl.
type WorkflowData struct {
	ActionsEnabled bool
	SecretEnvs     []string // env vars passed from repository secrets, besides GITHUB_TOKEN
}

// RenderWorkflow renders the GitHub Actions workflow.
func RenderWorkflow(d WorkflowData) ([]byte, error) {
	var b bytes.Buffer
	err := tmpl.ExecuteTemplate(&b, "workflow.yml.tmpl", d)
	return b.Bytes(), err
}

// secretEnvs is what the workflow passes to `mergeone pr` for a model: its key, and for a self-hosted
// row (key_optional) the endpoint override (a server on 127.0.0.1 is not reachable from a hosted runner).
func secretEnvs(s models.Spec) []string {
	var out []string
	if s.KeyEnv != "" && s.KeyEnv != "GITHUB_TOKEN" {
		out = append(out, s.KeyEnv)
	}
	if s.KeyOptional {
		out = append(out, "MERGEONE_LOCAL_ENDPOINT")
	}
	return out
}

// ClaudeSnippet is the three lines for CLAUDE.md (the marker is added when written).
func ClaudeSnippet(base string) string {
	return "## Merge gate (mergeone)\n" +
		fmt.Sprintf("Before merging, call gate_decide with repo_path=\".\" base=%q head=\"HEAD\".\n", base) +
		"Merge only on \"pass\". On \"hold\" or \"block\", open a PR and request the route named.\n"
}

// AppendClaude appends the snippet to path (creating it) unless the marker is already there.
// It reports whether it wrote anything.
func AppendClaude(path, base string) (bool, error) {
	old, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return false, err
	}
	if bytes.Contains(old, []byte(ClaudeMarker)) {
		return false, nil
	}
	var b bytes.Buffer
	b.Write(old)
	if len(old) > 0 {
		if !bytes.HasSuffix(old, []byte("\n")) {
			b.WriteString("\n")
		}
		b.WriteString("\n")
	}
	b.WriteString(ClaudeMarker + "\n" + ClaudeSnippet(base))
	return true, os.WriteFile(path, b.Bytes(), 0o644)
}

// printer writes the checklist with coloured status marks only.
type printer struct {
	w io.Writer
	p palette
}

func (pr printer) line(mark, label, detail string) {
	col := ""
	switch mark {
	case "✓":
		col = pr.p.ok
	case "✗":
		col = pr.p.bad
	case "·":
		col = pr.p.dim
	}
	reset := ""
	if col != "" {
		reset = pr.p.reset
	}
	if detail == "" {
		fmt.Fprintf(pr.w, "  %s%s%s %s\n", col, mark, reset, label)
		return
	}
	fmt.Fprintf(pr.w, "  %s%s%s %-31s %s\n", col, mark, reset, label, detail)
}

func mark(ok bool) string {
	if ok {
		return "✓"
	}
	return "·"
}

// RunEnv is Run with an explicit environment. It returns the process exit code.
func RunEnv(args []string, env Env) int {
	o, err := ParseFlags(args, env.Stderr)
	if errors.Is(err, flag.ErrHelp) {
		return ExitOK
	}
	if err != nil {
		fmt.Fprintln(env.Stderr, "error:", err)
		return ExitUsage
	}

	regPath := env.RegistryPath
	if regPath == "" {
		regPath = findRegistry(o.Dir)
	}
	reg, regFrom := LoadModels(regPath)
	spec, ok := reg[o.Model]
	if !ok {
		names := make([]string, 0, len(reg))
		for k := range reg {
			names = append(names, k)
		}
		sort.Strings(names)
		fmt.Fprintf(env.Stderr, "error: unknown model %q; want one of: %s\n", o.Model, strings.Join(names, ", "))
		return ExitUsage
	}
	if st, err := os.Stat(o.Dir); err != nil || !st.IsDir() {
		fmt.Fprintf(env.Stderr, "error: --dir %s is not a directory\n", o.Dir)
		return ExitUsage
	}

	out := env.Stdout
	if !o.Quiet {
		WriteBanner(out, env.Color)
	}
	pr := printer{out, paletteFor(env.Color)}

	d := Detect(o.Dir, env.Getenv)
	WriteDetected(out, d, env.Color, regFrom != "")

	// Refuse before writing anything, so a refusal never leaves a half-initialised repository.
	policyPath := filepath.Join(o.Dir, PolicyFile)
	workflowPath := filepath.Join(o.Dir, WorkflowFile)
	var clash []string
	if d.HasPolicy && !o.Force {
		clash = append(clash, PolicyFile)
	}
	if o.CI && d.HasWorkflow && !o.Force {
		clash = append(clash, WorkflowFile)
	}
	if len(clash) > 0 {
		fmt.Fprintf(env.Stderr, "refusing to overwrite %s (use --force)\n", strings.Join(clash, ", "))
		return ExitRefused
	}

	fmt.Fprintln(out, "Wrote")
	policy, err := RenderPolicy(PolicyData{Model: o.Model, Local: o.Model == "local", RepoOrPlaceholder: d.Remote,
		PortalURL: strings.TrimRight(o.PortalURL, "/"), PortalToken: o.Portal})
	if err != nil {
		fmt.Fprintln(env.Stderr, "error:", err)
		return ExitError
	}
	if err := os.WriteFile(policyPath, policy, 0o644); err != nil {
		fmt.Fprintln(env.Stderr, "error:", err)
		return ExitError
	}
	pr.line("✓", PolicyFile, "model: "+o.Model+overwrote(d.HasPolicy))
	if o.Portal != "" {
		pr.line("✓", "portal", o.PortalURL+" (token written to "+PolicyFile+"; use ${MERGEONE_PORTAL_TOKEN} there if the file is committed)")
	}
	return writeRest(o, env, pr, d, spec, workflowPath)
}

// Status writes the "Detected" checklist for dir (the same one init prints) and nothing else.
// Key values are never read beyond "is it set".
func Status(dir string, w io.Writer, getenv func(string) string, m ColorMode) {
	_, regFrom := LoadModels(findRegistry(dir))
	WriteDetected(w, Detect(dir, getenv), m, regFrom != "")
}

// WriteDetected prints the "Detected" checklist for d followed by one blank line. registryFound is
// whether a models.yaml was found (when not, a note says --model is checked against the built-in list).
func WriteDetected(out io.Writer, d Detection, m ColorMode, registryFound bool) {
	pr := printer{out, paletteFor(m)}
	fmt.Fprintln(out, "Detected")
	if d.GitRepo {
		pr.line("✓", "git repository", "")
	} else {
		pr.line("·", "git repository", "not a git repository (init still writes the policy)")
	}
	if d.Remote != "" {
		pr.line("✓", "remote origin", d.Remote)
	} else {
		pr.line("·", "remote origin", "none")
	}
	if d.BranchAssumed {
		pr.line("·", "default branch", "main (origin/HEAD not set; assumed)")
	} else {
		pr.line("✓", "default branch", d.DefaultBranch)
	}
	pr.line(mark(d.HasPolicy), PolicyFile, map[bool]string{true: "exists", false: "not yet"}[d.HasPolicy])
	pr.line(mark(d.HasWorkflow), WorkflowFile, map[bool]string{true: "exists", false: "not yet"}[d.HasWorkflow])
	claude := "not found"
	if d.HasClaude {
		claude = "present"
		if d.ClaudeHasBlock {
			claude = "present, has the mergeone snippet"
		}
	}
	pr.line(mark(d.HasClaude), ClaudeFile, claude)
	for _, k := range KeyEnvs {
		m := "✗"
		if d.Keys[k.Env] {
			m = "✓"
		}
		pr.line(m, k.Env, k.For)
	}
	if !registryFound {
		pr.line("·", "models.yaml", "not found; --model checked against the built-in list")
	}
	fmt.Fprintln(out)
}

// writeRest writes the optional workflow and CLAUDE.md, then prints the MCP block and next steps.
func writeRest(o Options, env Env, pr printer, d Detection, spec models.Spec, workflowPath string) int {
	out := env.Stdout
	if o.CI {
		wf, err := RenderWorkflow(WorkflowData{ActionsEnabled: false, SecretEnvs: secretEnvs(spec)})
		if err == nil {
			err = os.MkdirAll(filepath.Dir(workflowPath), 0o755)
		}
		if err == nil {
			err = os.WriteFile(workflowPath, wf, 0o644)
		}
		if err != nil {
			fmt.Fprintln(env.Stderr, "error:", err)
			return ExitError
		}
		detail := "on pull_request"
		if s := secretEnvs(spec); len(s) > 0 {
			detail += "; add repository secret " + strings.Join(s, ", ")
		}
		pr.line("✓", WorkflowFile, detail+overwrote(d.HasWorkflow))
	}
	if o.Write {
		wrote, err := AppendClaude(filepath.Join(o.Dir, ClaudeFile), d.DefaultBranch)
		if err != nil {
			fmt.Fprintln(env.Stderr, "error:", err)
			return ExitError
		}
		if wrote {
			pr.line("✓", ClaudeFile, "appended the mergeone snippet")
		} else {
			pr.line("·", ClaudeFile, "already has the mergeone snippet; unchanged")
		}
	}
	fmt.Fprintln(out)

	if o.MCP {
		fmt.Fprintln(out, "Agent (MCP)")
		fmt.Fprintln(out, "  "+MCPCommand)
		if !o.Write {
			fmt.Fprintln(out, "  CLAUDE.md snippet (or rerun with --mcp --write):")
		}
		for _, l := range strings.Split(strings.TrimRight(ClaudeSnippet(d.DefaultBranch), "\n"), "\n") {
			fmt.Fprintln(out, "    "+l)
		}
		fmt.Fprintln(out)
	}

	printNext(out, o, spec, d, env.Getenv)
	return ExitOK
}

func overwrote(existed bool) string {
	if existed {
		return " (overwritten)"
	}
	return ""
}

// printNext prints the numbered next steps.
func printNext(w io.Writer, o Options, spec models.Spec, d Detection, getenv func(string) string) {
	policy := PolicyFile
	if filepath.Clean(o.Dir) != "." {
		policy = filepath.Join(o.Dir, PolicyFile)
	}
	fmt.Fprintln(w, "Next")
	n := 1
	step := func(format string, a ...any) {
		fmt.Fprintf(w, "  %d. %s\n", n, fmt.Sprintf(format, a...))
		n++
	}
	switch {
	case spec.KeyOptional:
		step("start a /v1/systemone server for %s, or export MERGEONE_LOCAL_ENDPOINT=… (default http://127.0.0.1:8080/v1/systemone)", o.Model)
	case spec.KeyEnv != "" && getenv(spec.KeyEnv) == "":
		step("export %s=…   # missing: %s is not set, and model %s needs it", spec.KeyEnv, spec.KeyEnv, o.Model)
	case spec.KeyEnv != "":
		step("%s is set for model %s", spec.KeyEnv, o.Model)
	}
	step("mergeone pr --policy %s --base origin/%s --head HEAD", policy, d.DefaultBranch)
	step("mergeone serve --policy %s --addr :8787   # webhook mode (GITHUB_TOKEN, MERGEONE_WEBHOOK_SECRET); mergeone serve --mock for a dry run without any key", policy)
	step("%s", ReadmeURL)
}
