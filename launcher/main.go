// aiklips launcher: the small native program people download and start.
//
// First start: downloads uv and (if needed) FFmpeg, then the newest signed aiklips release, and lets uv install
// Python and the exact locked dependencies. Every start: installs an update the app has staged (after checking its
// signature), starts the app, and restarts it when the app asks (exit code 75). If a fresh update crashes on
// start, it goes back to the previous version on its own.
//
// Everything lives in ~/.aiklips (app/, bin/); projects in ~/aiklips-projects. See docs/design/distribution.md.
package main

import (
	_ "embed"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var (
	Repo            = "TengkuMuzaffar/aiklips" // -ldflags "-X main.Repo=owner/name"
	LauncherVersion = "dev"                    // -ldflags "-X main.LauncherVersion=1.2.3"
)

// launcherAPI is what this launcher can install; a release's manifest.min_launcher above it asks for a new launcher.
const launcherAPI = 1

const restartCode = 75 // the app's "restart me" exit code (updater.RESTART_CODE)

// crashWindow: a freshly installed version that exits with an error this soon is rolled back
const crashWindow = 20 * time.Second

//go:embed pubkey.txt
var pubKeyB64 string

type paths struct{ data, app, bin, projects, log string }

func getPaths() (paths, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return paths{}, err
	}
	data := filepath.Join(home, ".aiklips")
	if v := os.Getenv("AIKLIPS_HOME"); v != "" { // tests / portable installs
		data = v
	} else {
		adoptOldHome(filepath.Join(home, ".claude-cut"), data)
	}
	projects := filepath.Join(home, "aiklips-projects")
	if v := os.Getenv("VEDIT_PROJECTS_DIR"); v != "" {
		projects = v
	}
	p := paths{data: data, app: filepath.Join(data, "app"), bin: filepath.Join(data, "bin"), projects: projects,
		log: filepath.Join(data, "launcher.log")}
	for _, d := range []string{p.app, p.bin, p.projects, filepath.Join(p.app, "versions")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return p, err
		}
	}
	return p, nil
}

var logFile io.Writer = io.Discard

func logf(format string, a ...any) {
	fmt.Fprintf(logFile, time.Now().Format("2006-01-02 15:04:05 ")+format+"\n", a...)
}

func openLog(p paths) {
	if st, err := os.Stat(p.log); err == nil && st.Size() > 2<<20 { // keep it small
		os.Rename(p.log, p.log+".old")
	}
	if f, err := os.OpenFile(p.log, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
		logFile = f
	}
}

// status shows progress for slow first-run steps (a notification; also logged)
func status(msg string) {
	logf("%s", msg)
	if !quiet {
		notify(msg)
	}
}

var quiet = os.Getenv("AIKLIPS_QUIET") == "1" // tests / CI: log instead of showing notifications and dialogs

// warn shows a dialog (or only logs it when quiet)
func warn(title, msg string) {
	logf("%s: %s", title, msg)
	if !quiet {
		alert(title, msg)
	}
}

func main() {
	if len(os.Args) > 1 && (os.Args[1] == "--version" || os.Args[1] == "-v") {
		fmt.Println(LauncherVersion)
		return
	}
	p, err := getPaths()
	if err != nil {
		alert("aiklips couldn't start", err.Error())
		os.Exit(1)
	}
	openLog(p)
	logf("launcher %s on %s", LauncherVersion, platform())
	code, err := run(p)
	if err != nil {
		logf("error: %v", err)
		warn("aiklips couldn't start", err.Error()+"\n\nDetails are in "+p.log)
		os.Exit(1)
	}
	os.Exit(code)
}

func run(p paths) (int, error) {
	if err := selfInstall(p); err != nil {
		logf("shortcut install skipped: %v", err) // not fatal: the app still runs from here
	}
	unlock, err := lock(p)
	if err != nil {
		return 1, err
	}
	uv, err := ensureUV(p)
	if err != nil {
		unlock()
		return 1, err
	}
	ff, fp, err := ensureFFmpeg(p)
	if err != nil {
		unlock()
		return 1, err
	}
	for {
		fresh := false
		cur := readTrim(filepath.Join(p.app, "current"))
		pending := exists(filepath.Join(p.app, "pending", "manifest.json"))
		if cur == "" && !pending {
			status("Setting up aiklips. The first start downloads about 150 MB and takes a few minutes.")
			if err := stageLatest(p); err != nil {
				unlock()
				return 1, err
			}
			pending = true
		}
		if pending {
			v, err := installPending(p, uv)
			if err != nil {
				logf("update not installed: %v", err)
				if cur == "" || !exists(filepath.Join(p.app, "versions", cur, ".installed")) {
					unlock()
					return 1, err
				}
				warn("Update not installed", err.Error()+"\n\naiklips keeps running version "+cur+".")
			} else {
				fresh = v != cur
				cur = v
				logf("installed %s", v)
			}
		}
		unlock() // the app runs unlocked: a second window may start while it is open
		start := time.Now()
		code := launch(p, cur, uv, ff, fp)
		logf("app %s exited with %d after %s", cur, code, time.Since(start).Round(time.Second))
		if code == restartCode {
			if unlock, err = lock(p); err != nil {
				return 1, err
			}
			continue
		}
		if fresh && code != 0 && time.Since(start) < crashWindow {
			prev, err := rollback(p)
			if err == nil {
				warn("Update rolled back", fmt.Sprintf("aiklips %s didn't start properly, so it went back to %s.\n\nDetails are in %s", cur, prev, p.log))
				if unlock, err = lock(p); err != nil {
					return 1, err
				}
				continue
			}
		}
		return code, nil
	}
}

// launch runs the app and returns its exit code.
func launch(p paths, version, uv, ff, fp string) int {
	dir := filepath.Join(p.app, "versions", version)
	exe := venvBin(dir, "vedit")
	args := []string{"app"}
	if v := os.Getenv("AIKLIPS_APP_ARGS"); v != "" { // tests: run a quick command instead of opening the editor
		args = strings.Fields(v)
	}
	cmd := exec.Command(exe, args...)
	cmd.Dir = p.data
	sep := string(os.PathListSeparator)
	pathVar := filepath.Dir(uv) + sep + filepath.Dir(exe) + sep + os.Getenv("PATH")
	cmd.Env = append(os.Environ(),
		"VEDIT_LAUNCHER=1", "VEDIT_APP_ROOT="+p.app, "VEDIT_APP_DIR="+dir, "VEDIT_PROJECTS_DIR="+p.projects,
		"VEDIT_FFMPEG="+ff, "VEDIT_FFPROBE="+fp, "PATH="+pathVar,
		// the AI runs `uv run vedit ...`: it must use this install as-is, never re-resolve or re-sync it
		"UV_FROZEN=1", "UV_NO_SYNC=1", "UV_PYTHON_PREFERENCE=only-managed", "AIKLIPS_LAUNCHER_VERSION="+LauncherVersion)
	if f, err := os.OpenFile(filepath.Join(p.data, "app.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644); err == nil {
		defer f.Close()
		cmd.Stdout, cmd.Stderr = f, f
	}
	hidden(cmd)
	if err := cmd.Run(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return ee.ExitCode()
		}
		logf("couldn't start the app: %v", err)
		return 1
	}
	return 0
}

// lock makes installs exclusive: two launchers started at once must not unpack into the same folder.
func lock(p paths) (func(), error) {
	f := filepath.Join(p.app, ".lock")
	for i := 0; i < 2; i++ {
		fh, err := os.OpenFile(f, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err == nil {
			fmt.Fprintf(fh, "%d", os.Getpid())
			fh.Close()
			return func() { os.Remove(f) }, nil
		}
		if st, err := os.Stat(f); err == nil && time.Since(st.ModTime()) > 15*time.Minute {
			os.Remove(f) // left behind by a crash
			continue
		}
		break
	}
	return nil, errors.New("aiklips is already installing an update in another window; try again in a minute")
}

var isWindows = strings.HasPrefix(platform(), "windows")

// adoptOldHome moves the folder of the app's old name (claude-cut, before 0.2) to the new one, once, and leaves a
// link behind so absolute paths stored in projects keep working (same as library.adopt_old_home in the app).
func adoptOldHome(old, data string) {
	if exists(data) {
		return
	}
	if st, err := os.Lstat(old); err != nil || !st.IsDir() {
		return
	}
	if err := os.Rename(old, data); err != nil {
		return
	}
	linkDir(data, old)
}
