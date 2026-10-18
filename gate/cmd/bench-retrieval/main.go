// bench-retrieval measures retrieval v2 stage A1 in the Go store (PROTOCOL R5-H2): p99 over the run-3 dataset
// PRs against the run-4 memory, and optionally dumps every top-20 for the Python agreement check
// (python -m mergeone.eval.run5_retrieval go-compare <dump>).
//
//	go run ./cmd/bench-retrieval --memory ../data/run4/memory --dataset ../data/run3/dataset.jsonl \
//	    --out ../results/run5/retrieval_timing_go.json --dump /tmp/go_a1.jsonl
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
	"sort"
	"time"

	"github.com/haalp/mergeone/gate/internal/memory"
)

type row struct {
	PRNumber  int64  `json:"pr_number"`
	Repo      string `json:"repo"`
	Number    int    `json:"number"`
	Author    string `json:"author"`
	CreatedAt string `json:"created_at"`
	State     string `json:"state"`
	Split     string `json:"split"`
}

func pct(xs []float64, q float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	i := int(float64(len(s)) * q)
	if i >= len(s) {
		i = len(s) - 1
	}
	return s[i]
}

func main() {
	memDir := flag.String("memory", "data/run4/memory", "memory dir (<owner>__<repo>.jsonl)")
	dsPath := flag.String("dataset", "data/run3/dataset.jsonl", "run-3 dataset")
	out := flag.String("out", "results/run5/retrieval_timing_go.json", "timing JSON")
	dump := flag.String("dump", "", "optional JSONL dump of every top-20 (pr_number, candidates)")
	passes := flag.Int("passes", 2, "timed passes over the dataset (pass 1 is reported as the headline)")
	flag.Parse()

	t0 := time.Now()
	st, err := memory.Open(*memDir)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	loadMs := float64(time.Since(t0).Microseconds()) / 1000
	t1 := time.Now()
	sizes := st.Sizes()
	for name := range sizes {
		st.Repo(name).BuildV2()
	}
	indexMs := float64(time.Since(t1).Microseconds()) / 1000

	f, err := os.Open(*dsPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	var rows []row
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 256<<20)
	for sc.Scan() {
		var r row
		if err := json.Unmarshal(sc.Bytes(), &r); err == nil {
			rows = append(rows, r)
		}
	}
	f.Close()
	parsed := make([]memory.ParsedState, len(rows))
	created := make([]time.Time, len(rows))
	for i, r := range rows {
		parsed[i] = memory.ParseState(r.State)
		created[i], _ = time.Parse(time.RFC3339, r.CreatedAt)
	}

	var dumpW *bufio.Writer
	if *dump != "" {
		df, err := os.Create(*dump)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer df.Close()
		dumpW = bufio.NewWriter(df)
		defer dumpW.Flush()
	}
	runtime.GC()
	passLat := make([][]float64, 0, *passes)
	for p := 0; p < *passes; p++ {
		lat := make([]float64, 0, len(rows))
		for i, r := range rows {
			repo := st.Repo(r.Repo)
			ps := parsed[i]
			s := time.Now()
			q := memory.NewQueryV2(r.Repo, r.Number, ps.Title, ps.Body, ps.Paths, r.Author, created[i])
			cs := repo.CandidatesV2(q, memory.V2TopK)
			lat = append(lat, float64(time.Since(s).Nanoseconds())/1e6)
			if p == 0 && dumpW != nil {
				b, _ := json.Marshal(map[string]any{"pr_number": r.PRNumber, "repo": r.Repo, "number": r.Number, "candidates": cs})
				dumpW.Write(append(b, '\n'))
			}
		}
		passLat = append(passLat, lat)
	}
	w := memory.V2Weights()
	res := map[string]any{
		"measured_at":  time.Now().UTC().Format(time.RFC3339),
		"n":            len(rows),
		"go_a1_p50_ms": pct(passLat[0], 0.5), "go_a1_p99_ms": pct(passLat[0], 0.99), "go_a1_max_ms": pct(passLat[0], 1.0),
		"bar": "R5-H2: Go A1 p99 <= 20 ms", "pass": pct(passLat[0], 0.99) <= 20,
		"measured": "NewQueryV2 + Repo.CandidatesV2 (tokenise, window prune, score, top-20); memory loaded and v2 index built beforehand; state parsing excluded (as Python)",
		"passes":   len(passLat),
		"later_passes_p99_ms": func() []float64 {
			o := []float64{}
			for _, l := range passLat[1:] {
				o = append(o, pct(l, 0.99))
			}
			return o
		}(),
		"memory_load_ms": loadMs, "v2_index_build_ms": indexMs, "memory_prs": func() int {
			n := 0
			for _, v := range sizes {
				n += v
			}
			return n
		}(),
		"weights":     map[string]float64{"issue": w[0], "dir": w[1], "file": w[2], "text": w[3], "author": w[4]},
		"window_days": memory.V2WindowDays,
		"hardware":    map[string]any{"goos": runtime.GOOS, "goarch": runtime.GOARCH, "num_cpu": runtime.NumCPU(), "go": runtime.Version()},
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	_ = enc.Encode(res)
	b := buf.Bytes()
	if *out != "" {
		if err := os.WriteFile(*out, b, 0o644); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	fmt.Print(string(b))
}
