package main

import (
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

func linkDir(target, link string) { os.Symlink(target, link) }
