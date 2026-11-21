package main

// Uninstall: `aiklips --uninstall` (Windows: Settings → Apps, or the Start menu's "Uninstall aiklips"; every
// system: the app's Settings → General → Uninstall, which starts this and quits). It asks first, in the setup
// window, with "also delete my projects and media library" unticked. Without that box it removes only what the
// launcher installed (app/, bin/, shortcuts, the launcher itself) and keeps the person's data, so installing again
// brings everything back. uv's shared download cache and Python builds are left alone: other tools may use them.

import (
	"errors"
	"os"
	"path/filepath"
	"time"
)

// defaultDirs: only the standard folders are ever deleted wholesale, never a custom AIKLIPS_HOME /
// VEDIT_PROJECTS_DIR (it could point at a folder that holds other things).
func defaultDirs(p paths) bool {
	home, err := os.UserHomeDir()
	if err != nil {
		return false
	}
	return filepath.Clean(p.data) == filepath.Join(home, ".aiklips") &&
		filepath.Clean(p.projects) == filepath.Join(home, "aiklips-projects")
}

func uninstallMain(p paths) int {
	info := uninstallInfo{Data: p.data, Projects: p.projects, CanData: defaultDirs(p)}
	deleteData := os.Getenv("AIKLIPS_UNINSTALL_DATA") == "1" // scripted (quiet) uninstalls
	if ui.enabled {
		d, ok := ui.confirmUninstall(info)
		if !ok {
			logf("uninstall cancelled")
			ui.close()
			return 0
		}
		deleteData = d
	}
	deleteData = deleteData && info.CanData
	logf("uninstalling (delete data: %v)", deleteData)
	for {
		err := uninstall(p, deleteData)
		if err == nil {
			break
		}
		logf("uninstall: %v", err)
		retry, shown := ui.fail(err)
		if retry {
			continue
		}
		if !shown {
			warn("aiklips couldn't be uninstalled", err.Error())
		}
		return 1
	}
	sub := "Your projects and media library are still in " + p.data + " and " + p.projects + ". Install aiklips again to pick up where you left off."
	if deleteData {
		sub = "Your projects and media library were deleted too."
	}
	ui.setSub(sub)
	ui.mu.Lock()
	ui.title, ui.linger = "aiklips is uninstalled", 4*time.Second
	ui.mu.Unlock()
	ui.finish()
	return 0
}

func uninstall(p paths, deleteData bool) error {
	steps := []stepSpec{
		{Key: "close", Label: "Wait for aiklips to close", Expect: 2 * time.Second, Weight: 10},
		{Key: "app", Label: "Remove the app and its libraries", Expect: 6 * time.Second, Weight: 60},
		{Key: "shortcuts", Label: "Remove shortcuts", Expect: time.Second, Weight: 10},
	}
	if deleteData {
		steps = append(steps, stepSpec{Key: "data", Label: "Delete projects and media library", Expect: 4 * time.Second, Weight: 20})
	}
	ui.begin("Uninstalling aiklips", "This takes a few seconds.", steps)

	// 1. the app must not be running: on Windows its files are locked while it runs, so moving the folder aside
	//    fails until it has closed (the app's Uninstall button quits it just before starting this)
	ui.set("close", "active")
	os.MkdirAll(p.app, 0o755) // the lock lives in it (an earlier uninstall may have removed it)
	unlock, err := lock(p)
	if err != nil {
		return err
	}
	defer unlock()
	gone := p.app + ".removing"
	os.RemoveAll(gone) // left over from an earlier try
	deadline := time.Now().Add(30 * time.Second)
	for {
		err = os.Rename(p.app, gone)
		if err == nil || errors.Is(err, os.ErrNotExist) {
			break
		}
		if time.Now().After(deadline) {
			return errors.New("aiklips is still open. Close it (and any export it is running), then try again")
		}
		ui.setLabel("Waiting for aiklips to close…")
		time.Sleep(time.Second)
	}
	ui.set("close", "done")

	// 2. the app (every installed version and its Python environment) and the tools (uv, FFmpeg)
	ui.set("app", "active")
	ui.setLabel("Removing the app…")
	if err := os.RemoveAll(gone); err != nil {
		return err
	}
	if err := os.RemoveAll(p.bin); err != nil {
		return err
	}
	ui.set("app", "done")

	// 3. shortcuts, menu entries and the launcher itself (Windows deletes its own exe a moment after it exits)
	ui.set("shortcuts", "active")
	if err := removeIntegration(); err != nil {
		logf("shortcuts: %v", err) // not fatal: the app itself is gone
	}
	ui.set("shortcuts", "done")

	// 4. only when asked: the data folder (library, uploads, settings and keys) and the projects folder
	if deleteData {
		ui.set("data", "active")
		ui.setLabel("Deleting projects and media library…")
		if f, ok := logFile.(*os.File); ok { // the log lives in the data folder (an open file can't go on Windows)
			f.Close()
			logFile = nilWriter{}
		}
		home, _ := os.UserHomeDir()
		if st, err := os.Lstat(filepath.Join(home, ".claude-cut")); err == nil && st.Mode()&(os.ModeSymlink|os.ModeIrregular) != 0 {
			os.Remove(filepath.Join(home, ".claude-cut")) // the old name's link (adoptOldHome)
		}
		if err := os.RemoveAll(p.projects); err != nil {
			return err
		}
		if err := os.RemoveAll(p.data); err != nil {
			return err
		}
		ui.set("data", "done")
	}
	return nil
}

type nilWriter struct{}

func (nilWriter) Write(b []byte) (int, error) { return len(b), nil }
