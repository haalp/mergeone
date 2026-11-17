package main

// `mergeone connect`: link this repository's gateway to the portal without copying a token by hand.
//
//	mergeone connect            (in the clone; the policy from `mergeone init` is there)
//
// The command asks the portal for a short code, opens the portal's /connect page with it and waits. The person
// confirms the code in the browser, signed in to GitHub with the mergeone App installed on the repository; the portal
// then hands the repository's ingest token to this command, once. The token is written to .mergeone/portal-token
// beside the policy (mode 0600, excluded from git locally) and the policy gets a portal: block if it has none.
// The token is never printed, unless --print-env asks for it (for a gateway that runs somewhere else).

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/initcmd"
)

var (
	repoSlugRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$`)
	// openBrowser and gitOriginURL are variables so tests replace them.
	openBrowser = func(url string) error {
		var cmd *exec.Cmd
		switch runtime.GOOS {
		case "darwin":
			cmd = exec.Command("open", url)
		case "windows":
			cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
		default:
			cmd = exec.Command("xdg-open", url)
		}
		return cmd.Start()
	}
	gitOriginURL = func(dir string) (string, error) {
		out, err := exec.Command("git", "-C", dir, "remote", "get-url", "origin").Output()
		return strings.TrimSpace(string(out)), err
	}
	connectSleep = time.Sleep
)

// repoFromRemote reads owner/name out of a GitHub remote URL (https, ssh or scp form); "" when it is not one.
func repoFromRemote(remote string) string {
	r := strings.TrimSuffix(strings.TrimSpace(remote), "/")
	r = strings.TrimSuffix(r, ".git")
	for _, pre := range []string{"https://github.com/", "http://github.com/", "ssh://git@github.com/", "git@github.com:", "git://github.com/"} {
		if strings.HasPrefix(r, pre) {
			if slug := strings.TrimPrefix(r, pre); repoSlugRE.MatchString(slug) {
				return slug
			}
		}
	}
	// https://user@github.com/owner/name
	if i := strings.Index(r, "@github.com/"); i >= 0 && strings.HasPrefix(r, "https://") {
		if slug := r[i+len("@github.com/"):]; repoSlugRE.MatchString(slug) {
			return slug
		}
	}
	return ""
}

type connectStart struct {
	DeviceCode      string `json:"device_code"`
	UserCode        string `json:"user_code"`
	VerificationURI string `json:"verification_uri_complete"`
	ExpiresIn       int    `json:"expires_in"`
	Interval        int    `json:"interval"`
	Error           string `json:"error"`
}

type connectPoll struct {
	Status string `json:"status"` // pending | approved
	Token  string `json:"token"`
	Repo   string `json:"repo"`
	Error  string `json:"error"`
}

func postJSON(ctx context.Context, c *http.Client, url, bearer string, in, out any) (int, error) {
	body, _ := json.Marshal(in)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "mergeone/"+version)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := c.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if out != nil && len(b) > 0 {
		_ = json.Unmarshal(b, out)
	}
	return resp.StatusCode, nil
}

// ensurePortalBlock appends a portal: block to the policy when it has none. It reports whether the file changed.
// A policy that already has a portal: block is left exactly as it is.
func ensurePortalBlock(policyPath, portalURL string) (bool, error) {
	b, err := os.ReadFile(policyPath)
	if err != nil {
		return false, err
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "portal:") {
			return false, nil
		}
	}
	add := "\n# written by `mergeone connect`: every verdict is also posted to the portal. The token is read from\n" +
		"# $" + initcmd.PortalTokenEnv + " when set, else from .mergeone/portal-token beside this file.\n" +
		"portal:\n  url: " + portalURL + "\n  token: ${" + initcmd.PortalTokenEnv + "}\n"
	if len(b) > 0 && b[len(b)-1] != '\n' {
		add = "\n" + add
	}
	return true, os.WriteFile(policyPath, append(b, add...), 0o644)
}

// excludeLocally adds .mergeone/ to .git/info/exclude (this clone only; no tracked file changes). Best effort.
func excludeLocally(dir string) {
	out, err := exec.Command("git", "-C", dir, "rev-parse", "--git-path", "info/exclude").Output()
	if err != nil {
		return
	}
	p := strings.TrimSpace(string(out))
	if !filepath.IsAbs(p) {
		p = filepath.Join(dir, p)
	}
	b, _ := os.ReadFile(p)
	for _, l := range strings.Split(string(b), "\n") {
		if t := strings.TrimSpace(l); t == ".mergeone/" || t == ".mergeone" || t == "/.mergeone/" {
			return
		}
	}
	_ = os.MkdirAll(filepath.Dir(p), 0o755)
	f, err := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	if len(b) > 0 && b[len(b)-1] != '\n' {
		_, _ = f.WriteString("\n")
	}
	_, _ = f.WriteString(".mergeone/\n")
}

func cmdConnect(args []string) int {
	fs := flag.NewFlagSet("connect", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file (from `mergeone init`)")
	portalURL := fs.String("portal-url", initcmd.DefaultPortalURL, "the portal's base URL")
	repo := fs.String("repo", "", "owner/name (default: the origin remote of this clone)")
	noBrowser := fs.Bool("no-browser", false, "print the link instead of opening a browser")
	printEnv := fs.Bool("print-env", false, "also print "+initcmd.PortalTokenEnv+"=<token> on stdout, for a gateway that runs elsewhere (a secret: do not paste it into a log)")
	timeout := fs.Duration("timeout", 10*time.Minute, "how long to wait for the confirmation in the browser")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	base := strings.TrimRight(*portalURL, "/")
	if !strings.HasPrefix(base, "https://") && !strings.HasPrefix(base, "http://") {
		fmt.Fprintf(stderr, "error: --portal-url %q: want an http(s) URL\n", *portalURL)
		return 2
	}
	if _, err := os.Stat(*policy); err != nil {
		fmt.Fprintf(stderr, "error: %s not found. Run `mergeone init` in this clone first, then `mergeone connect`.\n", *policy)
		return 2
	}
	dir := filepath.Dir(*policy)
	slug := *repo
	if slug == "" {
		remote, err := gitOriginURL(dir)
		if slug = repoFromRemote(remote); err != nil || slug == "" {
			fmt.Fprintln(stderr, "error: could not read a GitHub owner/name from this clone's origin remote; pass --repo owner/name")
			return 2
		}
	}
	if !repoSlugRE.MatchString(slug) {
		fmt.Fprintf(stderr, "error: --repo %q: want owner/name\n", slug)
		return 2
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	client := &http.Client{Timeout: 20 * time.Second}

	var st connectStart
	code, err := postJSON(ctx, client, base+"/api/connect/start", "", map[string]string{"repo": slug}, &st)
	if err != nil || code != http.StatusCreated || st.DeviceCode == "" || st.UserCode == "" {
		fmt.Fprintf(stderr, "error: the portal at %s did not start a connection (%s)\n", base, connectErr(code, err, st.Error))
		return 3
	}
	link := st.VerificationURI
	if link == "" {
		link = base + "/connect?code=" + st.UserCode
	}
	fmt.Fprintf(stderr, "Connecting %s to %s\n\n  code  %s\n  open  %s\n\n", slug, base, st.UserCode, link)
	if *noBrowser || openBrowser(link) != nil {
		fmt.Fprintln(stderr, "Open the link, check that the code matches, and confirm.")
	} else {
		fmt.Fprintln(stderr, "Your browser is opening. Check that the code matches, and confirm.")
	}
	fmt.Fprintln(stderr, "Waiting for the confirmation…")

	interval := time.Duration(st.Interval) * time.Second
	if interval < time.Second {
		interval = 2 * time.Second
	}
	var token string
	for token == "" {
		if ctx.Err() != nil {
			fmt.Fprintln(stderr, "error: no confirmation arrived in time. Nothing was changed; run `mergeone connect` again.")
			return 3
		}
		connectSleep(interval)
		var pl connectPoll
		code, err := postJSON(ctx, client, base+"/api/connect/poll", "", map[string]string{"device_code": st.DeviceCode}, &pl)
		switch {
		case err != nil:
			if errors.Is(err, context.DeadlineExceeded) {
				continue
			}
			// a dropped request is retried at the next tick
		case code == http.StatusOK && pl.Status == "approved" && pl.Token != "":
			token = pl.Token
		case code == http.StatusOK:
		case code == http.StatusGone:
			fmt.Fprintln(stderr, "error: the code expired before it was confirmed. Nothing was changed; run `mergeone connect` again.")
			return 3
		default:
			fmt.Fprintf(stderr, "error: the portal refused the connection (%s). Nothing was changed.\n", connectErr(code, nil, pl.Error))
			return 3
		}
	}

	tokenFile := engine.PortalTokenFile(*policy)
	if err := os.MkdirAll(filepath.Dir(tokenFile), 0o700); err != nil {
		fmt.Fprintln(stderr, "error:", err)
		return 3
	}
	if err := os.WriteFile(tokenFile, []byte(token+"\n"), 0o600); err != nil {
		fmt.Fprintln(stderr, "error:", err)
		return 3
	}
	_ = os.Chmod(tokenFile, 0o600)
	excludeLocally(dir)
	changed, err := ensurePortalBlock(*policy, base)
	if err != nil {
		fmt.Fprintln(stderr, "error:", err)
		return 3
	}

	// an empty batch: the portal marks the token as seen, and its /connect page turns to "connected"
	var ping map[string]any
	pcode, perr := postJSON(ctx, client, base+"/ingest", token, map[string]any{"records": []any{}}, &ping)

	fmt.Fprintf(stderr, "\n✓ token   stored in %s (this clone only; not printed, not committed)\n", tokenFile)
	if changed {
		fmt.Fprintf(stderr, "✓ policy  portal: block added to %s — commit it\n", *policy)
	} else {
		fmt.Fprintf(stderr, "✓ policy  %s already has a portal: block; left as it is\n", *policy)
	}
	if perr == nil && pcode == http.StatusOK {
		fmt.Fprintf(stderr, "✓ portal  %s answered: this gateway is connected\n", base)
	} else {
		fmt.Fprintf(stderr, "! portal  the test request to %s/ingest did not succeed (%s); run `mergeone doctor`\n", base, connectErr(pcode, perr, ""))
	}
	if p, err := engine.LoadPolicy(*policy); err == nil && p.Portal.Token != token {
		fmt.Fprintf(stderr, "! note    $%s is set in this shell and takes precedence over the stored token\n", initcmd.PortalTokenEnv)
	}
	fmt.Fprintf(stderr, "\nRecords appear in the portal with the next verdict: `mergeone pr <number>` here, or the next webhook on a running gateway.\n")
	if !*printEnv {
		fmt.Fprintf(stderr, "A gateway on another machine reads the token from $%s; `mergeone connect --print-env` prints a token as that line for its secret store.\n", initcmd.PortalTokenEnv)
	}
	if *printEnv {
		fmt.Fprintf(stdout, "%s=%s\n", initcmd.PortalTokenEnv, token)
	}
	if perr != nil || pcode != http.StatusOK {
		return 1
	}
	return 0
}

func connectErr(code int, err error, msg string) string {
	switch {
	case err != nil:
		return err.Error()
	case msg != "":
		return fmt.Sprintf("HTTP %d: %s", code, msg)
	default:
		return fmt.Sprintf("HTTP %d", code)
	}
}
