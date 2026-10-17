package models

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func repoRoot(t *testing.T) string {
	wd, _ := os.Getwd()
	return filepath.Join(wd, "..", "..", "..")
}

func TestRegistryLoadsAndBuildsLocal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Model     string                    `json:"model"`
			Questions map[string]map[string]any `json:"questions"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if r.Header.Get("Authorization") != "" {
			t.Errorf("local model must not send a bearer header when no key is set")
		}
		ans := map[string]any{}
		for q := range body.Questions {
			ans[q] = map[string]any{"type": "noul", "noul": 0.7}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"model": "fake-1", "answers": ans, "usage": map[string]int{"input_tokens": 10}})
	}))
	defer srv.Close()
	t.Setenv("MERGEONE_LOCAL_ENDPOINT", srv.URL+"/v1/systemone")
	os.Unsetenv("MERGEONE_LOCAL_KEY")

	root := repoRoot(t)
	reg, err := LoadRegistry(filepath.Join(root, "prompts", "models.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"jev", "pplx", "pplx-flash", "haiku", "local"} {
		if reg.Models[want] == nil {
			t.Fatalf("registry missing %s", want)
		}
	}
	if reg.Models["local"].Endpoint != srv.URL+"/v1/systemone" {
		t.Fatalf("env expansion failed: %s", reg.Models["local"].Endpoint)
	}
	m, err := Build(reg.Models["local"], filepath.Join(root, "prompts", "jev_v2.json"), "", "")
	if err != nil {
		t.Fatal(err)
	}
	d := m.Decide("state", []string{"is_logic_change", "needs_human_review"})
	if d.Error != "" || d.Probs["is_logic_change"] != 0.7 || d.ModelVersion != "fake-1" {
		t.Fatalf("unexpected decision: %+v", d)
	}
	if m.Name() != "local" {
		t.Fatalf("name = %s", m.Name())
	}
}

func TestRegistryDecideDialectEnvelope(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"model": "jev-9b", "decisions": map[string]any{"weakens_tests": map[string]any{"probability": 0.2}}})
	}))
	defer srv.Close()
	s := &Spec{Name: "x", Dialect: "decide", Endpoint: srv.URL + "/v1/decide", KeyOptional: true, ModelID: "JEV-9B"}
	m, err := Build(s, filepath.Join(repoRoot(t), "prompts", "jev_v2.json"), "", "")
	if err != nil {
		t.Fatal(err)
	}
	d := m.Decide("s", []string{"weakens_tests"})
	if d.Probs["weakens_tests"] != 0.2 {
		t.Fatalf("decide dialect not parsed: %+v", d)
	}
}

func TestRegistryKeyRequired(t *testing.T) {
	os.Unsetenv("TYPESAFE_API_KEY")
	reg, _ := LoadRegistry(filepath.Join(repoRoot(t), "prompts", "models.yaml"))
	if _, err := Build(reg.Models["jev"], filepath.Join(repoRoot(t), "prompts", "jev_v2.json"), "", ""); err == nil {
		t.Fatal("expected missing-key error")
	}
}
