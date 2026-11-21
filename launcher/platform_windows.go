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

// noConsole starts the app itself: no console window (CREATE_NO_WINDOW), but not "start hidden". Windows applies
// a hidden start (STARTF_USESHOWWINDOW + SW_HIDE) to the first window the process shows, which made the editor's
// own window invisible.
func noConsole(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

func psq(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

func messageBox(title, msg string) *exec.Cmd {
	return hidden(exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command",
		"Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show("+psq(msg)+", "+psq(title)+")"))
}

func notify(msg string) { messageBox("aiklips", msg).Start() } // non-blocking

func alert(title, msg string) { messageBox(title, msg).Run() }

const uninstallKey = `HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\aiklips`

func winDirs() (dir, exe, startMenu, uninstallLnk, desktop string) {
	dir = filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "aiklips")
	menu := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs")
	return dir, filepath.Join(dir, "aiklips.exe"), filepath.Join(menu, "aiklips.lnk"),
		filepath.Join(menu, "Uninstall aiklips.lnk"), filepath.Join(os.Getenv("USERPROFILE"), "Desktop", "aiklips.lnk")
}

// selfInstall copies the launcher to %LOCALAPPDATA%\Programs\aiklips, adds Start menu + desktop shortcuts, and
// registers it in Settings → Apps (per user, no admin rights) with "aiklips.exe --uninstall" as its uninstaller.
func selfInstall(p paths) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	dir, exe, startMenu, uninstallLnk, desktop := winDirs()
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
	marker := filepath.Join(dir, "registered.txt") // this launcher version has registered itself
	if exists(startMenu) && readTrim(marker) == LauncherVersion {
		return nil
	}
	shortcut := func(lnk, args string) string {
		return "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + psq(lnk) + "); $s.TargetPath=" + psq(exe) +
			"; $s.Arguments=" + psq(args) + "; $s.WorkingDirectory=" + psq(dir) + "; $s.Save();"
	}
	script := shortcut(startMenu, "") + shortcut(uninstallLnk, "--uninstall")
	if !exists(startMenu) { // first install only: someone who deleted the desktop icon doesn't get it back
		script += shortcut(desktop, "")
	}
	script += "$k=" + psq(uninstallKey) + "; New-Item -Path $k -Force | Out-Null;"
	for _, kv := range [][2]string{{"DisplayName", "aiklips"}, {"DisplayVersion", LauncherVersion}, {"Publisher", "aiklips"},
		{"DisplayIcon", exe}, {"InstallLocation", dir}, {"UninstallString", `"` + exe + `" --uninstall`},
		{"URLInfoAbout", "https://aiklips.muzdev.online"}} {
		script += "New-ItemProperty -Path $k -Name " + psq(kv[0]) + " -Value " + psq(kv[1]) + " -PropertyType String -Force | Out-Null;"
	}
	script += "New-ItemProperty -Path $k -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null;" +
		"New-ItemProperty -Path $k -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null;"
	if err := hidden(exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script)).Run(); err != nil {
		return err
	}
	return os.WriteFile(marker, []byte(LauncherVersion), 0o644)
}

// removeIntegration undoes selfInstall: shortcuts, the Settings → Apps entry, and the launcher's folder. A running
// exe can't delete itself, so a hidden cmd removes the folder a few seconds after this process has exited.
func removeIntegration() error {
	dir, _, startMenu, uninstallLnk, desktop := winDirs()
	for _, f := range []string{startMenu, uninstallLnk, desktop} {
		os.Remove(f)
	}
	err := hidden(exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command",
		"Remove-Item -Path "+psq(uninstallKey)+" -Recurse -Force -ErrorAction SilentlyContinue")).Run()
	if exists(dir) {
		cmd := exec.Command(os.Getenv("ComSpec"))
		if cmd.Path == "" {
			cmd = exec.Command("cmd.exe")
		}
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 | 0x00000008, // NO_WINDOW | DETACHED
			CmdLine: `cmd.exe /c ping -n 5 127.0.0.1 >nul & rmdir /s /q "` + dir + `"`}
		if e := cmd.Start(); e != nil && err == nil {
			err = e
		}
	}
	return err
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
