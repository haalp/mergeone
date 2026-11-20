package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func TestRepoFromRemote(t *testing.T) {
	for in, want := range map[string]string{
		"https://github.com/haalp/mergeone.git":       "haalp/mergeone",
		"https://github.com/haalp/mergeone":           "haalp/mergeone",
		"git@github.com:haalp/mergeone.dev.git":       "haalp/mergeone.dev",
		"ssh://git@github.com/haalp/mergeone-portal":  "haalp/mergeone-portal",
		"https://x-token@github.com/haalp/mergeone":   "haalp/mergeone",
		"https://gitlab.com/haalp/mergeone.git":       "",
		"https://github.com/haalp":                    "",
		"https://github.com/haalp/mergeone/tree/main": "",
		"": "",
	} {
		if got := repoFromRemote(in); got != want {
			t.Errorf("repoFromRemote(%q) = %q, want %q", in, got, want)
		}
	}
}

const testPortalToken = "mo_pt_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"

// fakePortal answers /api/connect/start, /api/connect/poll (pending twice, then the token) and /ingest.
func fakePortal(t *testing.T, pollStatus int) (*httptest.Server, *atomic.Int32, *atomic.Value) {
	t.Helper()
	var polls atomic.Int32
	var seen atomic.Value
	seen.Store("")
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/connect/start":
			if body["repo"] != "acme/widgets" {
				t.Errorf("start: repo = %v", body["repo"])
			}
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]any{"device_code": "dev-secret", "user_code": "BCDF-GHJK",
				"verification_uri_complete": srv.URL + "/connect?code=BCDF-GHJK", "expires_in": 600, "interval": 1})
		case "/api/connect/poll":
			if body["device_code"] != "dev-secret" {
				t.Errorf("poll: device_code = %v", body["device_code"])
			}
			if pollStatus != http.StatusOK {
				w.WriteHeader(pollStatus)
				_ = json.NewEncoder(w).Encode(map[string]any{"error": "expired"})
				return
			}
			if polls.Add(1) < 3 {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "pending"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "approved", "token": testPortalToken, "repo": "acme/widgets"})
		case "/ingest":
			seen.Store(r.Header.Get("Authorization"))
			_ = json.NewEncoder(w).Encode(map[string]any{"received": 0})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &polls, &seen
}

func connectEnv(t *testing.T) (dir string, out, errb *bytes.Buffer, opened *string) {
	t.Helper()
	dir = t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".mergeone.yaml"), []byte("model: jev\nrules: []\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	out, errb = &bytes.Buffer{}, &bytes.Buffer{}
	oOut, oErr, oOpen, oGit, oSleep := stdout, stderr, openBrowser, gitOriginURL, connectSleep
	var url string
	opened = &url
	stdout, stderr = out, errb
	openBrowser = func(u string) error { url = u; return nil }
	gitOriginURL = func(string) (string, error) { return "git@github.com:acme/widgets.git", nil }
	connectSleep = func(time.Duration) {}
	t.Cleanup(func() { stdout, stderr, openBrowser, gitOriginURL, connectSleep = oOut, oErr, oOpen, oGit, oSleep })
	t.Setenv("MERGEONE_PORTAL_TOKEN", "")
	return
}

func TestConnectStoresTheTokenAndNeverPrintsIt(t *testing.T) {
	srv, polls, seen := fakePortal(t, http.StatusOK)
	dir, out, errb, opened := connectEnv(t)
	policy := filepath.Join(dir, ".mergeone.yaml")
	if code := cmdConnect([]string{"--policy", policy, "--portal-url", srv.URL}); code != 0 {
		t.Fatalf("exit %d\n%s", code, errb)
	}
	if polls.Load() != 3 || *opened != srv.URL+"/connect?code=BCDF-GHJK" {
		t.Errorf("polls=%d opened=%q", polls.Load(), *opened)
	}
	if strings.Contains(out.String()+errb.String(), testPortalToken) {
		t.Fatal("the token was printed")
	}
	if !strings.Contains(errb.String(), "BCDF-GHJK") || !strings.Contains(errb.String(), "this gateway is connected") {
		t.Errorf("output:\n%s", errb)
	}
	tf := filepath.Join(dir, ".mergeone", "portal-token")
	b, err := os.ReadFile(tf)
	if err != nil || strings.TrimSpace(string(b)) != testPortalToken {
		t.Fatalf("token file: %q %v", b, err)
	}
	if st, _ := os.Stat(tf); st.Mode().Perm() != 0o600 {
		t.Errorf("token file mode %v", st.Mode().Perm())
	}
	if seen.Load().(string) != "Bearer "+testPortalToken {
		t.Errorf("ingest ping bearer = %q", seen.Load())
	}
	// the policy got a portal: block, the token is not in it, and the gateway resolves the stored token
	pb, _ := os.ReadFile(policy)
	if !strings.Contains(string(pb), "portal:\n  url: "+srv.URL+"\n  token: ${MERGEONE_PORTAL_TOKEN}\n") || strings.Contains(string(pb), testPortalToken) {
		t.Errorf("policy:\n%s", pb)
	}
	p, err := engine.LoadPolicy(policy)
	if err != nil || p.Portal.URL != srv.URL || p.Portal.Token != testPortalToken {
		t.Fatalf("loaded portal = %+v, %v", p.Portal, err)
	}
	// the environment wins over the file
	t.Setenv("MERGEONE_PORTAL_TOKEN", "from-env")
	if p, _ := engine.LoadPolicy(policy); p.Portal.Token != "from-env" {
		t.Errorf("env should win, got %q", p.Portal.Token)
	}
	// a second run leaves an existing portal: block alone
	t.Setenv("MERGEONE_PORTAL_TOKEN", "")
	errb.Reset()
	if code := cmdConnect([]string{"--policy", policy, "--portal-url", srv.URL, "--no-browser"}); code != 0 {
		t.Fatalf("second run exit %d\n%s", code, errb)
	}
	if pb2, _ := os.ReadFile(policy); string(pb2) != string(pb) || !strings.Contains(errb.String(), "already has a portal: block") {
		t.Errorf("second run changed the policy or did not say so:\n%s", errb)
	}
}

func TestConnectPrintEnvIsTheOnlyWayTheTokenIsPrinted(t *testing.T) {
	srv, _, _ := fakePortal(t, http.StatusOK)
	dir, out, errb, _ := connectEnv(t)
	if code := cmdConnect([]string{"--policy", filepath.Join(dir, ".mergeone.yaml"), "--portal-url", srv.URL, "--print-env", "--repo", "acme/widgets"}); code != 0 {
		t.Fatalf("exit %d\n%s", code, errb)
	}
	if out.String() != "MERGEONE_PORTAL_TOKEN="+testPortalToken+"\n" || strings.Contains(errb.String(), testPortalToken) {
		t.Errorf("stdout %q", out.String())
	}
}

func TestConnectFailuresChangeNothing(t *testing.T) {
	srv, _, _ := fakePortal(t, http.StatusGone)
	dir, _, errb, _ := connectEnv(t)
	policy := filepath.Join(dir, ".mergeone.yaml")
	before, _ := os.ReadFile(policy)
	if code := cmdConnect([]string{"--policy", policy, "--portal-url", srv.URL}); code != 3 || !strings.Contains(errb.String(), "expired") {
		t.Fatalf("exit %d\n%s", code, errb)
	}
	after, _ := os.ReadFile(policy)
	if _, err := os.Stat(filepath.Join(dir, ".mergeone", "portal-token")); err == nil || string(after) != string(before) {
		t.Error("an expired code must leave no token and an untouched policy")
	}
	// no policy, an unreadable remote, a non-http portal URL
	if code := cmdConnect([]string{"--policy", filepath.Join(dir, "nope.yaml"), "--portal-url", srv.URL}); code != 2 {
		t.Errorf("missing policy: exit %d", code)
	}
	gitOriginURL = func(string) (string, error) { return "https://gitlab.com/a/b.git", nil }
	if code := cmdConnect([]string{"--policy", policy, "--portal-url", srv.URL}); code != 2 {
		t.Errorf("non-GitHub remote: exit %d", code)
	}
	if code := cmdConnect([]string{"--policy", policy, "--portal-url", "ftp://x", "--repo", "a/b"}); code != 2 {
		t.Errorf("bad portal url: exit %d", code)
	}
}

func TestConnectUsesThePortalThePolicyNames(t *testing.T) {
	srv, _, seen := fakePortal(t, http.StatusOK)
	dir, _, errb, opened := connectEnv(t)
	policy := filepath.Join(dir, ".mergeone.yaml")
	if err := os.WriteFile(policy, []byte("model: jev\nrules: []\nportal:\n  url: "+srv.URL+"\n  token: ${MERGEONE_PORTAL_TOKEN}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	// no --portal-url: the default would be app.mergeone.dev; the policy's own portal is the one asked
	if code := cmdConnect([]string{"--policy", policy}); code != 0 {
		t.Fatalf("exit %d\n%s", code, errb)
	}
	if !strings.HasPrefix(*opened, srv.URL+"/connect?code=") || seen.Load().(string) != "Bearer "+testPortalToken {
		t.Errorf("opened %q, ingest bearer %q", *opened, seen.Load())
	}
}
