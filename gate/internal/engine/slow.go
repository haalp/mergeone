package engine

import (
	"math"
	"sort"
)

// The slow drift signal (docs/drift-monitor.md, PROTOCOL.md §R8 C5): a question whose ROC-AUC against the
// outcome label fell by more than R8SlowDrop from the reference window to the current one, with at least
// R8DriftMinN labelled decisions in the current window. It needs closed PRs, so it lags the fast signal and
// sees what the fast one cannot: a question that loses ranking with its mean unmoved.
const (
	R8SlowDrop  = 0.05       // AUC(current) − AUC(reference) below −0.05 flags the question
	R8SlowLabel = "iterated" // the run-5 outcome the gateway's slow signal and calibrate use by default
)

// Labelled is one question's raw answers with their outcome label, in any order.
type Labelled struct {
	Y []bool
	P []float64
}

// Add appends one labelled answer (NaN is skipped).
func (l *Labelled) Add(y bool, p float64) {
	if math.IsNaN(p) {
		return
	}
	l.Y = append(l.Y, y)
	l.P = append(l.P, p)
}

// AUC is the ROC-AUC of scores p against labels y (Mann–Whitney with average ranks for ties, the value
// sklearn.metrics.roc_auc_score gives). ok is false when one class is missing.
func AUC(y []bool, p []float64) (auc float64, ok bool) {
	n := len(p)
	idx := make([]int, n)
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool { return p[idx[a]] < p[idx[b]] })
	pos := 0
	rankSum := 0.0
	for i := 0; i < n; {
		j := i
		for j < n && p[idx[j]] == p[idx[i]] {
			j++
		}
		avg := float64(i+j+1) / 2 // ranks i+1 … j, averaged
		for k := i; k < j; k++ {
			if y[idx[k]] {
				rankSum += avg
				pos++
			}
		}
		i = j
	}
	neg := n - pos
	if pos == 0 || neg == 0 {
		return 0, false
	}
	return (rankSum - float64(pos)*float64(pos+1)/2) / (float64(pos) * float64(neg)), true
}

// SlowQuestion is one question's row in the slow block. AUCs are null (omitted) when a window has one class.
type SlowQuestion struct {
	AUC      *float64 `json:"auc"`
	RefAUC   *float64 `json:"ref_auc"`
	Delta    *float64 `json:"delta"`
	N        int      `json:"n"`
	RefN     int      `json:"ref_n"`
	Degraded bool     `json:"degraded"`
}

// SlowDrift is the slow signal for one model.
type SlowDrift struct {
	Label     string                  `json:"label"`
	Reference string                  `json:"reference"` // gatelog | replay
	Questions map[string]SlowQuestion `json:"questions"`
	Degraded  []string                `json:"degraded"`
}

// CompareSlow flags a question whose AUC fell by more than R8SlowDrop with n ≥ R8DriftMinN labelled
// answers in the current window. A window with one class has no AUC and never flags.
func CompareSlow(cur, ref Labelled) SlowQuestion {
	q := SlowQuestion{N: len(cur.P), RefN: len(ref.P)}
	ca, okC := AUC(cur.Y, cur.P)
	ra, okR := AUC(ref.Y, ref.P)
	if okC {
		q.AUC = &ca
	}
	if okR {
		q.RefAUC = &ra
	}
	if okC && okR {
		d := ca - ra
		q.Delta = &d
		q.Degraded = q.N >= R8DriftMinN && d < -R8SlowDrop
	}
	return q
}

// SlowReport compares current against reference question by question.
func SlowReport(cur, ref map[string]Labelled, label, reference string) SlowDrift {
	sd := SlowDrift{Label: label, Reference: reference, Questions: map[string]SlowQuestion{}, Degraded: []string{}}
	for q, c := range cur {
		sd.Questions[q] = CompareSlow(c, ref[q])
	}
	for q, r := range ref {
		if _, ok := sd.Questions[q]; !ok {
			sd.Questions[q] = CompareSlow(Labelled{}, r)
		}
	}
	for q, d := range sd.Questions {
		if d.Degraded {
			sd.Degraded = append(sd.Degraded, q)
		}
	}
	sort.Strings(sd.Degraded)
	return sd
}
