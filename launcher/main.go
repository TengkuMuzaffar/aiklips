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
	"encoding/json"
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
	ui = newSetupUI(!quiet, p.log)
	logf("launcher %s on %s", LauncherVersion, platform())
	if len(os.Args) > 1 && (os.Args[1] == "--uninstall" || os.Args[1] == "uninstall") {
		os.Exit(uninstallMain(p))
	}
	for {
		code, err := run(p)
		if err == nil {
			os.Exit(code)
		}
		logf("error: %v", err)
		retry, shown := ui.fail(err)
		if retry {
			logf("retrying")
			continue
		}
		if !shown {
			warn("aiklips couldn't start", err.Error()+"\n\nDetails are in "+p.log)
		}
		os.Exit(1)
	}
}

// pendingVersion reads the staged update's version for the window title (display only: not verified yet).
func pendingVersion(p paths) string {
	raw, _ := os.ReadFile(filepath.Join(p.app, "pending", "manifest.json"))
	var m manifest
	json.Unmarshal(raw, &m)
	return m.Version
}

// plan decides which setup steps this start needs; none for an ordinary start.
func plan(first, pending, uvOK, ffOK bool, pv string) (title, sub string, steps []stepSpec) {
	if !uvOK || !ffOK {
		steps = append(steps, stepSpec{Key: "tools", Label: "Download runtime tools", Bytes: true, Weight: 25})
	}
	if first {
		steps = append(steps, stepSpec{Key: "download", Label: "Download aiklips", Bytes: true, Weight: 20},
			stepSpec{Key: "install", Label: "Install Python & libraries", Expect: 100 * time.Second, Weight: 50})
	} else if pending {
		steps = append(steps, stepSpec{Key: "verify", Label: "Verify the update's signature", Expect: time.Second, Weight: 3},
			stepSpec{Key: "install", Label: "Install aiklips " + pv, Expect: 25 * time.Second, Weight: 60})
	}
	if len(steps) == 0 {
		return "", "", nil
	}
	steps = append(steps, stepSpec{Key: "start", Label: "Start aiklips", Expect: 6 * time.Second, Weight: 5})
	switch {
	case first:
		return "Setting up aiklips", "Pick what to install while the download starts. Later starts take seconds.", steps
	case pending:
		return "Updating to " + pv, "Installing the new version. Your projects stay as they are.", steps
	}
	return "Getting aiklips ready", "Downloading the tools aiklips needs.", steps
}

func run(p paths) (int, error) {
	if err := selfInstall(p); err != nil {
		logf("shortcut install skipped: %v", err) // not fatal: the app still runs from here
	}
	unlock, err := lock(p)
	if err != nil {
		return 1, err
	}
	locked := true
	defer func() {
		if locked {
			unlock()
		}
	}()
	var uv, ff, fp string
	cur := readTrim(filepath.Join(p.app, "current"))
	pending := exists(filepath.Join(p.app, "pending", "manifest.json"))
	first := cur == "" && !pending
	uvOK, ffOK := uvReady(p), ffReady(p)
	if title, sub, steps := plan(first, pending, uvOK, ffOK, pendingVersion(p)); steps != nil {
		ui.begin(title, sub, steps)
	}
	// a first install asks which optional features to include (all ticked), while the downloads below run
	if first && featurePick == nil { // asked once per launcher run: a retry after an error keeps the answer
		featurePick = make(chan []string, 1)
		go func() {
			keys, asked := ui.chooseFeatures(defaultFeatures())
			logf("features: %v (asked: %v)", keys, asked)
			ui.setExpect("install", expectOf(keys))
			ui.setSub(fmt.Sprintf("A one-time download of about %d MB. Later starts take seconds.", sizeOf(keys)))
			featurePick <- keys
		}()
	}
	// everything that downloads runs at once: uv (then Python), FFmpeg, and on a first start the app itself
	ui.set("tools", "active")
	ui.set("download", "active")
	err = parallel(
		func() error {
			var err error
			if uv, err = ensureUV(p); err != nil {
				return err
			}
			if first {
				return pythonInstall(uv)
			}
			return nil
		},
		func() error {
			var err error
			ff, fp, err = ensureFFmpeg(p)
			if err == nil {
				ui.set("tools", "done") // uv is small and starts first; FFmpeg is the long one
			}
			return err
		},
		func() error {
			if !first {
				return nil
			}
			if err := stageLatest(p); err != nil {
				return err
			}
			ui.set("download", "done")
			return nil
		},
	)
	if err != nil {
		return 1, err
	}
	ui.set("tools", "done")
	if first {
		ui.setLabel("Waiting for your choice of features…")
		keys := <-featurePick
		featurePick <- keys
		if err := writeExtras(p, keys); err != nil {
			return 1, err
		}
		pending = true
	}
	for {
		fresh := false
		if pending {
			ui.set("verify", "active")
			v, err := installPending(p, uv)
			if err != nil {
				logf("update not installed: %v", err)
				if cur == "" || !exists(filepath.Join(p.app, "versions", cur, ".installed")) {
					return 1, err
				}
				ui.close()
				warn("Update not installed", err.Error()+"\n\naiklips keeps running version "+cur+".")
			} else {
				fresh = v != cur
				cur = v
				logf("installed %s", v)
			}
			ui.set("install", "done")
		}
		unlock() // the app runs unlocked: a second window may start while it is open
		locked = false
		start := time.Now()
		code := launch(p, cur, uv, ff, fp)
		logf("app %s exited with %d after %s", cur, code, time.Since(start).Round(time.Second))
		if code == restartCode {
			if unlock, err = lock(p); err != nil {
				return 1, err
			}
			locked = true
			pending = exists(filepath.Join(p.app, "pending", "manifest.json"))
			if title, sub, steps := plan(false, pending, true, true, pendingVersion(p)); steps != nil {
				ui.begin(title, sub, steps)
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
				locked = true
				cur, pending = prev, false
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
		"VEDIT_FFMPEG="+ff, "VEDIT_FFPROBE="+fp, "PATH="+pathVar, "VEDIT_LAUNCHER_EXE="+selfPath(),
		// the AI runs `uv run vedit ...`: it must use this install as-is, never re-resolve or re-sync it
		"UV_FROZEN=1", "UV_NO_SYNC=1", "UV_PYTHON_PREFERENCE=only-managed", "AIKLIPS_LAUNCHER_VERSION="+LauncherVersion)
	if f, err := os.OpenFile(filepath.Join(p.data, "app.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644); err == nil {
		defer f.Close()
		cmd.Stdout, cmd.Stderr = f, f
	}
	ready := filepath.Join(p.app, ".ready") // the app may create it once its window shows (AIKLIPS_READY_FILE)
	os.Remove(ready)
	cmd.Env = append(cmd.Env, "AIKLIPS_READY_FILE="+ready)
	noConsole(cmd) // not hidden(): a hidden start would hide the editor's window on Windows
	ui.set("start", "active")
	ui.setLabel("Opening aiklips…")
	if err := cmd.Start(); err != nil {
		logf("couldn't start the app: %v", err)
		return 1
	}
	exited := make(chan struct{})
	go func() { // the setup window goes once the editor's own window is up (or after a while, or if the app quits)
		deadline := time.After(15 * time.Second)
		for !exists(ready) {
			select {
			case <-exited:
				ui.finish()
				return
			case <-deadline:
				ui.finish()
				return
			case <-time.After(200 * time.Millisecond):
			}
		}
		ui.finish()
	}()
	err := cmd.Wait()
	close(exited)
	if err != nil {
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

// featurePick holds the first install's choice of optional features (see run).
var featurePick chan []string

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

// selfPath is this launcher's own file (the app starts it with --uninstall from Settings).
func selfPath() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	if r, err := filepath.EvalSymlinks(exe); err == nil {
		return r
	}
	return exe
}
