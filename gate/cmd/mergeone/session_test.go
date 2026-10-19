package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/initcmd"
)

// TestMain doubles as the mergeone binary: with MERGEONE_TEST_MAIN=1 the test executable runs main()
// with the arguments in MERGEONE_TEST_ARGS, so tests can check real exit codes.
func TestMain(m *testing.M) {
	if os.Getenv("MERGEONE_TEST_MAIN") == "1" {
		os.Args = append([]string{"mergeone"}, strings.Fields(os.Getenv("MERGEONE_TEST_ARGS"))...)
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// withSession forces a session on input and captures stdout/stderr; it restores everything after.
func withSession(t *testing.T, input string, env map[string]string) (code int, out, errOut string) {
	t.Helper()
	var o, e bytes.Buffer
	oldIn, oldOut, oldErr, oldEnv, oldInteractive := stdin, stdout, stderr, getenv, interactive
	t.Cleanup(func() { stdin, stdout, stderr, getenv, interactive = oldIn, oldOut, oldErr, oldEnv, oldInteractive })
	stdin, stdout, stderr = strings.NewReader(input), &o, &e
	getenv = func(k string) string { return env[k] }
	interactive = func() bool { return true }
	code = run(nil)
	return code, o.String(), e.String()
}

func git(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@t", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@t", "GIT_CONFIG_GLOBAL=/dev/null")
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, b)
	}
}

// repoWithChange makes a git repo whose HEAD adds one file on top of an initial commit.
func repoWithChange(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	git(t, dir, "init", "-q", "-b", "main")
	git(t, dir, "commit", "-q", "--allow-empty", "-m", "init")
	if err := os.WriteFile(filepath.Join(dir, "a.go"), []byte("package a\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	git(t, dir, "add", "a.go")
	git(t, dir, "commit", "-q", "-m", "add a")
	return dir
}

func TestSessionRunsCommandsAndEnds(t *testing.T) {
	dir := repoWithChange(t)
	t.Chdir(dir)
	code, out, errOut := withSession(t, "help\n\nstatus\nversion\nquit\nversion\n", map[string]string{"TYPESAFE_API_KEY": "sk-secret-value"})
	if code != 0 {
		t.Fatalf("session exit %d, stderr %q", code, errOut)
	}
	for _, want := range []string{
		initcmd.Tagline, // banner
		"type help · init sets up this repo · quit leaves · no .mergeone.yaml here yet — run init",
		"status ", "Ctrl-D", // help lists the inner commands
		"Detected", "✓ git repository", "✓ TYPESAFE_API_KEY", "✗ ANTHROPIC_API_KEY", // status
		"mergeone dev", // version
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "sk-secret-value") {
		t.Error("status printed a key value")
	}
	// The loop ended at quit: the version after it never ran. Prompts: help, empty, status, version, quit.
	if n := strings.Count(out, "mergeone dev"); n != 1 {
		t.Errorf("version ran %d times, want 1 (nothing after quit)", n)
	}
	if n := strings.Count(out, "mergeone › "); n != 5 {
		t.Errorf("%d prompts, want 5:\n%s", n, out)
	}
	if strings.Contains(out, "\x1b[") {
		t.Error("colour escapes on a non-terminal")
	}
}

func TestSessionEndsOnEOFAndReportsUnknown(t *testing.T) {
	t.Chdir(t.TempDir())
	if err := os.WriteFile(initcmd.PolicyFile, []byte("model: mock\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	code, out, _ := withSession(t, "frobnicate --x\nmodels --nope\n", nil)
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	if !strings.Contains(out, "unknown: frobnicate — type help") {
		t.Errorf("no unknown-command line:\n%s", out)
	}
	if !strings.Contains(out, "↳ exit 2\n") {
		t.Errorf("bad flag did not report exit 2:\n%s", out)
	}
	if strings.Contains(out, "no .mergeone.yaml here yet") {
		t.Error("hint says no policy, but there is one")
	}
	if !strings.HasSuffix(out, "mergeone › \n") {
		t.Errorf("Ctrl-D should leave on a fresh line, got tail %q", out[max(0, len(out)-40):])
	}
}

func TestSessionCdAffectsStatus(t *testing.T) {
	repo := repoWithChange(t)
	t.Chdir(t.TempDir())
	_, out, _ := withSession(t, "status\ncd "+repo+"\nstatus\nquit\n", nil)
	first, second, ok := strings.Cut(out, repo)
	if !ok {
		t.Fatalf("cd did not print the new directory:\n%s", out)
	}
	if !strings.Contains(first, "· git repository") || !strings.Contains(second, "✓ git repository") {
		t.Errorf("status did not follow cd:\n%s", out)
	}
}

// holdPolicy always holds: mock model, no rules, default hold.
const holdPolicy = "model: mock\ncalibration: none\nmemory: { enabled: false }\ndefault: { verdict: hold, route: human, reason: test }\n"

func TestSessionReportsPRExitInsteadOfExiting(t *testing.T) {
	dir := repoWithChange(t)
	t.Chdir(dir)
	if err := os.WriteFile(filepath.Join(dir, initcmd.PolicyFile), []byte(holdPolicy), 0o644); err != nil {
		t.Fatal(err)
	}
	code, out, errOut := withSession(t, "pr --mock --base HEAD~1\npr --no-such-flag\nversion\nquit\n", nil)
	if code != 0 {
		t.Fatalf("session exit %d; stderr %q", code, errOut)
	}
	if !strings.Contains(out, `"verdict": "hold"`) {
		t.Fatalf("pr did not run:\n%s\nstderr: %s", out, errOut)
	}
	if !strings.Contains(out, "↳ exit 1 (hold)\n") {
		t.Errorf("hold not reported as ↳ exit 1 (hold):\n%s", out)
	}
	if !strings.Contains(out, "↳ exit 3\n") {
		t.Errorf("a bad pr flag should report exit 3 (error), not a verdict:\n%s", out)
	}
	if !strings.Contains(out, "mergeone dev") {
		t.Error("session did not survive pr")
	}
}

func TestExitNote(t *testing.T) {
	for _, c := range []struct {
		name string
		code int
		want string
	}{{"pr", 1, "↳ exit 1 (hold)"}, {"pr", 2, "↳ exit 2 (block)"}, {"pr", 3, "↳ exit 3"}, {"decide", 2, "↳ exit 2 (block)"}, {"models", 2, "↳ exit 2"}} {
		if got := exitNote(c.name, c.code); got != c.want {
			t.Errorf("exitNote(%s, %d) = %q, want %q", c.name, c.code, got, c.want)
		}
	}
}

func TestSplitArgs(t *testing.T) {
	got, err := splitArgs(`pr  --base "origin/my branch" --repo '/tmp/a b' x\ y`)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"pr", "--base", "origin/my branch", "--repo", "/tmp/a b", "x y"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("got %q, want %q", got, want)
	}
	if _, err := splitArgs(`cd "oops`); err == nil {
		t.Error("unterminated quote accepted")
	}
}

// runBinary runs main() in a child process with stdin from /dev/null (not a terminal).
func runBinary(t *testing.T, args string) (int, string, string) {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^$")
	cmd.Env = append(os.Environ(), "MERGEONE_TEST_MAIN=1", "MERGEONE_TEST_ARGS="+args, "NO_COLOR=1")
	var o, e bytes.Buffer
	cmd.Stdout, cmd.Stderr = &o, &e
	err := cmd.Run()
	var ee *exec.ExitError
	if err != nil && !errors.As(err, &ee) {
		t.Fatal(err)
	}
	return cmd.ProcessState.ExitCode(), o.String(), e.String()
}

func TestNoArgsWithoutTTYPrintsUsageAndExits2(t *testing.T) {
	for _, args := range []string{"", "--no-session"} {
		code, out, errOut := runBinary(t, args)
		if code != 2 {
			t.Errorf("%q: exit %d, want 2", args, code)
		}
		if strings.TrimSpace(errOut) != usageLine {
			t.Errorf("%q: stderr %q, want the usage line", args, errOut)
		}
		if !strings.Contains(out, initcmd.Tagline) || strings.Contains(out, "mergeone ›") {
			t.Errorf("%q: stdout should be the banner only:\n%s", args, out)
		}
	}
}

func TestOneShotPRStillExitsWithVerdict(t *testing.T) {
	dir := repoWithChange(t)
	if err := os.WriteFile(filepath.Join(dir, initcmd.PolicyFile), []byte(holdPolicy), 0o644); err != nil {
		t.Fatal(err)
	}
	code, out, errOut := runBinary(t, "pr --mock --base HEAD~1 --repo "+dir+" --policy "+filepath.Join(dir, initcmd.PolicyFile)+" --log "+filepath.Join(dir, "gatelog.jsonl"))
	if code != 1 || !strings.Contains(out, `"verdict": "hold"`) {
		t.Errorf("exit %d, want 1 (hold); stdout %s; stderr %s", code, out, errOut)
	}
}
