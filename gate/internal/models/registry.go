package models

import (
	"fmt"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/goccy/go-yaml"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// Spec is one row of prompts/models.yaml. Adding a decision model to the gateway is adding a row,
// not a Go type: every systemone/decide-dialect model is served by *Jev, every anthropic-dialect
// model by *Haiku. A policy's `provider:` block is a Spec too, so a private endpoint needs no registry edit.
type Spec struct {
	Name         string  `yaml:"name"`
	Dialect      string  `yaml:"dialect"` // systemone | decide | anthropic
	Endpoint     string  `yaml:"endpoint"`
	KeyEnv       string  `yaml:"key_env"`
	KeyOptional  bool    `yaml:"key_optional"`
	ModelID      string  `yaml:"model_id"`
	InputPerMTok float64 `yaml:"input_per_mtok"`
	OutPerMTok   float64 `yaml:"output_per_mtok"`
	Hosted       string  `yaml:"hosted"`
	Weights      string  `yaml:"weights"`
	License      string  `yaml:"license"`
	ParamsB      float64 `yaml:"params_b"`
	RPS          int     `yaml:"rps"`
	Notes        string  `yaml:"notes"`
}

// Registry is the parsed models.yaml.
type Registry struct {
	RecordedOn string           `yaml:"recorded_on"`
	Models     map[string]*Spec `yaml:"models"`
}

var envRE = regexp.MustCompile(`\$\{([A-Z0-9_]+)(?::-([^}]*))?\}`)

// ExpandEnv expands ${VAR} and ${VAR:-default}.
func ExpandEnv(s string) string {
	return envRE.ReplaceAllStringFunc(s, func(m string) string {
		g := envRE.FindStringSubmatch(m)
		if v, ok := os.LookupEnv(g[1]); ok {
			return v
		}
		return g[2]
	})
}

// LoadRegistry reads prompts/models.yaml (any path).
func LoadRegistry(path string) (*Registry, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var r Registry
	if err := yaml.Unmarshal(b, &r); err != nil {
		return nil, fmt.Errorf("registry %s: %w", path, err)
	}
	for k, s := range r.Models {
		if s == nil {
			s = &Spec{}
			r.Models[k] = s
		}
		s.Name = k
		s.Endpoint = ExpandEnv(s.Endpoint)
		s.ModelID = ExpandEnv(s.ModelID)
	}
	return &r, nil
}

// Names lists registry keys (sorted by appearance is not guaranteed; callers sort if needed).
func (r *Registry) Names() []string {
	out := make([]string, 0, len(r.Models))
	for k := range r.Models {
		out = append(out, k)
	}
	return out
}

// Build instantiates a Spec. questionsPath is the jev_<qset>.json file (systemone/decide);
// promptPath is the haiku_<qset>.md file (anthropic). modelIDOverride pins a different model id.
func Build(s *Spec, questionsPath, promptPath, modelIDOverride string) (Decider, error) {
	if s == nil {
		return nil, fmt.Errorf("nil model spec")
	}
	id := s.ModelID
	if modelIDOverride != "" {
		id = modelIDOverride
	}
	key := ""
	if s.KeyEnv != "" {
		key = os.Getenv(s.KeyEnv)
	}
	if key == "" && !s.KeyOptional {
		return nil, fmt.Errorf("model %s: %s not set", s.Name, s.KeyEnv)
	}
	switch strings.ToLower(s.Dialect) {
	case "systemone", "decide", "":
		j, err := newDecider(s.Name, s.Endpoint, s.KeyEnv, id, s.InputPerMTok, questionsPath)
		if err != nil {
			if s.KeyOptional && strings.Contains(err.Error(), "not set") {
				// local server without a key
				j, err = newDeciderWithKey(s.Name, s.Endpoint, "", id, s.InputPerMTok, questionsPath)
			}
			if err != nil {
				return nil, err
			}
		}
		j.OutPerMTok = s.OutPerMTok
		if !strings.HasSuffix(id, "-latest") {
			j.pinned = id
		}
		return j, nil
	case "anthropic":
		h, err := NewHaiku(promptPath)
		if err != nil {
			return nil, err
		}
		h.name, h.ModelID, h.InPerMTok, h.OutPerMTok = s.Name, id, s.InputPerMTok, s.OutPerMTok
		return h, nil
	}
	return nil, fmt.Errorf("model %s: unknown dialect %q", s.Name, s.Dialect)
}

// Decider is what Build returns.
type Decider = engine.Model

func newDeciderWithKey(name, endpoint, key, modelID string, inPerMTok float64, questionsPath string) (*Jev, error) {
	qs, err := loadQuestions(questionsPath)
	if err != nil {
		return nil, err
	}
	return &Jev{name: name, Endpoint: endpoint, Key: key, ModelID: modelID, Questions: qs,
		Client: &http.Client{Timeout: 30 * time.Second, Transport: Transport()}, InPerMTok: inPerMTok}, nil
}
