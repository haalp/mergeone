package server

import (
	"fmt"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// probsFor answers every question of a set (0.10, 0.11, …) so a listed row can be told from a missing one.
func probsFor(qs []string) map[string]float64 {
	p := map[string]float64{}
	for i, q := range qs {
		p[q] = 0.10 + float64(i)/100
	}
	return p
}

// listed returns the questions of qs whose "`q`" appears in body, checking they appear in qs order.
func listed(t *testing.T, body string, qs []string) []string {
	t.Helper()
	var out []string
	last := -1
	for _, q := range qs {
		i := strings.Index(body, "`"+q+"`")
		if i < 0 {
			continue
		}
		if i < last {
			t.Fatalf("%s out of order in:\n%s", q, body)
		}
		last = i
		out = append(out, q)
	}
	return out
}

// The verdict comment lists the policy's question set: all eight under v4, the five v2 ones under v2.
func TestCommentBodyListsQuestionSet(t *testing.T) {
	res := engine.Result{Verdict: "hold", Route: "human", Rule: "maintainer-judgement", Probs: probsFor(engine.QuestionsV4)}
	body := CommentBody("abc1234", "v4", res)
	if got := listed(t, body, engine.QuestionsV4); len(got) != 8 {
		t.Fatalf("v4 comment lists %d of 8 questions %v:\n%s", len(got), got, body)
	}
	for i, q := range engine.QuestionsV4 {
		if !strings.Contains(body, fmt.Sprintf("| `%s` | %.2f |", q, 0.10+float64(i)/100)) {
			t.Fatalf("v4 comment missing row for %s:\n%s", q, body)
		}
	}
	// v2 lists only v2 questions, even when the result carries more
	body = CommentBody("abc1234", "v2", res)
	for _, q := range []string{"exceeds_scope", "breaking_change", "tests_cover_change", "hard_to_revert"} {
		if strings.Contains(body, "`"+q+"`") {
			t.Fatalf("v2 comment lists %s:\n%s", q, body)
		}
	}
	// "" is v2 (the LoadPolicy default)
	if CommentBody("abc1234", "", res) != body {
		t.Fatal(`question set "" must render as v2`)
	}
	res.Probs = probsFor(engine.QuestionsV3)
	if got := listed(t, CommentBody("abc1234", "v3", res), engine.QuestionsV3); len(got) != 10 {
		t.Fatalf("v3 comment lists %d of 10: %v", len(got), got)
	}
}

// The legacy check-run summary lists the policy's question set too.
func TestPostCheckListsQuestionSet(t *testing.T) {
	f, gh := newFakeGH(t)
	res := engine.Result{Verdict: "hold", Route: "human", Rule: "r", Probs: probsFor(engine.QuestionsV4)}
	gh.PostCheck("o/r", "abc1234", "v4", res, "")
	var summary string
	for _, r := range f.reqs {
		if r.Method == "POST" && strings.HasSuffix(r.Path, "/check-runs") {
			summary, _ = r.Body["output"].(map[string]any)["summary"].(string)
		}
	}
	if got := listed(t, summary, engine.QuestionsV4); len(got) != 8 {
		t.Fatalf("v4 check-run lists %d of 8 questions %v:\n%s", len(got), got, summary)
	}
}

// End to end: question_set: v4 in the policy → the posted comment has all eight questions.
func TestActionsCommentUsesPolicyQuestionSet(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, "version: 1\nmodel: mock\nquestion_set: v4\n"+testActions, gh)
	if gw.Gate.Policy.Actions.QuestionSet != "v4" {
		t.Fatalf("actions.QuestionSet = %q", gw.Gate.Policy.Actions.QuestionSet)
	}
	gw.Gate.Model = askedModel{}
	postDecide(t, gw, 12, "d4", "some state")
	if len(f.comments[12]) != 1 {
		t.Fatalf("comments: %v", f.comments[12])
	}
	if got := listed(t, f.comments[12][0], engine.QuestionsV4); len(got) != 8 {
		t.Fatalf("comment lists %d of 8 questions %v:\n%s", len(got), got, f.comments[12][0])
	}
}

// askedModel answers 0.3 to every question it is asked (the main call asks the policy's question set).
type askedModel struct{}

func (askedModel) Name() string { return "asked" }
func (askedModel) Decide(state string, qs []string) engine.Decision {
	p := map[string]float64{}
	for _, q := range qs {
		p[q] = 0.3
	}
	return engine.Decision{Probs: p, ModelVersion: "asked-1"}
}
