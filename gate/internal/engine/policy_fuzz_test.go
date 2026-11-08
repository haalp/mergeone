package engine_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// FuzzLoadPolicy: any bytes as a policy file either load or return an error — never a panic. The policy is
// the one file a user edits by hand and the gateway parses at start; a crash there is a gateway that does
// not start. Seeds: the shipped default policy and a handful of edge cases.
func FuzzLoadPolicy(f *testing.F) {
	if b, err := os.ReadFile(filepath.Join("..", "..", "..", "mergeone", "gate", "policy.yaml")); err == nil {
		f.Add(b)
	}
	for _, s := range []string{
		"", "version: 1\n", "rules: 3\n", "rules:\n  - when: {x: '>= nope'}\n", "debounce: -1\n", "mode: 7\n",
		"resilience: {retries: -3, backoff: soon}\n", "actions: {comment_mode: shout, on_pass: {status: maybe}}\n",
		"memory: {top_k: -1, pair_state: weird}\n", "always_human_paths: [\"\", \"**\"]\n", "max_rounds: -5\n",
		"rules:\n  - name: a\n    when: {needs_human_review: '>= 1e309'}\n    verdict: hold\n",
		"portal: {url: 'http://[::1', token: '${NOPE'}\n", "\xff\xfe\x00",
	} {
		f.Add([]byte(s))
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		p := filepath.Join(t.TempDir(), "policy.yaml")
		if err := os.WriteFile(p, data, 0o644); err != nil {
			t.Skip()
		}
		pol, err := engine.LoadPolicy(p)
		if err == nil && pol == nil {
			t.Fatal("nil policy without an error")
		}
		if err == nil {
			_ = pol.Hash()
			_ = pol.Shadow()
			g := &engine.Gate{Policy: pol, Cal: &engine.Calibrator{}}
			_, _ = g.Match(map[string]float64{"needs_human_review": 0.5})
		}
	})
}
