package initcmd

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/goccy/go-yaml"
	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/models"
)

func registryPath(t *testing.T) string {
	wd, _ := os.Getwd()
	p := filepath.Join(wd, "..", "..", "..", "prompts", "models.yaml")
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("prompts/models.yaml: %v", err)
	}
	return p
}

func noEnv(string) string { return "" }

func envOf(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

// run invokes init in dir against the repository's models.yaml and returns code, stdout, stderr.
func run(t *testing.T, dir string, getenv func(string) string, args ...string) (int, string, string) {
	t.Helper()
	var out, errb bytes.Buffer
	code := RunEnv(append([]string{"--dir", dir}, args...), Env{Stdout: &out, Stderr: &errb, Getenv: getenv, Color: ColorNone, RegistryPath: registryPath(t)})
	return code, out.String(), errb.String()
}

func TestPolicyTemplateLoadsForEveryRegistryModel(t *testing.T) {
	reg, err := models.LoadRegistry(registryPath(t))
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	for _, name := range reg.Names() {
		b, err := RenderPolicy(PolicyData{Model: name, Local: name == "local", RepoOrPlaceholder: "acme/widgets"})
		if err != nil {
			t.Fatalf("%s: render: %v", name, err)
		}
		var generic map[string]any
		if err := yaml.Unmarshal(b, &generic); err != nil {
			t.Fatalf("%s: not valid YAML: %v", name, err)
		}
		path := filepath.Join(dir, name+".yaml")
		if err := os.WriteFile(path, b, 0o644); err != nil {
			t.Fatal(err)
		}
		p, err := engine.LoadPolicy(path)
		if err != nil {
			t.Fatalf("%s: LoadPolicy: %v", name, err)
		}
		if p.Model != name {
			t.Errorf("%s: model = %q", name, p.Model)
		}
		if p.Memory.Enabled || p.Memory.TopK != 5 {
			t.Errorf("%s: memory = %+v, want disabled, top_k 5", name, p.Memory)
		}
		if p.Actions.Enabled {
			t.Errorf("%s: actions must be off", name)
		}
		if len(p.AlwaysHumanPaths) != 0 {
			t.Errorf("%s: always_human_paths must be comments only, got %v", name, p.AlwaysHumanPaths)
		}
		if len(p.Provider) != 0 {
			t.Errorf("%s: provider must stay commented, got %v", name, p.Provider)
		}
		if len(p.Rules) != 6 || p.OnError.Verdict != "hold" {
			t.Errorf("%s: rules=%d on_error=%+v", name, len(p.Rules), p.OnError)
		}
		s := string(b)
		if strings.Contains(s, "k8s.io") || strings.Contains(s, "pkg/apis") {
			t.Errorf("%s: Kubernetes paths leaked into the template", name)
		}
		hasProvider := strings.Contains(s, "#   endpoint: http://127.0.0.1:8080/v1/systemone")
		if hasProvider != (name == "local") {
			t.Errorf("%s: commented provider example present=%v", name, hasProvider)
		}
	}
}

func TestFallbackMatchesRegistry(t *testing.T) {
	reg, err := models.LoadRegistry(registryPath(t))
	if err != nil {
		t.Fatal(err)
	}
	got, want := keys(fallbackRegistry), reg.Names()
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("fallback models %v != models.yaml %v", got, want)
	}
	for n, s := range reg.Models {
		f := fallbackRegistry[n]
		if f.KeyEnv != s.KeyEnv || f.KeyOptional != s.KeyOptional {
			t.Errorf("%s: fallback key_env=%s optional=%v vs registry key_env=%s optional=%v", n, f.KeyEnv, f.KeyOptional, s.KeyEnv, s.KeyOptional)
		}
	}
}

func keys(m map[string]models.Spec) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func TestRefusesWithoutForce(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, PolicyFile)
	if err := os.WriteFile(path, []byte("mine\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	code, _, stderr := run(t, dir, noEnv, "--quiet", "--ci")
	if code != ExitRefused {
		t.Fatalf("code %d, want %d", code, ExitRefused)
	}
	if !strings.Contains(stderr, "--force") {
		t.Errorf("stderr should mention --force: %q", stderr)
	}
	if b, _ := os.ReadFile(path); string(b) != "mine\n" {
		t.Errorf("policy was modified: %q", b)
	}
	if exists(filepath.Join(dir, WorkflowFile)) {
		t.Errorf("a refusal must not write the workflow either")
	}
}

func TestRefusesExistingWorkflowWithoutForce(t *testing.T) {
	dir := t.TempDir()
	wf := filepath.Join(dir, WorkflowFile)
	os.MkdirAll(filepath.Dir(wf), 0o755)
	os.WriteFile(wf, []byte("mine\n"), 0o644)
	if code, _, _ := run(t, dir, noEnv, "--quiet", "--ci"); code != ExitRefused {
		t.Fatalf("code %d, want %d", code, ExitRefused)
	}
	if exists(filepath.Join(dir, PolicyFile)) {
		t.Errorf("policy written despite the refusal")
	}
}

func TestForceOverwrites(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, PolicyFile)
	os.WriteFile(path, []byte("mine\n"), 0o644)
	code, out, stderr := run(t, dir, noEnv, "--quiet", "--force", "--model", "pplx-flash")
	if code != ExitOK {
		t.Fatalf("code %d: %s", code, stderr)
	}
	p, err := engine.LoadPolicy(path)
	if err != nil {
		t.Fatal(err)
	}
	if p.Model != "pplx-flash" {
		t.Errorf("model %q", p.Model)
	}
	if !strings.Contains(out, "(overwritten)") {
		t.Errorf("output should say it overwrote: %s", out)
	}
}

func TestBadFlagsAndModel(t *testing.T) {
	dir := t.TempDir()
	for _, args := range [][]string{{"--model", "nope"}, {"--bogus"}, {"extra-arg"}} {
		if code, _, _ := run(t, dir, noEnv, args...); code != ExitUsage {
			t.Errorf("%v: code %d, want %d", args, code, ExitUsage)
		}
	}
	if exists(filepath.Join(dir, PolicyFile)) {
		t.Errorf("bad flags must not write anything")
	}
}

func TestParseRemote(t *testing.T) {
	cases := map[string]string{
		"git@github.com:acme/widgets.git":                     "acme/widgets",
		"git@github.com:acme/widgets":                         "acme/widgets",
		"ssh://git@github.com/acme/widgets.git":               "acme/widgets",
		"ssh://git@github.com:22/acme/widgets.git":            "acme/widgets",
		"https://github.com/acme/widgets.git":                 "acme/widgets",
		"https://github.com/acme/widgets":                     "acme/widgets",
		"https://github.com/acme/widgets/":                    "acme/widgets",
		"https://x-access-token:secret@github.com/acme/w.git": "acme/w",
		"git://github.com/acme/widgets.git":                   "acme/widgets",
		"https://gitlab.com/group/sub/proj.git":               "group/sub/proj",
	}
	for in, want := range cases {
		got, ok := ParseRemote(in)
		if !ok || got != want {
			t.Errorf("ParseRemote(%q) = %q, %v; want %q", in, got, ok, want)
		}
	}
	for _, bad := range []string{"", "/srv/git/widgets.git", "../widgets", "https://github.com/acme", "git@github.com:widgets.git"} {
		if got, ok := ParseRemote(bad); ok {
			t.Errorf("ParseRemote(%q) = %q, want no match", bad, got)
		}
	}
}

func TestClaudeAppendIdempotent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ClaudeFile)
	os.WriteFile(path, []byte("# Project rules\nBe nice."), 0o644)
	for i := 0; i < 3; i++ {
		if code, _, stderr := run(t, dir, noEnv, "--quiet", "--force", "--mcp", "--write"); code != ExitOK {
			t.Fatalf("run %d: code %d: %s", i, code, stderr)
		}
	}
	b, _ := os.ReadFile(path)
	s := string(b)
	if n := strings.Count(s, ClaudeMarker); n != 1 {
		t.Fatalf("marker appears %d times:\n%s", n, s)
	}
	if !strings.HasPrefix(s, "# Project rules\nBe nice.\n\n"+ClaudeMarker+"\n") {
		t.Errorf("existing content not preserved / separated:\n%s", s)
	}
	if !strings.Contains(s, `call gate_decide with repo_path="." base="main" head="HEAD"`) {
		t.Errorf("snippet missing:\n%s", s)
	}
	// Created when absent.
	dir2 := t.TempDir()
	if wrote, err := AppendClaude(filepath.Join(dir2, ClaudeFile), "trunk"); err != nil || !wrote {
		t.Fatalf("create: wrote=%v err=%v", wrote, err)
	}
	if wrote, _ := AppendClaude(filepath.Join(dir2, ClaudeFile), "trunk"); wrote {
		t.Errorf("second append wrote again")
	}
}

func TestMCPWithoutWritePrintsOnly(t *testing.T) {
	dir := t.TempDir()
	code, out, _ := run(t, dir, noEnv, "--quiet", "--mcp")
	if code != ExitOK {
		t.Fatal(code)
	}
	if !strings.Contains(out, MCPCommand) || !strings.Contains(out, "## Merge gate (mergeone)") {
		t.Errorf("missing MCP command or snippet:\n%s", out)
	}
	if exists(filepath.Join(dir, ClaudeFile)) {
		t.Errorf("--mcp without --write must not touch CLAUDE.md")
	}
}

func TestBannerPlainWithNoColor(t *testing.T) {
	env := envOf(map[string]string{"NO_COLOR": "1", "COLORTERM": "truecolor"})
	if m := DetectColor(true, env); m != ColorNone {
		t.Fatalf("NO_COLOR on a TTY: mode %v, want none", m)
	}
	if m := DetectColor(false, envOf(map[string]string{"COLORTERM": "truecolor"})); m != ColorNone {
		t.Fatalf("not a TTY: mode %v, want none", m)
	}
	if m := DetectColor(true, envOf(map[string]string{"COLORTERM": "truecolor"})); m != ColorTrue {
		t.Fatalf("truecolor TTY: %v", m)
	}
	if m := DetectColor(true, noEnv); m != Color256 {
		t.Fatalf("plain TTY: %v", m)
	}
	var b bytes.Buffer
	WriteBanner(&b, DetectColor(true, env))
	if strings.Contains(b.String(), "\x1b") {
		t.Fatalf("escape codes with NO_COLOR:\n%q", b.String())
	}
	if want := strings.Join(BannerLines(), "\n") + "\n\n"; b.String() != want {
		t.Errorf("plain banner differs from BannerLines")
	}
	// Coloured: the two purples, 24-bit and 256-colour.
	var tc, c256 bytes.Buffer
	WriteBanner(&tc, ColorTrue)
	WriteBanner(&c256, Color256)
	for _, want := range []string{"38;2;163;113;247", "38;2;201;170;255"} {
		if !strings.Contains(tc.String(), want) {
			t.Errorf("truecolor banner lacks %s", want)
		}
	}
	for _, want := range []string{"38;5;141", "38;5;183"} {
		if !strings.Contains(c256.String(), want) {
			t.Errorf("256-colour banner lacks %s", want)
		}
	}
}

func TestBannerSize(t *testing.T) {
	lines := BannerLines()
	if len(lines) > 7 || len(lines) < 6 {
		t.Errorf("banner is %d lines", len(lines))
	}
	if w := bannerWidth(); w > 60 {
		t.Errorf("banner is %d columns wide", w)
	}
	if lines[len(lines)-1] != Tagline {
		t.Errorf("last line %q", lines[len(lines)-1])
	}
	for _, l := range lines[:5] {
		if utf8.RuneCountInString(l) == 0 {
			t.Errorf("empty wordmark row")
		}
	}
}

func TestQuietAndKeysNeverPrinted(t *testing.T) {
	dir := t.TempDir()
	secret := "sk-very-secret-value-123"
	code, out, _ := run(t, dir, envOf(map[string]string{"TYPESAFE_API_KEY": secret, "GITHUB_TOKEN": secret + "gh"}), "--quiet")
	if code != ExitOK {
		t.Fatal(code)
	}
	if strings.Contains(out, secret) {
		t.Fatalf("key value printed:\n%s", out)
	}
	if strings.Contains(out, Tagline) {
		t.Errorf("--quiet printed the banner")
	}
	if !strings.Contains(out, "✓ TYPESAFE_API_KEY") || !strings.Contains(out, "✗ ANTHROPIC_API_KEY") {
		t.Errorf("key checklist wrong:\n%s", out)
	}
	if strings.Contains(out, "missing: TYPESAFE_API_KEY") {
		t.Errorf("key is set but next steps say missing")
	}
}

func TestNextStepsNameMissingKey(t *testing.T) {
	dir := t.TempDir()
	_, out, _ := run(t, dir, noEnv, "--quiet", "--model", "haiku")
	for _, want := range []string{"missing: ANTHROPIC_API_KEY is not set", "mergeone pr --policy", "--base origin/main --head HEAD", "mergeone serve --mock", ReadmeURL} {
		if !strings.Contains(out, want) {
			t.Errorf("next steps lack %q:\n%s", want, out)
		}
	}
}

func TestWorkflowRendersForModel(t *testing.T) {
	dir := t.TempDir()
	if code, _, stderr := run(t, dir, noEnv, "--quiet", "--ci", "--model", "pplx"); code != ExitOK {
		t.Fatalf("code %d: %s", code, stderr)
	}
	b, err := os.ReadFile(filepath.Join(dir, WorkflowFile))
	if err != nil {
		t.Fatal(err)
	}
	var wf map[string]any
	if err := yaml.Unmarshal(b, &wf); err != nil {
		t.Fatalf("workflow is not valid YAML: %v\n%s", err, b)
	}
	s := string(b)
	for _, want := range []string{"install.sh", "mergeone pr --policy .mergeone.yaml", "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", "PERPLEXITY_API_KEY: ${{ secrets.PERPLEXITY_API_KEY }}", "# statuses: write", "pull_request"} {
		if !strings.Contains(s, want) {
			t.Errorf("workflow lacks %q:\n%s", want, s)
		}
	}
	on, _ := RenderWorkflow(WorkflowData{ActionsEnabled: true})
	for _, want := range []string{"\n  statuses: write", "\n  pull-requests: write", "\n  issues: write"} {
		if !strings.Contains(string(on), want) {
			t.Errorf("actions-enabled workflow lacks %q", want)
		}
	}
	local, _ := RenderWorkflow(WorkflowData{SecretEnvs: secretEnvs(fallbackRegistry["local"])})
	if !strings.Contains(string(local), "MERGEONE_LOCAL_ENDPOINT: ${{ secrets.MERGEONE_LOCAL_ENDPOINT }}") {
		t.Errorf("local workflow lacks endpoint secret")
	}
}

func TestDetectGitRemoteAndBranch(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not installed")
	}
	dir := t.TempDir()
	git := func(a ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", dir}, a...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", a, err, out)
		}
	}
	git("init", "-q")
	git("remote", "add", "origin", "https://github.com/acme/widgets.git")
	git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
	d := Detect(dir, noEnv)
	if !d.GitRepo || d.Remote != "acme/widgets" || d.DefaultBranch != "trunk" || d.BranchAssumed {
		t.Fatalf("detect: %+v", d)
	}
	if nd := Detect(t.TempDir(), noEnv); nd.GitRepo || nd.DefaultBranch != "main" || !nd.BranchAssumed {
		t.Fatalf("non-repo: %+v", nd)
	}
}
