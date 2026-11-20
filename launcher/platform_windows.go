package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

// hidden stops console windows flashing up for every helper process (the launcher is a GUI-subsystem program)
func hidden(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

func psq(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

func messageBox(title, msg string) *exec.Cmd {
	return hidden(exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command",
		"Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show("+psq(msg)+", "+psq(title)+")"))
}

func notify(msg string) { messageBox("aiklips", msg).Start() } // non-blocking

func alert(title, msg string) { messageBox(title, msg).Run() }

// selfInstall copies the launcher to %LOCALAPPDATA%\Programs\aiklips and adds Start menu + desktop shortcuts.
func selfInstall(p paths) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	dir := filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "aiklips")
	exe := filepath.Join(dir, "aiklips.exe")
	if !strings.EqualFold(self, exe) {
		src, err := os.Open(self)
		if err != nil {
			return err
		}
		defer src.Close()
		if err := writeFile(exe, src, 0o755); err != nil {
			return err
		}
	}
	startMenu := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "aiklips.lnk")
	if exists(startMenu) {
		return nil
	}
	desktop := filepath.Join(os.Getenv("USERPROFILE"), "Desktop", "aiklips.lnk")
	script := ""
	for _, lnk := range []string{startMenu, desktop} {
		script += "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + psq(lnk) + "); $s.TargetPath=" + psq(exe) +
			"; $s.WorkingDirectory=" + psq(dir) + "; $s.Save();"
	}
	return hidden(exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script)).Run()
}

// linkDir makes a directory junction (symlinks need admin rights on Windows; junctions don't)
func linkDir(target, link string) {
	hidden(exec.Command("cmd", "/c", "mklink", "/J", link, target)).Run()
}

// openWindow shows the setup page in Edge's app mode (always present on Windows 10/11), else Chrome, else the
// default browser.
func openWindow(url string, icon []byte) (*exec.Cmd, error) {
	var cands []string
	for _, env := range []string{"ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"} {
		if d := os.Getenv(env); d != "" {
			cands = append(cands, filepath.Join(d, "Microsoft", "Edge", "Application", "msedge.exe"),
				filepath.Join(d, "Google", "Chrome", "Application", "chrome.exe"))
		}
	}
	var found []string
	for _, c := range cands {
		if exists(c) {
			found = append(found, c)
		}
	}
	if cmd := chromiumApp(found, url); cmd != nil {
		return cmd, nil
	}
	return nil, exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
}

func openPath(path string) { exec.Command("explorer", "/select,", path).Start() }
