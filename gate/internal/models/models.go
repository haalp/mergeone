// Package models holds the decision backends: Jev (System One), Claude Haiku
// (structured JSON), and a deterministic mock for tests and load runs.
package models

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// ---------------------------------------------------------------- transport

var (
	transportOnce sync.Once
	transport     *http.Transport
)

// Transport is the one connection pool every decision client shares (Jev/pplx/local deciders, Haiku, and
// whatever Build returns): keep-alive to the model endpoint across PRs and across a decision's concurrent
// pair calls, HTTP/2 where offered, TLS session resumption. Proxy, dial and handshake settings are
// http.DefaultTransport's.
func Transport() *http.Transport {
	transportOnce.Do(func() {
		t := http.DefaultTransport.(*http.Transport).Clone()
		t.MaxIdleConns = 256
		t.MaxIdleConnsPerHost = 256
		t.IdleConnTimeout = 90 * time.Second
		t.ForceAttemptHTTP2 = true
		if t.TLSClientConfig == nil {
			t.TLSClientConfig = &tls.Config{}
		}
		t.TLSClientConfig.ClientSessionCache = tls.NewLRUClientSessionCache(256)
		transport = t
	})
	return transport
}

// ---------------------------------------------------------------- Jev

// JevQuestion is one typed question in prompts/jev_v2.json.
type JevQuestion struct {
	Type         string            `json:"type"`
	Instructions string            `json:"instructions"`
	Criteria     map[string]string `json:"criteria,omitempty"` // choice questions (run 4: same_change)
}

// Jev calls https://api.typesafe.ai/v1/systemone with noul questions.
type Jev struct {
	Endpoint   string
	Key        string
	ModelID    string // "jev-latest" or pinned
	Questions  map[string]JevQuestion
	Client     *http.Client
	InPerMTok  float64 // USD per million input tokens (list: 0.042)
	OutPerMTok float64
	pinned     string
	pinMu      sync.Mutex // guards pinned: pair calls for one decision run concurrently
	name       string
}

// NewJev builds a client from TYPESAFE_API_KEY and a questions file.
func NewJev(questionsPath string) (*Jev, error) {
	return newDecider("jev", "https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY", "jev-latest", 0.042, questionsPath)
}

// NewPplx builds a Perplexity Decisions API client (wire-compatible with the System One schema).
// modelID "" → pplx-decider-v1.2; "flash" → pplx-decider-flash-v1.2. Open weights exist; this is the hosted API.
func NewPplx(questionsPath, modelID string) (*Jev, error) {
	price := 0.02
	switch modelID {
	case "", "pplx-decider-v1.2":
		modelID = "pplx-decider-v1.2"
	case "flash", "pplx-decider-flash-v1.2":
		modelID, price = "pplx-decider-flash-v1.2", 0.01
	}
	j, err := newDecider("pplx", "https://api.perplexity.ai/v1/decisions", "PERPLEXITY_API_KEY", modelID, price, questionsPath)
	if err != nil {
		return nil, err
	}
	j.pinned = modelID
	return j, nil
}

func newDecider(name, endpoint, keyEnv, modelID string, inPerMTok float64, questionsPath string) (*Jev, error) {
	key := os.Getenv(keyEnv)
	if key == "" {
		return nil, fmt.Errorf("%s not set", keyEnv)
	}
	return newDeciderWithKey(name, endpoint, key, modelID, inPerMTok, questionsPath)
}

func loadQuestions(path string) (map[string]JevQuestion, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var qs map[string]JevQuestion
	if err := json.Unmarshal(b, &qs); err != nil {
		return nil, fmt.Errorf("questions %s: %w", path, err)
	}
	return qs, nil
}

func (j *Jev) Name() string {
	if j.name != "" {
		return j.name
	}
	return "jev"
}

// AddQuestions merges another questions file (e.g. prompts/jev_pair.json) into the client.
func (j *Jev) AddQuestions(path string) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var qs map[string]JevQuestion
	if err := json.Unmarshal(b, &qs); err != nil {
		return fmt.Errorf("questions %s: %w", path, err)
	}
	for k, v := range qs {
		j.Questions[k] = v
	}
	return nil
}

func (j *Jev) Decide(state string, questions []string) engine.Decision {
	qs := map[string]JevQuestion{}
	for _, q := range questions {
		if def, ok := j.Questions[q]; ok {
			qs[q] = def
		}
	}
	model := j.ModelID
	j.pinMu.Lock()
	if j.pinned != "" {
		model = j.pinned
	}
	j.pinMu.Unlock()
	body, _ := json.Marshal(map[string]any{"state": state, "model": model, "questions": qs})
	t0 := time.Now()
	req, _ := http.NewRequest("POST", j.Endpoint, bytes.NewReader(body))
	if j.Key != "" {
		req.Header.Set("Authorization", "Bearer "+j.Key)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := j.Client.Do(req)
	lat := float64(time.Since(t0).Microseconds()) / 1000
	if err != nil {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: model, Error: "ConnectError: " + err.Error()}
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: model, Error: fmt.Sprintf("HTTP %d: %.300s", resp.StatusCode, raw)}
	}
	var out struct {
		Model     string               `json:"model"`
		Answers   map[string]jevAnswer `json:"answers"`
		Decisions map[string]jevAnswer `json:"decisions"` // /v1/decide dialect
		Usage     struct {
			In  int `json:"input_tokens"`
			Out int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: model, Error: "bad JSON: " + err.Error()}
	}
	j.pinMu.Lock()
	if j.pinned == "" && out.Model != "" {
		j.pinned = out.Model
	}
	j.pinMu.Unlock()
	if out.Answers == nil {
		out.Answers = out.Decisions
	}
	probs, conf := map[string]float64{}, map[string]float64{}
	for _, q := range questions {
		a, ok := out.Answers[q]
		switch {
		case !ok:
			probs[q] = math.NaN()
		case a.Noul != nil:
			probs[q] = *a.Noul
		case a.Probability != nil:
			probs[q] = *a.Probability
		case a.Probabilities != nil:
			if q == "same_change" { // run 4: duplicate_of = P(same) + 0.5·P(subset)
				probs[q] = a.Probabilities["same"] + 0.5*a.Probabilities["subset"]
			} else {
				p, ok := a.Probabilities["yes"]
				if !ok {
					p = a.Probabilities["true"]
				}
				probs[q] = p
			}
			if a.Confidence != nil {
				conf[q] = *a.Confidence
			}
		default:
			probs[q] = math.NaN()
		}
	}
	return engine.Decision{Probs: probs, Confidence: conf, LatencyMS: lat, InputTokens: out.Usage.In, OutputTokens: out.Usage.Out,
		CostUSD: float64(out.Usage.In)*j.InPerMTok/1e6 + float64(out.Usage.Out)*j.OutPerMTok/1e6, ModelVersion: out.Model}
}

type jevAnswer struct {
	Type          string             `json:"type"`
	Noul          *float64           `json:"noul"`
	Probability   *float64           `json:"probability"` // some /v1/decide servers
	Probabilities map[string]float64 `json:"probabilities"`
	Confidence    *float64           `json:"confidence"`
}

// ---------------------------------------------------------------- Haiku

// Haiku calls the Claude Messages API with strict JSON output and thinking off.
type Haiku struct {
	name       string
	PairSystem string
	Key        string
	ModelID    string
	System     string
	Client     *http.Client
	InPerMTok  float64
	OutPerMTok float64
}

// NewHaiku builds a client from ANTHROPIC_API_KEY and prompts/haiku_v2.md.
func NewHaiku(promptPath string) (*Haiku, error) {
	key := os.Getenv("ANTHROPIC_API_KEY")
	if key == "" {
		return nil, fmt.Errorf("ANTHROPIC_API_KEY not set")
	}
	b, err := os.ReadFile(promptPath)
	if err != nil {
		return nil, err
	}
	return &Haiku{Key: key, ModelID: "claude-haiku-5-5", System: string(b), Client: &http.Client{Timeout: 60 * time.Second, Transport: Transport()}, InPerMTok: 0.10, OutPerMTok: 0.50}, nil
}

func (h *Haiku) Name() string {
	if h.name != "" {
		return h.name
	}
	return "haiku"
}

// SetPairPrompt loads prompts/haiku_pair.md, used when the only question is same_change.
func (h *Haiku) SetPairPrompt(path string) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	h.PairSystem = string(b)
	return nil
}

func (h *Haiku) Decide(state string, questions []string) engine.Decision {
	props := map[string]any{}
	pair := len(questions) == 1 && questions[0] == "same_change"
	for _, q := range questions {
		if q == "same_change" {
			opts := map[string]any{}
			for _, o := range []string{"same", "subset", "related", "unrelated"} {
				opts[o] = map[string]any{"type": "number"}
			}
			props[q] = map[string]any{"type": "object", "properties": opts, "required": []string{"same", "subset", "related", "unrelated"}, "additionalProperties": false}
			continue
		}
		props[q] = map[string]any{"type": "number"}
	}
	system := h.System
	if pair && h.PairSystem != "" {
		system = h.PairSystem
	}
	schema := map[string]any{"type": "object", "properties": props, "required": questions, "additionalProperties": false}
	body, _ := json.Marshal(map[string]any{
		"model": h.ModelID, "max_tokens": 256, "system": system,
		"thinking":      map[string]any{"type": "disabled"},
		"output_config": map[string]any{"effort": "low", "format": map[string]any{"type": "json_schema", "schema": schema}},
		"messages":      []map[string]any{{"role": "user", "content": state}},
	})
	t0 := time.Now()
	req, _ := http.NewRequest("POST", "https://api.anthropic.com/v1/messages", bytes.NewReader(body))
	req.Header.Set("x-api-key", h.Key)
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.Client.Do(req)
	lat := float64(time.Since(t0).Microseconds()) / 1000
	if err != nil {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: h.ModelID, Error: "ConnectError: " + err.Error()}
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: h.ModelID, Error: fmt.Sprintf("HTTP %d: %.300s", resp.StatusCode, raw)}
	}
	var out struct {
		Model   string `json:"model"`
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		Usage struct {
			In  int `json:"input_tokens"`
			Out int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: h.ModelID, Error: "bad JSON: " + err.Error()}
	}
	var text string
	for _, c := range out.Content {
		if c.Type == "text" {
			text = c.Text
			break
		}
	}
	var vals map[string]json.RawMessage
	if err := json.Unmarshal([]byte(text), &vals); err != nil {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: out.Model, Error: "bad model JSON: " + err.Error()}
	}
	probs := map[string]float64{}
	for _, q := range questions {
		if q == "same_change" { // run 4: normalise the 4-way distribution, duplicate_of = P(same)+0.5·P(subset)
			var d map[string]float64
			_ = json.Unmarshal(vals[q], &d)
			z := 0.0
			for _, v := range d {
				z += math.Max(0, v)
			}
			if z == 0 {
				z = 1
			}
			probs[q] = (math.Max(0, d["same"]) + 0.5*math.Max(0, d["subset"])) / z
			continue
		}
		var v float64
		_ = json.Unmarshal(vals[q], &v)
		probs[q] = math.Min(1, math.Max(0, v))
	}
	return engine.Decision{Probs: probs, LatencyMS: lat, InputTokens: out.Usage.In, OutputTokens: out.Usage.Out,
		CostUSD: float64(out.Usage.In)*h.InPerMTok/1e6 + float64(out.Usage.Out)*h.OutPerMTok/1e6, ModelVersion: out.Model}
}

// ---------------------------------------------------------------- Mock

// Mock is deterministic on its input (same state → same probabilities) and
// simulates inference latency, so load tests exercise the real pipeline.
type Mock struct {
	LatencyMS, JitterMS, ErrorRate float64
}

func (m *Mock) Name() string { return "mock" }

func (m *Mock) Decide(state string, questions []string) engine.Decision {
	sum := sha256.Sum256([]byte(state))
	rng := rand.New(rand.NewSource(int64(binary.LittleEndian.Uint64(sum[:8]))))
	t0 := time.Now()
	if m.LatencyMS > 0 {
		d := m.LatencyMS + rng.NormFloat64()*m.JitterMS
		if d > 0 {
			time.Sleep(time.Duration(d * float64(time.Millisecond)))
		}
	}
	failed := rng.Float64() < m.ErrorRate
	probs := map[string]float64{}
	for _, q := range questions {
		probs[q] = math.Round(rng.Float64()*1000) / 1000
	}
	lat := float64(time.Since(t0).Microseconds()) / 1000
	if failed {
		return engine.Decision{Probs: nan(questions), LatencyMS: lat, ModelVersion: "mock-1", Error: "MockError: simulated"}
	}
	return engine.Decision{Probs: probs, LatencyMS: lat, InputTokens: len(state) / 4, ModelVersion: "mock-1"}
}

func nan(qs []string) map[string]float64 {
	m := map[string]float64{}
	for _, q := range qs {
		m[q] = math.NaN()
	}
	return m
}
