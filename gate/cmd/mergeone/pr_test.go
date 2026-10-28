package main

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/haalp/mergeone/gate/internal/initcmd"
	"github.com/haalp/mergeone/gate/internal/store"
)

// fakeGitHub serves PR 12 of o/r: head "headsha" on base main, opened by agent-bot. An older head "oldsha" is
// served through the compare API. It records the paths it was asked for.
func fakeGitHub(t *testing.T) (*httptest.Server, func() []string) {
	t.Helper()
	var mu sync.Mutex
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.URL.Path)
		mu.Unlock()
		if r.Header.Get("Authorization") != "Bearer tok" {
			w.WriteHeader(401)
			return
		}
		diff := r.Header.Get("Accept") == "application/vnd.github.diff"
		switch r.URL.Path {
		case "/repos/o/r/pulls/12/files":
			fmt.Fprint(w, `[{"filename":"pkg/parse/p.go","additions":7,"deletions":2},{"filename":"README.md","additions":1,"deletions":0}]`)
		case "/repos/o/r/pulls/12":
			if diff {
				fmt.Fprint(w, "diff --git a/pkg/parse/p.go b/pkg/parse/p.go\n+fixed\n")
				return
			}
			fmt.Fprint(w, `{"title":"Fix the parser","body":"closes #3","head":{"sha":"headsha"},"base":{"ref":"main"},
				"user":{"login":"agent-bot"},"created_at":"2026-10-20T09:00:00Z","updated_at":"2026-10-21T09:00:00Z"}`)
		case "/repos/o/r/compare/main...oldsha":
			if diff {
				fmt.Fprint(w, "diff --git a/pkg/parse/p.go b/pkg/parse/p.go\n+first try\n")
				return
			}
			fmt.Fprint(w, `{"files":[{"filename":"pkg/parse/p.go","additions":3,"deletions":1}]}`)
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, func() []string { mu.Lock(); defer mu.Unlock(); return append([]string(nil), seen...) }
}

// branchRepo is a clone whose branch feat adds a.go on top of main, with a commit subject that is not the PR title.
func branchRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	git(t, dir, "init", "-q", "-b", "main")
	git(t, dir, "commit", "-q", "--allow-empty", "-m", "init")
	git(t, dir, "checkout", "-q", "-b", "feat")
	if err := os.WriteFile(filepath.Join(dir, "a.go"), []byte("package a\n\nfunc A() {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	git(t, dir, "add", "a.go")
	git(t, dir, "commit", "-q", "-m", "wip: first commit on the branch")
	if err := os.WriteFile(filepath.Join(dir, initcmd.PolicyFile), []byte(holdPolicy), 0o644); err != nil {
		t.Fatal(err)
	}
	return dir
}

// runPR runs `mergeone pr …` in process with env and returns the exit code, stderr and the gatelog.
func runPR(t *testing.T, dir string, env map[string]string, args ...string) (int, string, *store.Store) {
	t.Helper()
	var o, e bytes.Buffer
	oldOut, oldErr, oldEnv := stdout, stderr, getenv
	t.Cleanup(func() { stdout, stderr, getenv = oldOut, oldErr, oldEnv })
	stdout, stderr = &o, &e
	getenv = func(k string) string { return env[k] }
	logPath := filepath.Join(t.TempDir(), "gatelog.jsonl")
	base := []string{"--mock", "--repo", dir, "--policy", filepath.Join(dir, initcmd.PolicyFile), "--log", logPath}
	full := append([]string{"pr"}, args...)
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		full = append(full, base...) // positional first, flags after
	} else {
		full = append([]string{"pr"}, append(base, args...)...) // flags first, positional (if any) last
	}
	code := run(full)
	st, err := store.Open(logPath)
	if err != nil {
		t.Fatal(err)
	}
	return code, e.String(), st
}

func TestPRFetchesPRFromGitHub(t *testing.T) {
	srv, seen := fakeGitHub(t)
	dir := branchRepo(t)
	env := map[string]string{"GITHUB_TOKEN": "tok", "GITHUB_API_URL": srv.URL}
	for name, args := range map[string][]string{
		"flags":           {"--gh-repo", "o/r", "--pr", "12", "--head-sha", "headsha"},
		"positional":      {"o/r", "12"},
		"positional-last": {"--base", "main", "o/r", "12"}, // flags first, then owner/name N
	} {
		t.Run(name, func(t *testing.T) {
			code, errOut, st := runPR(t, dir, env, args...)
			if code != 1 {
				t.Fatalf("exit %d, want 1 (hold); stderr %s", code, errOut)
			}
			if errOut != "" {
				t.Errorf("unexpected stderr %q", errOut)
			}
			rec := st.Latest("o/r", "headsha")
			if rec == nil {
				t.Fatal("no gatelog record for o/r@headsha (head sha not resolved from GitHub?)")
			}
			if rec.PR != 12 || rec.Source != "cli" {
				t.Errorf("record pr %d source %q", rec.PR, rec.Source)
			}
			want := map[string]any{"title": "Fix the parser", "author": "agent-bot", "base": "main",
				"created_at": "2026-10-20T09:00:00Z", "additions": 8.0, "deletions": 2.0, "files": 2.0}
			for k, v := range want {
				if got := rec.Context[k]; fmt.Sprint(got) != fmt.Sprint(v) {
					t.Errorf("context[%s] = %v, want %v", k, got, v)
				}
			}
			if !strings.HasPrefix(rec.State, "PULL REQUEST TITLE: Fix the parser\nMERGE MESSAGE BODY:\ncloses #3") ||
				!strings.Contains(rec.State, "pkg/parse/p.go +7 -2") || strings.Contains(rec.State, "wip: first commit") || strings.Contains(rec.State, "a.go") {
				t.Errorf("state not rendered from the GitHub PR:\n%s", rec.State)
			}
		})
	}
	for _, p := range seen() {
		if strings.Contains(p, "compare") {
			t.Errorf("current head should use the PR's own files and diff, got %s", p)
		}
	}
}

func TestPROlderHeadRendersThatHead(t *testing.T) {
	srv, seen := fakeGitHub(t)
	dir := branchRepo(t)
	code, errOut, st := runPR(t, dir, map[string]string{"GITHUB_TOKEN": "tok", "GITHUB_API_URL": srv.URL}, "o/r", "12", "--head-sha", "oldsha")
	if code != 1 {
		t.Fatalf("exit %d; stderr %s", code, errOut)
	}
	rec := st.Latest("o/r", "oldsha")
	if rec == nil {
		t.Fatalf("no record keyed on the requested head; GitHub saw %v", seen())
	}
	if !strings.Contains(rec.State, "+first try") || rec.Context["title"] != "Fix the parser" || fmt.Sprint(rec.Context["files"]) != "1" {
		t.Errorf("older head not rendered from compare: context %v\n%s", rec.Context, rec.State)
	}
}

func TestPRWithoutTokenKeepsLocalRenderingAndLeavesTitleEmpty(t *testing.T) {
	dir := branchRepo(t)
	code, errOut, st := runPR(t, dir, nil, "o/r", "12", "--head-sha", "abc123")
	if code != 1 {
		t.Fatalf("exit %d; stderr %s", code, errOut)
	}
	if n := strings.Count(errOut, "\n"); n != 1 || !strings.Contains(errOut, "no GITHUB_TOKEN") || !strings.Contains(errOut, "context is partial") {
		t.Errorf("want one notice line, got %q", errOut)
	}
	rec := st.Latest("o/r", "abc123")
	if rec == nil {
		t.Fatal("no record keyed on (o/r, abc123)")
	}
	if !strings.Contains(rec.State, "a.go +3 -0") {
		t.Errorf("local diff not rendered:\n%s", rec.State)
	}
	c := rec.Context
	if c == nil {
		t.Fatal("no context")
	}
	if c["title"] != "" || c["author"] != "" {
		t.Errorf("title/author must stay empty without GitHub (a commit subject is not the PR title): %v", c)
	}
	if _, ok := c["created_at"]; ok {
		t.Errorf("created_at is not known from git: %v", c)
	}
	if c["base"] != "main" || fmt.Sprint(c["files"]) != "1" || fmt.Sprint(c["additions"]) != "3" || fmt.Sprint(c["deletions"]) != "0" {
		t.Errorf("git-derived context wrong: %v", c)
	}
	// A base that is a revision, not a branch, is not reported as the PR's base.
	_, _, st = runPR(t, dir, nil, "o/r", "12", "--head-sha", "abc123", "--base", "HEAD~1")
	if rec := st.Latest("o/r", "abc123"); rec == nil || rec.Context["base"] != "" {
		t.Errorf("base from a revision: %v", rec)
	}
}

func TestPRFetchFailureFallsBackWithNotice(t *testing.T) {
	srv, _ := fakeGitHub(t)
	dir := branchRepo(t)
	code, errOut, st := runPR(t, dir, map[string]string{"GITHUB_TOKEN": "wrong", "GITHUB_API_URL": srv.URL}, "o/r", "12", "--head-sha", "abc123")
	if code != 1 || !strings.Contains(errOut, "could not fetch o/r#12") || !strings.Contains(errOut, "context is partial") {
		t.Fatalf("exit %d stderr %q", code, errOut)
	}
	if rec := st.Latest("o/r", "abc123"); rec == nil || rec.Context["title"] != "" {
		t.Errorf("fallback record: %v", rec)
	}
}

func TestPRPositionalArgs(t *testing.T) {
	for _, c := range []struct {
		args []string
		repo string
		pr   int
		rest int
	}{
		{[]string{"o/r", "12"}, "o/r", 12, 0},
		{[]string{"o/r", "#12", "--mock"}, "o/r", 12, 1},
		{[]string{"--mock", "o/r", "12"}, "", 0, 3},
		{[]string{"o/r"}, "", 0, 1},
		{[]string{"o/r/x", "12"}, "", 0, 2},
		{[]string{"o/r", "x"}, "", 0, 2},
		{[]string{"/r", "12"}, "", 0, 2},
	} {
		repo, pr, rest := prPositional(c.args)
		if repo != c.repo || pr != c.pr || len(rest) != c.rest {
			t.Errorf("%v → %q %d %v", c.args, repo, pr, rest)
		}
	}
	dir := branchRepo(t)
	for _, args := range [][]string{{"o/r", "12", "extra"}, {"--base", "main", "nope"}, {"o/r", "12", "--base", "main", "o/r", "13"}} {
		if code, errOut, _ := runPR(t, dir, nil, args...); code != 3 || !strings.Contains(errOut, "unexpected argument") {
			t.Errorf("%v: exit %d stderr %q, want 3 and unexpected argument", args, code, errOut)
		}
	}
}
