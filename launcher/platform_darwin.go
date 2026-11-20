package main

import (
	_ "embed"
	"encoding/json"
	"os"
	"os/exec"
	"strings"
)

// osascript strings: escape backslashes and quotes
func asq(s string) string { return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"` }

func notify(msg string) {
	exec.Command("osascript", "-e", "display notification "+asq(msg)+" with title \"aiklips\"").Start()
}

func alert(title, msg string) {
	exec.Command("osascript", "-e", "display alert "+asq(title)+" message "+asq(msg)).Run()
}

// the macOS download is an .app bundle the user drags to Applications: nothing to install
func selfInstall(p paths) error { return nil }

func hidden(cmd *exec.Cmd) *exec.Cmd { return cmd }

func noConsole(cmd *exec.Cmd) *exec.Cmd { return cmd }

func linkDir(target, link string) { os.Symlink(target, link) }

//go:embed ui/window.js
var windowJS string

// openWindow shows the setup page in a native window: osascript (JavaScript for Automation) with a WKWebView.
func openWindow(url string, icon []byte) (*exec.Cmd, error) {
	iconPath := ""
	if f, err := os.CreateTemp("", "aiklips-icon-*.png"); err == nil {
		f.Write(icon)
		f.Close()
		iconPath = f.Name()
	}
	js := func(s string) string { b, _ := json.Marshal(s); return string(b) }
	hiddenWin := "false"
	if os.Getenv("AIKLIPS_WINDOW_TEST") == "1" { // tests: build the window off screen, quit after 1.5 s
		hiddenWin = "true"
	}
	src := strings.NewReplacer("__URL__", js(url), "__ICON__", js(iconPath), "__HIDDEN__", hiddenWin).Replace(windowJS)
	cmd := exec.Command("osascript", "-l", "JavaScript", "-e", src)
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd, nil
}

func openPath(path string) { exec.Command("open", "-R", path).Start() }
