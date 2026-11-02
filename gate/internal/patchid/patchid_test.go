package patchid

import (
	"strings"
	"testing"
)

const onBaseA = `diff --git a/pkg/x.go b/pkg/x.go
index 1111111..2222222 100644
--- a/pkg/x.go
+++ b/pkg/x.go
@@ -10,6 +10,7 @@ func Run() {
 	a := 1
 	b := 2
-	return a + b
+	c := 3
+	return a + b + c
 }
diff --git a/pkg/y.go b/pkg/y.go
index 3333333..4444444 100644
--- a/pkg/y.go
+++ b/pkg/y.go
@@ -1,3 +1,3 @@
 package pkg
-const N = 1
+const N = 2
`

// The same patch after a rebase: new blob ids, shifted hunks, a different function context, files listed in
// another order, re-indented context and CRLF line ends.
const onBaseB = "diff --git a/pkg/y.go b/pkg/y.go\r\n" +
	"index 5555555..6666666 100644\r\n" +
	"--- a/pkg/y.go\r\n" +
	"+++ b/pkg/y.go\r\n" +
	"@@ -4,3 +4,3 @@\r\n" +
	" package pkg\r\n" +
	"-const N = 1\r\n" +
	"+const N = 2\r\n" +
	`diff --git a/pkg/x.go b/pkg/x.go
index 7777777..8888888 100644
--- a/pkg/x.go
+++ b/pkg/x.go
@@ -42,6 +42,7 @@ func RunAll() {
     a := 1
 	b  :=  2
-	return a + b
+	c := 3
+	return a + b + c
 }
`

func TestSamePatchDifferentBaseSameID(t *testing.T) {
	a, b := Diff(onBaseA), Diff(onBaseB)
	if a == "" || a != b {
		t.Fatalf("rebased patch: %q vs %q", a, b)
	}
}

func TestOneChangedLineDifferentID(t *testing.T) {
	changed := strings.Replace(onBaseA, "+const N = 2", "+const N = 3", 1)
	if Diff(changed) == Diff(onBaseA) {
		t.Fatal("a changed added line kept the id")
	}
	removed := strings.Replace(onBaseA, "-\treturn a + b\n", "-\treturn a - b\n", 1)
	if Diff(removed) == Diff(onBaseA) {
		t.Fatal("a changed removed line kept the id")
	}
	reindented := strings.Replace(onBaseA, "+\tc := 3", "+\t\tc := 3", 1)
	if Diff(reindented) == Diff(onBaseA) {
		t.Fatal("indentation of an added line is part of the patch")
	}
	renamed := strings.ReplaceAll(onBaseA, "pkg/y.go", "pkg/z.go")
	if Diff(renamed) == Diff(onBaseA) {
		t.Fatal("a different path kept the id")
	}
}

func TestEmptyAndState(t *testing.T) {
	if Diff("") != "" || Diff("no diff here\n") != "" {
		t.Fatal("no file section should give no id")
	}
	if State("hold") != "" {
		t.Fatal("a state without a diff section has no id")
	}
	st := func(diff string) string {
		return "PULL REQUEST TITLE: t\nMERGE MESSAGE BODY:\n\n\nSUMMARY: 2 files changed, +3 / -2 lines, 1 top-level areas touched.\n\n" +
			"FILES CHANGED (path +ins -del):\n  pkg/x.go +2 -1\n  pkg/y.go +1 -1\n\nDIFF (first 100 chars):\n" + diff
	}
	if a, b := State(st(onBaseA)), State(st(onBaseB)); a == "" || a != b {
		t.Fatalf("state ids: %q vs %q", a, b)
	}
	if State(st(onBaseA)) == State(strings.Replace(st(onBaseA), "pkg/x.go +2 -1", "pkg/x.go +9 -1", 1)) {
		t.Fatal("file stats are part of a state's patch")
	}
}
