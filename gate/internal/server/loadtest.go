package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

// Loadtest fires open-loop requests at a fixed rate against a running gateway.
func Loadtest(url string, rate float64, n int) error {
	client := &http.Client{Timeout: 60 * time.Second, Transport: &http.Transport{MaxIdleConnsPerHost: 512, MaxConnsPerHost: 0}}
	runID := time.Now().Unix()
	t0 := time.Now()
	type res struct {
		status    int
		latencyMS float64
	}
	results := make([]res, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		at := t0.Add(time.Duration(float64(i) / rate * float64(time.Second)))
		time.Sleep(time.Until(at))
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			state := fmt.Sprintf("PULL REQUEST TITLE: change %d\nFILES CHANGED:\n  pkg/x/%d.go +%d -%d\nDIFF: %s", i, i%50, i%40, i%7, bytes.Repeat([]byte("x"), 3000))
			body, _ := json.Marshal(map[string]any{"repo": "load/test", "pr": i, "head_sha": fmt.Sprintf("%d-%06d", runID, i), "state": state, "paths": []string{fmt.Sprintf("pkg/x/%d.go", i%50)}, "actions": false})
			s := time.Now()
			r, err := client.Post(url+"/decide", "application/json", bytes.NewReader(body))
			lat := float64(time.Since(s).Microseconds()) / 1000
			if err != nil {
				results[i] = res{0, lat}
				return
			}
			r.Body.Close()
			results[i] = res{r.StatusCode, lat}
		}(i)
	}
	wg.Wait()
	wall := time.Since(t0).Seconds()
	var lat []float64
	ok := 0
	for _, r := range results {
		if r.status == 200 || r.status == 202 {
			ok++
			lat = append(lat, r.latencyMS)
		}
	}
	sort.Float64s(lat)
	out := map[string]any{"rate_per_s": rate, "n": n, "wall_s": wall, "ok": ok, "error_rate": 1 - float64(ok)/float64(n), "achieved_rate": float64(n) / wall, "mode": "sync"}
	if len(lat) > 0 {
		out["p50_ms"] = lat[len(lat)/2]
		out["p99_ms"] = lat[min(int(float64(len(lat))*0.99), len(lat)-1)]
	}
	b, _ := json.Marshal(out)
	fmt.Println(string(b))
	return nil
}
