package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func notify(msg string) {
	if _, err := exec.LookPath("notify-send"); err == nil {
		exec.Command("notify-send", "aiklips", msg).Start()
		return
	}
	fmt.Fprintln(os.Stderr, msg)
}

func alert(title, msg string) {
	if _, err := exec.LookPath("zenity"); err == nil {
		exec.Command("zenity", "--error", "--title", title, "--text", msg, "--no-markup").Run()
		return
	}
	if _, err := exec.LookPath("kdialog"); err == nil {
		exec.Command("kdialog", "--title", title, "--error", msg).Run()
		return
	}
	fmt.Fprintf(os.Stderr, "%s: %s\n", title, msg)
}

// selfInstall copies the launcher to ~/.local/bin and adds a menu entry, so it shows up like any other app.
func selfInstall(p paths) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	home, _ := os.UserHomeDir()
	bin := filepath.Join(home, ".local", "bin", "aiklips")
	if self != bin {
		src, err := os.Open(self)
		if err != nil {
			return err
		}
		defer src.Close()
		if err := writeFile(bin, src, 0o755); err != nil {
			return err
		}
	}
	icon := filepath.Join(home, ".local", "share", "icons", "hicolor", "256x256", "apps", "aiklips.png")
	if png, err := uiFiles.ReadFile("ui/icon-256.png"); err == nil && !exists(icon) {
		os.MkdirAll(filepath.Dir(icon), 0o755)
		os.WriteFile(icon, png, 0o644)
	}
	desktop := filepath.Join(home, ".local", "share", "applications", "aiklips.desktop")
	entry := fmt.Sprintf("[Desktop Entry]\nType=Application\nName=aiklips\nComment=Video editor you drive with AI\n"+
		"Exec=%s\nIcon=aiklips\nTerminal=false\nCategories=AudioVideo;Video;AudioVideoEditing;\n", bin)
	if b, err := os.ReadFile(desktop); err == nil && string(b) == entry {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(desktop), 0o755); err != nil {
		return err
	}
	return os.WriteFile(desktop, []byte(entry), 0o644)
}

func hidden(cmd *exec.Cmd) *exec.Cmd { return cmd }

func linkDir(target, link string) { os.Symlink(target, link) }

// openWindow shows the setup page as a Chromium-family app window, else in the default browser.
func openWindow(url string, icon []byte) (*exec.Cmd, error) {
	if cmd := chromiumApp([]string{"chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
		"microsoft-edge", "microsoft-edge-stable", "brave-browser"}, url); cmd != nil {
		return cmd, nil
	}
	return nil, exec.Command("xdg-open", url).Start()
}

func openPath(path string) { exec.Command("xdg-open", filepath.Dir(path)).Start() }
