package initcmd

import (
	"fmt"
	"io"
	"os"
	"strings"
	"unicode/utf8"
)

// Tagline is printed dim under the wordmark.
const Tagline = "One decision per merge, at swarm scale."

// ColorMode is how much colour the terminal gets.
type ColorMode int

const (
	ColorNone ColorMode = iota // NO_COLOR, or stdout is not a terminal
	Color256                   // a terminal without COLORTERM=truecolor|24bit
	ColorTrue                  // COLORTERM=truecolor|24bit
)

// DetectColor picks the colour mode from whether stdout is a terminal and the environment.
// NO_COLOR (any non-empty value, https://no-color.org) always wins.
func DetectColor(isTTY bool, getenv func(string) string) ColorMode {
	if getenv("NO_COLOR") != "" || !isTTY {
		return ColorNone
	}
	switch strings.ToLower(getenv("COLORTERM")) {
	case "truecolor", "24bit":
		return ColorTrue
	}
	return Color256
}

// IsTerminal reports whether w is a character device (a TTY). No dependency: a stat of the file mode.
func IsTerminal(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeCharDevice != 0
}

// palette holds the escape sequences for one colour mode (all empty for ColorNone).
type palette struct{ brand, light, dim, ok, bad, reset string }

func paletteFor(m ColorMode) palette {
	switch m {
	case ColorTrue:
		return palette{"\x1b[38;2;163;113;247m", "\x1b[38;2;201;170;255m", "\x1b[2m", "\x1b[32m", "\x1b[31m", "\x1b[0m"}
	case Color256:
		return palette{"\x1b[38;5;141m", "\x1b[38;5;183m", "\x1b[2m", "\x1b[32m", "\x1b[31m", "\x1b[0m"}
	}
	return palette{}
}

// glyphs is a hand-drawn five-row block font, only the letters the wordmark needs.
var glyphs = map[rune][5]string{
	'm': {
		"██   ██",
		"███ ███",
		"██ █ ██",
		"██   ██",
		"██   ██",
	},
	'e': {
		"█████",
		"██   ",
		"████ ",
		"██   ",
		"█████",
	},
	'r': {
		"█████ ",
		"██  ██",
		"█████ ",
		"██ ██ ",
		"██  ██",
	},
	'g': {
		" ████ ",
		"██    ",
		"██ ███",
		"██  ██",
		" ████ ",
	},
	'o': {
		" ████ ",
		"██  ██",
		"██  ██",
		"██  ██",
		" ████ ",
	},
	'n': {
		"███  ██",
		"████ ██",
		"██ ████",
		"██  ███",
		"██   ██",
	},
}

// wordRows renders word in the block font, one string per row, letters separated by one space.
func wordRows(word string) [5]string {
	var rows [5]string
	for i, r := range word {
		g := glyphs[r]
		for y := range rows {
			if i > 0 {
				rows[y] += " "
			}
			rows[y] += g[y]
		}
	}
	return rows
}

// BannerLines returns the banner as plain text lines (wordmark rows, then the tagline).
func BannerLines() []string {
	a, b := wordRows("merge"), wordRows("one")
	out := make([]string, 0, 6)
	for y := range a {
		out = append(out, strings.TrimRight(a[y]+" "+b[y], " "))
	}
	return append(out, Tagline)
}

// WriteBanner writes the wordmark ("merge" in brand purple, "one" lighter) and the dim tagline,
// followed by one blank line.
func WriteBanner(w io.Writer, m ColorMode) {
	p := paletteFor(m)
	a, b := wordRows("merge"), wordRows("one")
	for y := range a {
		line := p.brand + a[y] + p.reset + " " + p.light + strings.TrimRight(b[y], " ") + p.reset
		if m == ColorNone {
			line = strings.TrimRight(a[y]+" "+b[y], " ")
		}
		fmt.Fprintln(w, line)
	}
	fmt.Fprintln(w, p.dim+Tagline+p.reset)
	fmt.Fprintln(w)
}

// PrintBanner writes the banner to w with the colour mode detected for w and the process environment.
func PrintBanner(w io.Writer) {
	WriteBanner(w, DetectColor(IsTerminal(w), os.Getenv))
}

// bannerWidth is the widest banner line in columns (every rune in the font is one column wide).
func bannerWidth() int {
	n := 0
	for _, l := range BannerLines() {
		if c := utf8.RuneCountInString(l); c > n {
			n = c
		}
	}
	return n
}
