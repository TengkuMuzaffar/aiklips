package main

// Releases: find the newest one, stage it in <app>/pending, and install a staged one into <app>/versions/<v>.
//
// The app itself (src/vedit/updater.py) only *stages* updates. Everything that decides what code runs on this
// computer happens here: the manifest must carry a valid ed25519 signature from the release key (public half built
// into this launcher), and the payload must match the manifest's sha256.

import (
	"bufio"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type manifest struct {
	Version     string `json:"version"`
	Payload     string `json:"payload"`
	SHA256      string `json:"sha256"`
	Size        int64  `json:"size"`
	Schema      int    `json:"schema"`
	MinLauncher int    `json:"min_launcher"`
	Notes       string `json:"notes"`
}

// PubKeyOverride replaces the embedded key at build time (-ldflags "-X main.PubKeyOverride=..."; tests only).
var PubKeyOverride = ""

func publicKey() (ed25519.PublicKey, error) {
	s := strings.TrimSpace(pubKeyB64)
	if PubKeyOverride != "" {
		s = PubKeyOverride
	}
	if s == "" {
		return nil, nil
	}
	k, err := base64.StdEncoding.DecodeString(s)
	if err != nil || len(k) != ed25519.PublicKeySize {
		return nil, errors.New("this launcher's built-in release key is malformed")
	}
	return ed25519.PublicKey(k), nil
}

// verifyManifest checks the signature over the manifest's exact bytes and returns the parsed manifest.
func verifyManifest(raw, sigB64 []byte) (*manifest, error) {
	key, err := publicKey()
	if err != nil {
		return nil, err
	}
	if key == nil {
		if os.Getenv("AIKLIPS_DEV_INSECURE") != "1" {
			return nil, errors.New("this is a development launcher without a release key; it won't install unsigned code")
		}
	} else {
		sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(sigB64)))
		if err != nil || !ed25519.Verify(key, raw, sig) {
			return nil, errors.New("the update's signature is not valid; it was not installed")
		}
	}
	var m manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("bad update manifest: %w", err)
	}
	if m.Version == "" || m.Payload == "" || m.SHA256 == "" || strings.ContainsAny(m.Payload, `/\`) || strings.Contains(m.Version, "..") ||
		strings.ContainsAny(m.Version, `/\`) {
		return nil, errors.New("bad update manifest")
	}
	return &m, nil
}

// IndexURL is the release index on the project's site (written by .github/workflows/site.yml). Movable hosting, no
// GitHub API rate limit; the GitHub API is the fallback. Not trusted: only the signed manifest decides what installs.
var IndexURL = "https://aiklips.muzdev.online/latest.json"

func siteIndex() (map[string]string, error) {
	tmp, err := os.CreateTemp("", "aiklips-index-*.json")
	if err != nil {
		return nil, err
	}
	tmp.Close()
	defer os.Remove(tmp.Name())
	if err := download(IndexURL, tmp.Name(), ""); err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(tmp.Name())
	if err != nil {
		return nil, err
	}
	var idx struct {
		Version string            `json:"version"`
		Assets  map[string]string `json:"assets"`
	}
	if err := json.Unmarshal(raw, &idx); err != nil || idx.Version == "" || len(idx.Assets) == 0 {
		return nil, errors.New("bad release index")
	}
	return idx.Assets, nil
}

// releaseAssets maps asset name -> download URL for the newest published release.
// AIKLIPS_RELEASE_BASE=<url> serves manifest.json etc. from one folder instead (local testing).
func releaseAssets() (map[string]string, error) {
	if base := strings.TrimRight(os.Getenv("AIKLIPS_RELEASE_BASE"), "/"); base != "" {
		out := map[string]string{"manifest.json": base + "/manifest.json", "manifest.sig": base + "/manifest.sig"}
		return out, nil
	}
	if assets, err := siteIndex(); err == nil {
		return assets, nil
	} else {
		logf("release index unavailable (%v); asking GitHub", err)
	}
	tmp, err := os.CreateTemp("", "aiklips-release-*.json")
	if err != nil {
		return nil, err
	}
	tmp.Close()
	defer os.Remove(tmp.Name())
	if err := download("https://api.github.com/repos/"+Repo+"/releases/latest", tmp.Name(), ""); err != nil {
		return nil, fmt.Errorf("couldn't find the latest aiklips release: %w", err)
	}
	raw, err := os.ReadFile(tmp.Name())
	if err != nil {
		return nil, err
	}
	var rel struct {
		Assets []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err := json.Unmarshal(raw, &rel); err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, a := range rel.Assets {
		out[a.Name] = a.URL
	}
	return out, nil
}

// stageLatest downloads the newest release into <app>/pending (first install; later updates are staged by the app).
func stageLatest(p paths) error {
	logf("downloading the latest aiklips")
	tr := ui.track("aiklips", 8<<20) // registered now so the bar counts it from the start
	assets, err := releaseAssets()
	if err != nil {
		return err
	}
	if assets["manifest.json"] == "" || assets["manifest.sig"] == "" {
		return errors.New("the latest release has no manifest yet; try again in a few minutes")
	}
	tmp := filepath.Join(p.app, "pending.part")
	os.RemoveAll(tmp)
	for _, n := range []string{"manifest.json", "manifest.sig"} {
		if err := download(assets[n], filepath.Join(tmp, n), ""); err != nil {
			return err
		}
	}
	raw, _ := os.ReadFile(filepath.Join(tmp, "manifest.json"))
	sig, _ := os.ReadFile(filepath.Join(tmp, "manifest.sig"))
	m, err := verifyManifest(raw, sig) // fail early, before the big download
	if err != nil {
		return err
	}
	url := assets[m.Payload]
	if base := strings.TrimRight(os.Getenv("AIKLIPS_RELEASE_BASE"), "/"); base != "" {
		url = base + "/" + m.Payload
	}
	if url == "" {
		return fmt.Errorf("the release is missing %s", m.Payload)
	}
	tr.setTotal(m.Size)
	ui.setStepLabel("download", "Download aiklips "+m.Version)
	if err := downloadP(url, filepath.Join(tmp, m.Payload), m.SHA256, tr); err != nil {
		return err
	}
	pending := filepath.Join(p.app, "pending")
	os.RemoveAll(pending)
	return os.Rename(tmp, pending)
}

func venvBin(dir, name string) string {
	if isWindows {
		return filepath.Join(dir, ".venv", "Scripts", name+".exe")
	}
	return filepath.Join(dir, ".venv", "bin", name)
}

func readTrim(path string) string {
	b, _ := os.ReadFile(path)
	return strings.TrimSpace(string(b))
}

func extras(p paths) []string {
	var args []string
	for _, x := range validFeatures(strings.Fields(readTrim(filepath.Join(p.app, "extras.txt")))) {
		args = append(args, "--extra", x)
	}
	return args
}

// uvSync installs Python (managed by uv) and the locked dependencies into dir/.venv. Bytecode is compiled during the
// install (while the setup window is up) so the first start of the app is quick.
func uvSync(p paths, uv, dir string) error {
	args := append([]string{"sync", "--frozen", "--no-dev", "--python", "3.12", "--compile-bytecode"}, extras(p)...)
	cmd := hidden(exec.Command(uv, args...))
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "UV_PYTHON_PREFERENCE=only-managed")
	out, err := runLines(cmd, func(line string) {
		if l := uvLabel(line); l != "" {
			ui.setLabel(l)
		}
		if strings.HasPrefix(line, "Prepared ") {
			ui.setFrac("install", 0.85)
		}
	})
	logf("uv sync in %s:\n%s", dir, out)
	if err != nil {
		return fmt.Errorf("installing dependencies failed: %v\n%s", err, tail(out, 1200))
	}
	return nil
}

// pythonInstall fetches uv's managed Python early (in parallel with the app download), so uv sync only installs
// the libraries.
func pythonInstall(uv string) error {
	cmd := hidden(exec.Command(uv, "python", "install", "3.12"))
	cmd.Env = append(os.Environ(), "UV_PYTHON_PREFERENCE=only-managed")
	out, err := cmd.CombinedOutput()
	logf("uv python install:\n%s", out)
	if err != nil {
		return fmt.Errorf("installing Python failed: %v\n%s", err, tail(string(out), 600))
	}
	return nil
}

// runLines runs cmd, calling fn for every output line (stdout + stderr), and returns all the output.
func runLines(cmd *exec.Cmd, fn func(string)) (string, error) {
	r, w := io.Pipe()
	cmd.Stdout, cmd.Stderr = w, w
	var buf strings.Builder
	done := make(chan struct{})
	go func() {
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64<<10), 1<<20)
		for sc.Scan() {
			line := sc.Text()
			buf.WriteString(line + "\n")
			fn(strings.TrimSpace(line))
		}
		io.Copy(io.Discard, r)
		close(done)
	}()
	err := cmd.Run()
	w.Close()
	<-done
	return buf.String(), err
}

// uvLabel turns a line of uv's output into a short status ("" = not worth showing).
func uvLabel(line string) string {
	switch {
	case strings.HasPrefix(line, "Downloading "), strings.HasPrefix(line, "Downloaded "):
		return line
	case strings.HasPrefix(line, "Creating virtual environment"):
		return "Creating the app's Python environment"
	case strings.HasPrefix(line, "Prepared "):
		return "Installing libraries…"
	case strings.HasPrefix(line, "Installed "):
		return "Optimising for a fast first start…"
	case strings.HasPrefix(line, "Bytecode compiled"):
		return "Almost there…"
	}
	return ""
}

// installPending verifies and installs <app>/pending, then makes it current. Returns the installed version.
func installPending(p paths, uv string) (string, error) {
	pending := filepath.Join(p.app, "pending")
	defer os.RemoveAll(pending) // installed or rejected: never try the same staged files twice
	raw, err := os.ReadFile(filepath.Join(pending, "manifest.json"))
	if err != nil {
		return "", err
	}
	sig, _ := os.ReadFile(filepath.Join(pending, "manifest.sig"))
	m, err := verifyManifest(raw, sig)
	if err != nil {
		return "", err
	}
	if m.MinLauncher > launcherAPI {
		return "", fmt.Errorf("version %s needs a newer aiklips launcher: download it from https://github.com/%s/releases/latest", m.Version, Repo)
	}
	payload := filepath.Join(pending, m.Payload)
	if got, err := fileSHA256(payload); err != nil || !strings.EqualFold(got, m.SHA256) {
		return "", errors.New("the downloaded update is corrupt (checksum mismatch); it was not installed")
	}
	ui.set("verify", "done")
	ui.set("install", "active")
	ui.setLabel("Unpacking aiklips " + m.Version)
	dest := filepath.Join(p.app, "versions", m.Version)
	if !exists(filepath.Join(dest, ".installed")) {
		// installed in place: a virtualenv records its absolute path, so it can't be built elsewhere and moved
		os.RemoveAll(dest)
		if err := extractTarGz(payload, dest, true); err != nil {
			os.RemoveAll(dest)
			return "", fmt.Errorf("unpacking the update: %w", err)
		}
		if err := uvSync(p, uv, dest); err != nil {
			os.RemoveAll(dest)
			return "", err
		}
		ui.setLabel("Checking the install")
		out, err := hidden(exec.Command(venvBin(dest, "vedit"), "--version")).Output()
		if got := strings.TrimSpace(string(out)); err != nil || got != m.Version {
			os.RemoveAll(dest)
			return "", fmt.Errorf("the new version didn't start (smoke test: %q, %v)", got, err)
		}
		if err := os.WriteFile(filepath.Join(dest, ".installed"), []byte(time.Now().Format(time.RFC3339)), 0o644); err != nil {
			return "", err
		}
	}
	if cur := readTrim(filepath.Join(p.app, "current")); cur != "" && cur != m.Version {
		if err := os.WriteFile(filepath.Join(p.app, "previous"), []byte(cur), 0o644); err != nil {
			return "", err
		}
	}
	if err := os.WriteFile(filepath.Join(p.app, "current"), []byte(m.Version), 0o644); err != nil {
		return "", err
	}
	prune(p)
	return m.Version, nil
}

// rollback makes the previous version current again and remembers not to offer the broken one.
func rollback(p paths) (string, error) {
	cur, prev := readTrim(filepath.Join(p.app, "current")), readTrim(filepath.Join(p.app, "previous"))
	if prev == "" || !exists(filepath.Join(p.app, "versions", prev, ".installed")) {
		return "", errors.New("no previous version to go back to")
	}
	os.WriteFile(filepath.Join(p.app, "current"), []byte(prev), 0o644)
	os.WriteFile(filepath.Join(p.app, "previous"), []byte(cur), 0o644)
	os.WriteFile(filepath.Join(p.app, "skip"), []byte(cur), 0o644)
	return prev, nil
}

// prune keeps the current and previous versions and removes older ones (and half-finished installs).
func prune(p paths) {
	keep := map[string]bool{readTrim(filepath.Join(p.app, "current")): true, readTrim(filepath.Join(p.app, "previous")): true}
	entries, _ := os.ReadDir(filepath.Join(p.app, "versions"))
	names := []string{}
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	for _, n := range names {
		if !keep[n] {
			logf("removing old version %s", n)
			os.RemoveAll(filepath.Join(p.app, "versions", n))
		}
	}
}

func tail(s string, n int) string {
	if len(s) > n {
		return "…" + s[len(s)-n:]
	}
	return s
}
