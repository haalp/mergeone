package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/models"
	"github.com/haalp/mergeone/gate/internal/store"
)

// mergeone doctor (v0.7): one command that says whether this setup can decide a pull request, and what is
// missing when it cannot. It checks, in the order a decision needs them: the policy, the assets, the model key
// and one real model call, the GitHub token and its access to a repository, the gatelog, the portal block, and —
// with --url — a running gateway: health, mode, and that it verifies webhook signatures with the secret in this
// environment. Secrets are never printed; a check names the variable, not its value.
//
//	mergeone doctor [--policy .mergeone.yaml] [--repo owner/name] [--url https://gate.example] [--offline] [--json]
//
// Exit 0 when nothing failed (warnings allowed), 1 otherwise.

type check struct {
	Name   string `json:"name"`
	Status string `json:"status"` // ok | warn | fail | skip
	Detail string `json:"detail"`
	Fix    string `json:"fix,omitempty"`
}

type doctor struct {
	checks  []check
	ghAPI   string
	client  *http.Client
	offline bool
}

func (d *doctor) add(name, status, detail, fix string) {
	d.checks = append(d.checks, check{name, status, detail, fix})
}

// doctorState is the fixed, tiny input of the doctor's one model call: no repository content leaves the machine.
const doctorState = "PULL REQUEST TITLE: docs: fix a typo in the README\nBODY: One-word spelling fix.\nSUMMARY: 1 files changed, +1 / -1 lines\nFILES:\nREADME.md (+1/-1)\nDIFF:\n-A merge gatway.\n+A merge gateway.\n"

func cmdDoctor(args []string) int {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	assets := fs.String("assets", "", "assets dir")
	logPath := fs.String("log", ".mergeone/gatelog.jsonl", "gatelog path")
	repo := fs.String("repo", getenv("GITHUB_REPOSITORY"), "owner/name to check the GitHub token against")
	url := fs.String("url", "", "a running gateway to check (health, mode, webhook signature)")
	offline := fs.Bool("offline", false, "make no network call (skip the model call, GitHub and --url)")
	asJSON := fs.Bool("json", false, "print the checks as JSON")
	if code, ok := parseFlags(fs, args, 2); !ok {
		return code
	}
	d := &doctor{ghAPI: getenvOr("MERGEONE_GITHUB_API", "https://api.github.com"), client: &http.Client{Timeout: 20 * time.Second}, offline: *offline}
	d.add("binary", "ok", "mergeone "+version, "")
	p := d.checkPolicy(*policy)
	if p != nil {
		assetsDir := *assets
		if assetsDir == "" {
			assetsDir = filepath.Dir(*policy)
		}
		spec := d.checkAssets(p, assetsDir)
		d.checkModel(p, spec, assetsDir)
		d.checkPortal(p)
	}
	d.checkGitHub(*repo, p)
	d.checkGatelog(*logPath)
	d.checkGateway(*url)
	failed, warned := 0, 0
	for _, c := range d.checks {
		switch c.Status {
		case "fail":
			failed++
		case "warn":
			warned++
		}
	}
	if *asJSON {
		b, _ := json.MarshalIndent(map[string]any{"checks": d.checks, "failed": failed, "warnings": warned}, "", "  ")
		fmt.Fprintln(stdout, string(b))
	} else {
		for _, c := range d.checks {
			mark := map[string]string{"ok": "ok  ", "warn": "warn", "fail": "FAIL", "skip": "skip"}[c.Status]
			fmt.Fprintf(stdout, "%s  %-10s %s\n", mark, c.Name, c.Detail)
			if c.Fix != "" && c.Status != "ok" {
				fmt.Fprintf(stdout, "                  → %s\n", c.Fix)
			}
		}
		switch {
		case failed > 0:
			fmt.Fprintf(stdout, "%d failed, %d warnings: this setup cannot decide a pull request yet.\n", failed, warned)
		case warned > 0:
			fmt.Fprintf(stdout, "ready, with %d warnings.\n", warned)
		default:
			fmt.Fprintln(stdout, "ready.")
		}
	}
	if failed > 0 {
		return 1
	}
	return 0
}

func getenvOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func (d *doctor) checkPolicy(path string) *engine.Policy {
	p, err := engine.LoadPolicy(path)
	if err != nil {
		fix := "mergeone init writes one"
		if !os.IsNotExist(err) && !strings.Contains(err.Error(), "no such file") {
			fix = "mergeone policy check --policy " + path + " shows the same error; docs/POLICY.md has every key"
		}
		d.add("policy", "fail", err.Error(), fix)
		return nil
	}
	detail := fmt.Sprintf("%s: mode %s, model %s, question_set %s, %d rules, on_error %s/%s", path, p.Mode, p.Model, p.QuestionSet, len(p.Rules), p.OnError.Verdict, p.OnError.Route)
	d.add("policy", "ok", detail, "")
	if p.Mode == engine.ModeShadow {
		d.add("mode", "ok", "shadow: every PR is decided and recorded, nothing is written to GitHub", "")
	} else if !p.Actions.Enabled {
		d.add("mode", "ok", "enforce, actions off: verdicts are returned and recorded; no status, label or comment is written", "")
	} else {
		d.add("mode", "ok", "enforce, actions on: status, labels and one comment per PR are written (never a merge or a close)", "")
	}
	return p
}

func (d *doctor) checkAssets(p *engine.Policy, assetsDir string) *models.Spec {
	var spec *models.Spec
	regPath := findAsset(assetsDir, "models.yaml")
	reg, err := models.LoadRegistry(regPath)
	switch {
	case err != nil && len(p.Provider) == 0 && p.Model != "mock":
		d.add("assets", "fail", "models.yaml not found near "+assetsDir, "pass --assets <dir> (the release archive's assets/ folder, or brew's share/mergeone/assets)")
		return nil
	case err == nil:
		spec = reg.Models[p.Model]
	}
	if spec == nil && len(p.Provider) == 0 && p.Model != "mock" {
		d.add("assets", "fail", fmt.Sprintf("model %q is not in %s", p.Model, regPath), "mergeone models lists the names; or give a provider: block in the policy")
		return nil
	}
	cal := "none (raw probabilities)"
	status := "ok"
	fix := ""
	switch p.Calibration {
	case "none":
	case "", "auto":
		name := fmt.Sprintf("calibration_%s_%s.json", p.Model, p.QuestionSet)
		if f := findAsset(assetsDir, name); fileExists(f) {
			cal = f
		} else {
			status, cal = "warn", "no "+name+": the rules will read raw, uncalibrated probabilities"
			fix = "use a model/question_set pair that ships a map, or run mergeone calibrate --from-gatelog once you have ≥ 200 outcomes"
		}
	default:
		if fileExists(p.Calibration) {
			cal = p.Calibration
		} else {
			status, cal, fix = "fail", "calibration file "+p.Calibration+" not found", "fix the path or set calibration: auto"
		}
	}
	d.add("assets", status, "calibration: "+cal, fix)
	return spec
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func (d *doctor) checkModel(p *engine.Policy, spec *models.Spec, assetsDir string) {
	if p.Model == "mock" {
		d.add("model", "warn", "model: mock — a stand-in for wiring checks; its verdicts mean nothing", "set model: to a real row (mergeone models)")
		return
	}
	keyEnv, optional := "", false
	if spec != nil {
		keyEnv, optional = spec.KeyEnv, spec.KeyOptional
	}
	if ke, ok := p.Provider["key_env"].(string); ok && ke != "" {
		keyEnv = ke
	}
	if keyEnv != "" && models.KeyFromEnv(keyEnv) == "" && !optional {
		d.add("model key", "fail", keyEnv+" is not set", "export "+keyEnv+"=… (or put it in a git-ignored .env and source it); never commit it")
		return
	}
	if keyEnv != "" {
		d.add("model key", "ok", keyEnv+" is set", "")
	}
	if d.offline {
		d.add("model", "skip", "--offline: no call made", "")
		return
	}
	m, err := buildModel(p, assetsDir)
	if err != nil {
		d.add("model", "fail", err.Error(), "")
		return
	}
	dec := m.Decide(doctorState, engine.Questions(p.QuestionSet))
	if dec.Error != "" {
		fix := "check the endpoint and the key"
		switch kind := store.ErrorKind(dec.Error); kind {
		case "http_4xx":
			fix = "the endpoint refused the request: the key is wrong, expired, or lacks access to this model"
		case "http_429":
			fix = "rate limited: the key works; try again, or lower the gateway's load"
		case "connect":
			fix = "cannot reach the endpoint: network, proxy, or a self-hosted server that is not running"
		}
		d.add("model", "fail", fmt.Sprintf("one call to %s failed: %.160s", m.Name(), dec.Error), fix)
		return
	}
	missing := 0
	for _, q := range engine.Questions(p.QuestionSet) {
		if _, ok := dec.Probs[q]; !ok {
			missing++
		}
	}
	if missing > 0 {
		d.add("model", "fail", fmt.Sprintf("%s answered, but %d of the %s questions are missing from the reply", m.Name(), missing, p.QuestionSet), "the model row and the question set do not match")
		return
	}
	d.add("model", "ok", fmt.Sprintf("%s answered all %d %s questions in %.0f ms ($%.6f, %s)", m.Name(), len(engine.Questions(p.QuestionSet)), p.QuestionSet, dec.LatencyMS, dec.CostUSD, dec.ModelVersion), "")
}

func (d *doctor) checkPortal(p *engine.Policy) {
	if p.Portal.URL == "" {
		return
	}
	if p.Portal.Token == "" {
		d.add("portal", "fail", "portal.url is set but the token is empty (MERGEONE_PORTAL_TOKEN unset?)", "export MERGEONE_PORTAL_TOKEN=… — the portal issues it per repository")
		return
	}
	note := "records are posted to " + p.Portal.URL + "/ingest"
	if p.LogState != "hash" {
		note += " with the rendered PR state, diff included (log_state: hash sends title and summary only)"
	}
	d.add("portal", "ok", note, "")
}

func (d *doctor) checkGitHub(repo string, p *engine.Policy) {
	tok := models.GitHubTokenFromEnv()
	if tok == "" {
		d.add("github", "warn", "GITHUB_TOKEN is not set: mergeone pr decides the local diff only; serve cannot fetch pull requests", "export GITHUB_TOKEN=… (read access to pull requests and contents; write to statuses, issues and pull requests only with actions.enabled)")
		return
	}
	if d.offline {
		d.add("github", "skip", "GITHUB_TOKEN is set; --offline: not checked against GitHub", "")
		return
	}
	if repo == "" {
		d.add("github", "warn", "GITHUB_TOKEN is set; no repository to check it against", "pass --repo owner/name")
		return
	}
	body, code, err := d.get(d.ghAPI+"/repos/"+repo, tok)
	if err != nil {
		d.add("github", "fail", "cannot reach GitHub: "+err.Error(), "")
		return
	}
	switch code {
	case 200:
	case 401:
		d.add("github", "fail", "GitHub rejected the token (401)", "the token is wrong or expired")
		return
	case 403, 404:
		d.add("github", "fail", fmt.Sprintf("the token cannot see %s (HTTP %d)", repo, code), "grant the token access to this repository (fine-grained tokens list repositories explicitly)")
		return
	default:
		d.add("github", "fail", fmt.Sprintf("GET /repos/%s: HTTP %d", repo, code), "")
		return
	}
	var r struct {
		Private     bool            `json:"private"`
		Permissions map[string]bool `json:"permissions"`
	}
	_ = json.Unmarshal(body, &r)
	// the pulls list is what the gateway reads; a token can see the repository and still lack it
	_, pc, _ := d.get(d.ghAPI+"/repos/"+repo+"/pulls?per_page=1&state=all", tok)
	if pc != 200 {
		d.add("github", "fail", fmt.Sprintf("the token sees %s but cannot read its pull requests (HTTP %d)", repo, pc), "add Pull requests: Read to the token")
		return
	}
	detail := "token can read " + repo + " and its pull requests"
	status, fix := "ok", ""
	if p != nil && p.Actions.Enabled && p.Mode != engine.ModeShadow && !r.Permissions["push"] {
		status = "warn"
		detail += "; actions are on but the token looks read-only"
		fix = "statuses, labels and the comment need write access (Commit statuses, Issues, Pull requests: Write) — or set mode: shadow"
	}
	d.add("github", status, detail, fix)
}

func (d *doctor) get(url, bearer string) ([]byte, int, error) {
	req, _ := http.NewRequest("GET", url, nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := d.client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return b, resp.StatusCode, nil
}

func (d *doctor) checkGatelog(path string) {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		d.add("gatelog", "fail", "cannot create "+dir+": "+err.Error(), "pass --log <a writable path>")
		return
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		d.add("gatelog", "fail", "cannot write "+path+": "+err.Error(), "pass --log <a writable path>")
		return
	}
	st, _ := f.Stat()
	f.Close()
	size := int64(0)
	if st != nil {
		size = st.Size()
	}
	d.add("gatelog", "ok", fmt.Sprintf("%s is writable (%d bytes)", path, size), "")
}

func (d *doctor) checkGateway(url string) {
	secret := os.Getenv("MERGEONE_WEBHOOK_SECRET")
	if url == "" {
		switch {
		case secret == "":
			d.add("webhook", "warn", "MERGEONE_WEBHOOK_SECRET is not set: a gateway on a public address answers 401 to every webhook", "export MERGEONE_WEBHOOK_SECRET=\"$(openssl rand -hex 32)\" and give GitHub the same value (only needed for mergeone serve)")
		case len(secret) < 16:
			d.add("webhook", "warn", "MERGEONE_WEBHOOK_SECRET is set but short (under 16 characters)", "use openssl rand -hex 32")
		default:
			d.add("webhook", "ok", "MERGEONE_WEBHOOK_SECRET is set (pass --url to check a running gateway against it)", "")
		}
		return
	}
	if d.offline {
		d.add("gateway", "skip", "--offline: "+url+" not contacted", "")
		return
	}
	url = strings.TrimRight(url, "/")
	body, code, err := d.get(url+"/healthz", "")
	if err != nil || code != 200 {
		d.add("gateway", "fail", fmt.Sprintf("GET %s/healthz failed (%v, HTTP %d)", url, err, code), "is mergeone serve running and reachable at this address?")
		return
	}
	var h struct {
		OK      bool   `json:"ok"`
		Mode    string `json:"mode"`
		Model   string `json:"model"`
		Breaker struct {
			State string `json:"state"`
		} `json:"breaker"`
	}
	_ = json.Unmarshal(body, &h)
	status, detail, fix := "ok", fmt.Sprintf("%s is up: mode %s, model %s", url, h.Mode, h.Model), ""
	if h.Breaker.State != "" && h.Breaker.State != "closed" {
		status, fix = "warn", "the model endpoint has been failing; PRs are held until it recovers"
		detail += ", circuit breaker " + h.Breaker.State
	}
	d.add("gateway", status, detail, fix)

	ping := []byte(`{"zen":"mergeone doctor","hook_id":0}`)
	post := func(sig string) (int, error) {
		req, _ := http.NewRequest("POST", url+"/webhook/github", bytes.NewReader(ping))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-GitHub-Event", "ping")
		req.Header.Set("X-GitHub-Delivery", fmt.Sprintf("doctor-%d", time.Now().UnixNano()))
		if sig != "" {
			req.Header.Set("X-Hub-Signature-256", sig)
		}
		resp, err := d.client.Do(req)
		if err != nil {
			return 0, err
		}
		resp.Body.Close()
		return resp.StatusCode, nil
	}
	bad, err := post("sha256=" + strings.Repeat("0", 64))
	if err != nil {
		d.add("webhook", "fail", "POST /webhook/github failed: "+err.Error(), "")
		return
	}
	if bad == 200 {
		d.add("webhook", "fail", "the gateway accepted a webhook with a wrong signature: it is not verifying deliveries", "start it with MERGEONE_WEBHOOK_SECRET set (and without --allow-unsigned-webhooks)")
		return
	}
	if secret == "" {
		d.add("webhook", "warn", fmt.Sprintf("the gateway rejects a wrong signature (HTTP %d); MERGEONE_WEBHOOK_SECRET is not set here, so the right one was not tried", bad), "export the gateway's secret here to check that it matches")
		return
	}
	good, err := post(signFor(secret, ping))
	switch {
	case err != nil:
		d.add("webhook", "fail", "POST /webhook/github failed: "+err.Error(), "")
	case good == 200:
		d.add("webhook", "ok", "the gateway verifies signatures and the secret in this environment matches (signed ping accepted, wrong signature refused)", "")
	case good == 401:
		d.add("webhook", "fail", "the gateway refused a ping signed with MERGEONE_WEBHOOK_SECRET from this environment: the two secrets differ", "set the same value on the gateway and in the repository's webhook settings")
	default:
		d.add("webhook", "fail", fmt.Sprintf("signed ping: HTTP %d", good), "")
	}
}

// signFor is the X-Hub-Signature-256 value GitHub sends for body under secret.
func signFor(secret string, body []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}
