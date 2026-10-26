package engine

import (
	"math"
	"sort"
)

// IsotonicFit is sklearn's IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(x, y), the fit
// `gate fit-calibration` uses: points sorted by x, equal x pooled into their weighted mean, then
// pool-adjacent-violators for a non-decreasing y, clipped to [0, 1]. It returns the thresholds
// (unique x, fitted y); Predict interpolates between them linearly and clips outside.
func IsotonicFit(x, y []float64) (xs, ys []float64) {
	n := len(x)
	if n == 0 || len(y) != n {
		return nil, nil
	}
	idx := make([]int, n)
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool {
		if x[idx[a]] != x[idx[b]] {
			return x[idx[a]] < x[idx[b]]
		}
		return y[idx[a]] < y[idx[b]]
	})
	// _make_unique: one point per distinct x, y averaged, weight = count.
	var ux, uy, uw []float64
	for i := 0; i < n; {
		j, sum := i, 0.0
		for j < n && x[idx[j]] == x[idx[i]] {
			sum += y[idx[j]]
			j++
		}
		ux = append(ux, x[idx[i]])
		uy = append(uy, sum/float64(j-i))
		uw = append(uw, float64(j-i))
		i = j
	}
	// Pool adjacent violators (weighted), non-decreasing.
	type block struct {
		v, w float64
		n    int
	}
	var st []block
	for i := range uy {
		st = append(st, block{uy[i], uw[i], 1})
		for len(st) > 1 && st[len(st)-2].v > st[len(st)-1].v {
			a, b := st[len(st)-2], st[len(st)-1]
			w := a.w + b.w
			st = st[:len(st)-2]
			st = append(st, block{(a.v*a.w + b.v*b.w) / w, w, a.n + b.n})
		}
	}
	ys = make([]float64, 0, len(ux))
	for _, b := range st {
		v := math.Min(1, math.Max(0, b.v))
		for k := 0; k < b.n; k++ {
			ys = append(ys, v)
		}
	}
	return ux, ys
}

// IsotonicPredict is the fitted map at p: np.interp over the thresholds, p clipped to their range.
func IsotonicPredict(xs, ys []float64, p float64) float64 {
	if len(xs) == 0 {
		return math.NaN()
	}
	if len(xs) == 1 {
		return ys[0]
	}
	return interp(p, xs, ys)
}

// CalibrationGrid is the 101-point grid (np.linspace(0, 1, 101)) every shipped map is tabulated on.
func CalibrationGrid() []float64 {
	g := make([]float64, 101)
	for i := range g {
		g[i] = float64(i) * 0.01
	}
	g[100] = 1
	return g
}

// QuestionMap is one question's entry in calibration_<model>_<qset>.json.
type QuestionMap struct {
	X         []float64 `json:"x"`
	Y         []float64 `json:"y"`
	N         int       `json:"n"`
	Positives int       `json:"positives"`
}

// FitQuestionMap fits one question's isotonic map on raw answers vs labels and tabulates it on the grid,
// as `gate fit-calibration` writes it. ok is false when the labels have one class (the question is skipped).
func FitQuestionMap(l Labelled) (QuestionMap, bool) {
	pos := 0
	y := make([]float64, len(l.Y))
	for i, b := range l.Y {
		if b {
			pos++
			y[i] = 1
		}
	}
	if pos == 0 || pos == len(l.Y) {
		return QuestionMap{}, false
	}
	tx, ty := IsotonicFit(l.P, y)
	grid := CalibrationGrid()
	out := QuestionMap{X: grid, Y: make([]float64, len(grid)), N: len(l.Y), Positives: pos}
	for i, g := range grid {
		out.Y[i] = IsotonicPredict(tx, ty, g)
	}
	return out, true
}
