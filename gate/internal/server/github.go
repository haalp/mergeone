package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// GitHub is a minimal REST client: PR state in, check-runs and labels out.
type GitHub struct {
	Token  string
	API    string
	Client *http.Client
}

// NewGitHub returns nil when token is empty (gateway then runs API-only).
func NewGitHub(token string) *GitHub {
	if token == "" {
		return nil
	}
	return &GitHub{Token: token, API: "https://api.github.com", Client: &http.Client{Timeout: 30 * time.Second}}
}

func (g *GitHub) do(method, url, accept string, body any) ([]byte, int, error) {
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, url, rd)
	req.Header.Set("Authorization", "Bearer "+g.Token)
	req.Header.Set("Accept", accept)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := g.Client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return b, resp.StatusCode, nil
}

// FileStat mirrors gatebench's per-file numstat.
type FileStat struct {
	Path                  string
	Insertions, Deletions int
}

// PRState fetches a PR and renders it exactly as gatebench renders history,
// so shipped calibration maps apply. No clone is needed (the Scion idea).
func (g *GitHub) PRState(repo string, pr int) (state string, paths []string, headSHA string, err error) {
	meta, code, err := g.do("GET", fmt.Sprintf("%s/repos/%s/pulls/%d", g.API, repo, pr), "application/vnd.github+json", nil)
	if err != nil || code != 200 {
		return "", nil, "", fmt.Errorf("GET pull %d: %d %v", pr, code, err)
	}
	var m struct {
		Title string `json:"title"`
		Body  string `json:"body"`
		Head  struct {
			SHA   string `json:"sha"`
			Label string `json:"label"`
		} `json:"head"`
	}
	_ = json.Unmarshal(meta, &m)
	var files []FileStat
	for page := 1; page <= 30; page++ {
		b, code, err := g.do("GET", fmt.Sprintf("%s/repos/%s/pulls/%d/files?per_page=100&page=%d", g.API, repo, pr, page), "application/vnd.github+json", nil)
		if err != nil || code != 200 {
			break
		}
		var fs []struct {
			Filename  string `json:"filename"`
			Additions int    `json:"additions"`
			Deletions int    `json:"deletions"`
		}
		_ = json.Unmarshal(b, &fs)
		for _, f := range fs {
			files = append(files, FileStat{f.Filename, f.Additions, f.Deletions})
		}
		if len(fs) < 100 {
			break
		}
	}
	diff, _, _ := g.do("GET", fmt.Sprintf("%s/repos/%s/pulls/%d", g.API, repo, pr), "application/vnd.github.diff", nil)
	for _, f := range files {
		paths = append(paths, f.Path)
	}
	return RenderState(m.Title, m.Body, files, string(diff)), paths, m.Head.SHA, nil
}

// RenderState is the byte-for-byte equivalent of gatebench's render_state().
func RenderState(title, body string, files []FileStat, diff string) string {
	const diffLimit, maxFiles = 4000, 60
	ins, dels := 0, 0
	tops := map[string]bool{}
	for _, f := range files {
		ins += f.Insertions
		dels += f.Deletions
		tops[topDir(f.Path)] = true
	}
	if len(body) > 1500 {
		body = body[:1500]
	}
	var sb strings.Builder
	fmt.Fprintf(&sb, "PULL REQUEST TITLE: %s\n", title)
	fmt.Fprintf(&sb, "MERGE MESSAGE BODY:\n%s\n\n", strings.TrimSpace(body))
	fmt.Fprintf(&sb, "SUMMARY: %d files changed, +%d / -%d lines, %d top-level areas touched.\n\n", len(files), ins, dels, len(tops))
	sb.WriteString("FILES CHANGED (path +ins -del):\n")
	for i, f := range files {
		if i >= maxFiles {
			fmt.Fprintf(&sb, "  ... and %d more files\n", len(files)-maxFiles)
			break
		}
		fmt.Fprintf(&sb, "  %s +%d -%d\n", f.Path, f.Insertions, f.Deletions)
	}
	truncated := len(diff) > diffLimit
	if truncated {
		diff = diff[:diffLimit]
	}
	tr := ""
	if truncated {
		tr = ", truncated"
	}
	fmt.Fprintf(&sb, "\nDIFF (first %d chars%s):\n%s", len(diff), tr, diff)
	return sb.String()
}

func topDir(p string) string {
	parts := strings.Split(p, "/")
	switch {
	case parts[0] == "staging" && len(parts) > 3:
		return strings.Join(parts[:3], "/")
	case (parts[0] == "pkg" || parts[0] == "cmd" || parts[0] == "plugin" || parts[0] == "test") && len(parts) > 1:
		return strings.Join(parts[:2], "/")
	}
	return parts[0]
}

// PostCheck writes a check-run for the head sha.
func (g *GitHub) PostCheck(repo, sha string, res engine.Result, detailsURL string) {
	concl := map[string]string{"pass": "success", "hold": "neutral", "block": "failure"}[res.Verdict]
	var sb strings.Builder
	fmt.Fprintf(&sb, "**%s** → route `%s` (rule `%s`)\n\n%s\n\n", strings.ToUpper(res.Verdict), res.Route, res.Rule, res.Reason)
	for _, q := range engine.QuestionsV2 {
		if p, ok := res.Probs[q]; ok {
			fmt.Fprintf(&sb, "- `%s`: %.2f\n", q, p)
		}
	}
	fmt.Fprintf(&sb, "\nmodel `%s` · %.0f ms", res.ModelVersion, res.LatencyMS)
	body := map[string]any{"name": "mergeone", "head_sha": sha, "status": "completed", "conclusion": concl,
		"output": map[string]any{"title": res.Verdict + " · " + res.Route, "summary": sb.String()}}
	if detailsURL != "" {
		body["details_url"] = detailsURL
	}
	_, _, _ = g.do("POST", fmt.Sprintf("%s/repos/%s/check-runs", g.API, repo), "application/vnd.github+json", body)
}

// SetLabels replaces gate:* and route:* labels on the PR.
func (g *GitHub) SetLabels(repo string, pr int, res engine.Result) {
	b, code, err := g.do("GET", fmt.Sprintf("%s/repos/%s/issues/%d/labels", g.API, repo, pr), "application/vnd.github+json", nil)
	if err != nil || code != 200 {
		return
	}
	var cur []struct {
		Name string `json:"name"`
	}
	_ = json.Unmarshal(b, &cur)
	keep := []string{}
	for _, l := range cur {
		if !strings.HasPrefix(l.Name, "gate:") && !strings.HasPrefix(l.Name, "route:") {
			keep = append(keep, l.Name)
		}
	}
	keep = append(keep, "gate:"+res.Verdict, "route:"+res.Route)
	_, _, _ = g.do("PUT", fmt.Sprintf("%s/repos/%s/issues/%d/labels", g.API, repo, pr), "application/vnd.github+json", map[string]any{"labels": keep})
}

var _ = path.Dir
