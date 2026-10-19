package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"

	"github.com/haalp/mergeone/gate/internal/initcmd"
)

// activeSession is the running session, nil on the one-shot path. Commands that own the terminal
// (mcp, loadtest) consult it.
var activeSession *session

func inSession() bool { return activeSession != nil }

// line is one read from the session's stdin: text (with its newline, if any) and the read error.
type line struct {
	text string
	err  error
}

// session is the interactive loop opened by bare `mergeone` on a terminal.
type session struct {
	out   io.Writer
	color initcmd.ColorMode
	lines <-chan line    // the only reader of stdin while the session runs
	sig   chan os.Signal // SIGINT at the prompt (and while a foreground command runs)
}

// foreground commands run until Ctrl-C; inside a session, Ctrl-C stops them and returns to the prompt.
var foreground = map[string]bool{"serve": true, "mcp": true, "loadtest": true}

// sessionHelp is what `help` prints inside a session.
var sessionHelp = [][2]string{
	{"init [flags]", "write .mergeone.yaml here (--model, --ci, --mcp, --all, --force)"},
	{"status", "what init detects: git, remote, default branch, policy, model keys"},
	{"pr [flags]", "decide this branch against --base (pass / hold / block)"},
	{"decide [flags]", "decide a rendered PR state (--state file)"},
	{"replay [flags]", "dry-run the policy over saved predictions (--predictions)"},
	{"models", "list the decision models"},
	{"index owner/name", "index a repository's PR history into memory (GITHUB_TOKEN)"},
	{"serve [flags]", "run the webhook gateway (foreground; Ctrl-C returns here)"},
	{"mcp [flags]", "run the MCP server on stdio (foreground; Ctrl-C returns here)"},
	{"loadtest [flags]", "load-test a gateway (foreground; Ctrl-C returns here)"},
	{"cd <dir>", "change the session's working directory"},
	{"clear", "clear the screen"},
	{"version", "print the version"},
	{"help", "this list"},
	{"quit", "leave (also exit, q, Ctrl-D)"},
}

// runSession runs the loop on stdin/stdout until quit, Ctrl-D or Ctrl-C at the prompt. It returns 0.
func runSession() int {
	s := &session{out: stdout, color: initcmd.DetectColor(initcmd.IsTerminal(stdout), getenv), sig: make(chan os.Signal, 1)}
	s.lines = readLines(stdin)
	activeSession = s
	defer func() { activeSession = nil }()
	signal.Notify(s.sig, os.Interrupt)
	defer signal.Stop(s.sig)

	hint := "type help · init sets up this repo · quit leaves"
	if _, err := os.Stat(initcmd.PolicyFile); err != nil {
		hint += " · no " + initcmd.PolicyFile + " here yet — run init"
	}
	fmt.Fprintln(s.out, initcmd.Dim(s.color, hint))

	prompt := initcmd.Brand(s.color, "mergeone") + " › "
	for {
		fmt.Fprint(s.out, prompt)
		select {
		case <-s.sig: // Ctrl-C while idle: leave on a fresh line
			fmt.Fprintln(s.out)
			return 0
		case l := <-s.lines:
			if l.err != nil && l.text == "" { // Ctrl-D
				fmt.Fprintln(s.out)
				return 0
			}
			if !s.exec(l.text) {
				return 0
			}
		}
	}
}

// readLines starts the one goroutine that reads stdin for the whole session. It hands over one line
// at a time, so a line typed while a command runs waits for the next prompt (or for mcp).
func readLines(r io.Reader) <-chan line {
	ch := make(chan line)
	br := bufio.NewReader(r)
	go func() {
		for {
			t, err := br.ReadString('\n')
			ch <- line{t, err} // a terminal can be read again after Ctrl-D, so keep going
		}
	}()
	return ch
}

// exec runs one input line. It returns false when the session should end.
func (s *session) exec(text string) bool {
	argv, err := splitArgs(text)
	if err != nil {
		fmt.Fprintln(s.out, err)
		return true
	}
	if len(argv) == 0 {
		return true
	}
	name, args := argv[0], argv[1:]
	switch name {
	case "quit", "exit", "q":
		return false
	case "help", "-h", "--help":
		s.help()
	case "status":
		initcmd.Status(".", s.out, getenv, s.color)
	case "version":
		printVersion()
	case "cd":
		s.cd(args)
	case "clear":
		if initcmd.IsTerminal(s.out) {
			fmt.Fprint(s.out, "\x1b[H\x1b[2J")
		}
	case "init":
		// The banner is already on screen; init's checklist and next steps follow without it.
		s.run(name, append([]string{"--quiet"}, args...))
	default:
		if _, ok := commands[name]; !ok {
			fmt.Fprintf(s.out, "unknown: %s — type help\n", name)
			return true
		}
		s.run(name, args)
	}
	return true
}

// run dispatches to the subcommand's handler and reports a non-zero exit code instead of exiting.
func (s *session) run(name string, args []string) {
	var code int
	if foreground[name] && !isHelp(args) {
		fmt.Fprintln(s.out, initcmd.Dim(s.color, name+" runs in the foreground; Ctrl-C returns to the session"))
		// The handler traps SIGINT itself (interruptContext); s.sig receives the same signal and is
		// drained afterwards so it does not also end the session.
		code = commands[name](args)
		if drain(s.sig) {
			fmt.Fprintln(s.out) // the terminal echoed ^C; start the next prompt on its own line
		}
	} else {
		// A short command cannot be cancelled half-way, so Ctrl-C keeps its one-shot meaning while it
		// runs: it ends mergeone. The session's own trap is back on before the next prompt.
		signal.Stop(s.sig)
		code = commands[name](args)
		signal.Notify(s.sig, os.Interrupt)
	}
	if code != 0 {
		fmt.Fprintln(s.out, initcmd.Dim(s.color, exitNote(name, code)))
	}
}

// exitNote is how a session reports a command's exit code: "↳ exit 1 (hold)" for a verdict.
func exitNote(name string, code int) string {
	if name == "pr" || name == "decide" {
		if v, ok := map[int]string{1: "hold", 2: "block"}[code]; ok {
			return fmt.Sprintf("↳ exit %d (%s)", code, v)
		}
	}
	return fmt.Sprintf("↳ exit %d", code)
}

func (s *session) help() {
	w := 0
	for _, h := range sessionHelp {
		w = max(w, len(h[0]))
	}
	for _, h := range sessionHelp {
		fmt.Fprintf(s.out, "  %-*s  %s\n", w, h[0], h[1])
	}
	fmt.Fprintln(s.out, initcmd.Dim(s.color, "Commands take the same flags as `mergeone <command>`; add -h to list them."))
}

func (s *session) cd(args []string) {
	if len(args) > 1 {
		fmt.Fprintln(s.out, "usage: cd <dir>")
		return
	}
	dir := "~"
	if len(args) == 1 {
		dir = args[0]
	}
	if dir == "~" || strings.HasPrefix(dir, "~/") {
		home, err := os.UserHomeDir()
		if err != nil {
			fmt.Fprintln(s.out, "cd:", err)
			return
		}
		dir = filepath.Join(home, strings.TrimPrefix(dir, "~"))
	}
	if err := os.Chdir(dir); err != nil {
		fmt.Fprintln(s.out, "cd:", err)
		return
	}
	wd, _ := os.Getwd()
	fmt.Fprintln(s.out, initcmd.Dim(s.color, wd))
}

// stdinUntil hands the session's stdin to a foreground command (mcp) as a reader, until ctx is done
// or stdin reaches EOF. release must be called after the command returns: it waits for the
// forwarder, so the next line typed goes to the prompt again.
func (s *session) stdinUntil(ctx context.Context) (io.Reader, func()) {
	pr, pw := io.Pipe()
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer pw.Close()
		for {
			select {
			case <-ctx.Done():
				return
			case l := <-s.lines:
				if l.text != "" {
					if _, err := io.WriteString(pw, l.text); err != nil {
						return
					}
				}
				if l.err != nil {
					return
				}
			}
		}
	}()
	return pr, func() {
		pr.Close() // unblocks a pending write once the command has stopped reading
		<-done
	}
}

// drain empties c and reports whether it held a signal.
func drain(c chan os.Signal) bool {
	got := false
	for {
		select {
		case <-c:
			got = true
		default:
			return got
		}
	}
}

func isHelp(args []string) bool {
	for _, a := range args {
		if a == "-h" || a == "--help" || a == "-help" {
			return true
		}
	}
	return false
}

// isTTY reports whether v is a terminal (a character device).
func isTTY(v any) bool {
	f, ok := v.(*os.File)
	if !ok {
		return false
	}
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeCharDevice != 0
}

// splitArgs splits a session line into words: whitespace-separated, with '…' and "…" quoting and
// backslash escapes outside single quotes. No expansion of any kind.
func splitArgs(s string) ([]string, error) {
	var out []string
	var cur strings.Builder
	inWord := false
	var quote rune
	escaped := false
	for _, r := range s {
		switch {
		case escaped:
			cur.WriteRune(r)
			escaped = false
		case r == '\\' && quote != '\'':
			escaped, inWord = true, true
		case quote != 0:
			if r == quote {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case r == '\'' || r == '"':
			quote, inWord = r, true
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			if inWord {
				out = append(out, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}
	if quote != 0 {
		return nil, fmt.Errorf("unterminated %c quote", quote)
	}
	if inWord {
		out = append(out, cur.String())
	}
	return out, nil
}
