package memory

import (
	"fmt"
	"math/rand"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// SynthRepo is the repository name the synthetic memory lives under.
const SynthRepo = "synth/repo"

// synthEpoch anchors every synthetic timestamp, so retrieval (and therefore every decision) is reproducible.
var synthEpoch = time.Date(2026, 9, 1, 12, 0, 0, 0, time.UTC)

var synthWords = []string{"retry", "backoff", "cache", "timeout", "client", "server", "handler", "parser", "config", "logging",
	"metrics", "auth", "token", "session", "queue", "worker", "scheduler", "index", "storage", "migration", "schema", "router",
	"webhook", "limit", "pool", "flaky", "test", "docs", "build", "release"}

var synthDirs = []string{"pkg/http", "pkg/cache", "pkg/auth", "pkg/queue", "pkg/store", "cmd/server", "cmd/cli", "internal/api",
	"internal/sched", "docs"}

var synthAuthors = []string{"copilot", "devin-ai", "claude", "cursor-agent", "alice", "bob"}

// SyntheticPR is one synthetic change: its metadata and the rendered state the gate decides on.
type SyntheticPR struct {
	Meta  engine.PRMeta
	State string
	Files []FileStat
}

func synthPR(rng *rand.Rand, number int, created time.Time) SyntheticPR {
	dir := synthDirs[rng.Intn(len(synthDirs))]
	w := func() string { return synthWords[rng.Intn(len(synthWords))] }
	title := fmt.Sprintf("%s: fix %s %s in %s", dir[strings.LastIndex(dir, "/")+1:], w(), w(), w())
	body := fmt.Sprintf("Improves %s handling for the %s %s path. Fixes #%d", w(), w(), w(), 100+rng.Intn(40))
	nf := 1 + rng.Intn(4)
	var files []FileStat
	var paths []string
	for i := 0; i < nf; i++ {
		d := dir
		if rng.Intn(4) == 0 {
			d = synthDirs[rng.Intn(len(synthDirs))]
		}
		p := fmt.Sprintf("%s/%s_%d.go", d, w(), rng.Intn(5))
		files = append(files, FileStat{p, 1 + rng.Intn(80), rng.Intn(30)})
		paths = append(paths, p)
	}
	var sb strings.Builder
	fmt.Fprintf(&sb, "PULL REQUEST TITLE: %s\nMERGE MESSAGE BODY:\n%s\n\nFILES CHANGED:\n", title, body)
	for _, f := range files {
		fmt.Fprintf(&sb, "  %s +%d -%d\n", f.Path, f.Insertions, f.Deletions)
	}
	sb.WriteString("\nDIFF:\n")
	for i := 0; i < 40; i++ {
		fmt.Fprintf(&sb, "+\t%s := %s(%s)\n", w(), w(), w())
	}
	meta := engine.PRMeta{Repo: SynthRepo, Number: number, Title: title, Body: body, Author: synthAuthors[rng.Intn(len(synthAuthors))],
		Paths: paths, CreatedAt: created}
	return SyntheticPR{Meta: meta, State: sb.String(), Files: files}
}

// Synthetic builds an in-memory store holding n remembered PRs (#1..#n, spread over the 60 days before
// the synthetic epoch) and returns it with q fresh PRs to decide (#100001.., created at the epoch).
// Same (n, q, seed) → same store and same queries. Used by `mergeone loadtest --mock` and engine tests.
func Synthetic(n, q int, seed int64) (*Store, []SyntheticPR) {
	rng := rand.New(rand.NewSource(seed))
	s, _ := Open("")
	r := s.Repo(SynthRepo)
	states := []string{"MERGED", "MERGED", "CLOSED", "OPEN"}
	for i := 1; i <= n; i++ {
		created := synthEpoch.Add(-time.Duration(rng.Intn(60*24)) * time.Hour)
		p := synthPR(rng, i, created)
		e := EntryFromPR(p.Meta, states[rng.Intn(len(states))], p.Files)
		if e.State == "MERGED" {
			e.MergedAt = created.Add(6 * time.Hour).Format("2006-01-02T15:04:05Z")
		}
		_ = r.Upsert(e)
	}
	r.BuildV2()
	out := make([]SyntheticPR, 0, q)
	for i := 0; i < q; i++ {
		out = append(out, synthPR(rng, 100001+i, synthEpoch))
	}
	return s, out
}
