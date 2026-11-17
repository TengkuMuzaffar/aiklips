package main

// The two programs aiklips needs besides itself: uv (installs Python and the app's locked dependencies) and
// FFmpeg. Both are pinned to exact builds and checked by sha256, so a compromised mirror can't swap them.

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
)

type asset struct{ url, sha256 string }

const uvVersion = "0.13.9"

var uvBuilds = map[string]asset{
	"darwin/arm64":  {"uv-aarch64-apple-darwin.tar.gz", "5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca"},
	"darwin/amd64":  {"uv-x86_64-apple-darwin.tar.gz", "617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041"},
	"linux/amd64":   {"uv-x86_64-unknown-linux-gnu.tar.gz", "a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5"},
	"linux/arm64":   {"uv-aarch64-unknown-linux-gnu.tar.gz", "0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d"},
	"windows/amd64": {"uv-x86_64-pc-windows-msvc.zip", "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb"},
	"windows/arm64": {"uv-x86_64-pc-windows-msvc.zip", "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb"}, // x64 emulation
}

// static FFmpeg 6.1.1 builds (github.com/eugeneware/ffmpeg-static, GPL); used only when the system has no FFmpeg 6+
const ffmpegTag = "b6.1.1"

var ffmpegBuilds = map[string][2]asset{ // ffmpeg, ffprobe
	"darwin/arm64": {{"ffmpeg-darwin-arm64.gz", "8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa"},
		{"ffprobe-darwin-arm64.gz", "d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be"}},
	"darwin/amd64": {{"ffmpeg-darwin-x64.gz", "929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106"},
		{"ffprobe-darwin-x64.gz", "d4da574d6e2e197bd259b47d69cf262df9e312af24ad960444f6d806d3d4c186"}},
	"linux/amd64": {{"ffmpeg-linux-x64.gz", "bfe8a8fc511530457b528c48d77b5737527b504a3797a9bc4866aeca69c2dffa"},
		{"ffprobe-linux-x64.gz", "25d9b6ccb05e3d9de9e04e31e2506d8dd7f9f0418981965ac6df12e8d3afd067"}},
	"linux/arm64": {{"ffmpeg-linux-arm64.gz", "754a678672298bc68156adff58aa7385a592c2b30b1d0ae8750c45c915c4bac0"},
		{"ffprobe-linux-arm64.gz", "2ab6aba60ee84412dff9188720703376cb4e7aaf7e0b5e43aa8249f2acae5bf8"}},
	"windows/amd64": {{"ffmpeg-win32-x64.gz", "8883a3dffbd0a16cf4ef95206ea05283f78908dbfb118f73c83f4951dcc06d77"},
		{"ffprobe-win32-x64.gz", "f309e6223ad89d2fe54bccd420a7709b66fd27540674e92309578ed491a43c8d"}},
}

func platform() string { return runtime.GOOS + "/" + runtime.GOARCH }

func exeName(name string) string {
	if runtime.GOOS == "windows" {
		return name + ".exe"
	}
	return name
}

// ensureUV returns the path of the pinned uv, downloading it the first time (~15 MB).
func ensureUV(p paths) (string, error) {
	dest := filepath.Join(p.bin, exeName("uv"))
	stamp := dest + ".version"
	if b, err := os.ReadFile(stamp); err == nil && string(b) == uvVersion && exists(dest) {
		return dest, nil
	}
	a, ok := uvBuilds[platform()]
	if !ok {
		return "", fmt.Errorf("aiklips doesn't support %s yet", platform())
	}
	status("Downloading uv (Python installer)…")
	arc := filepath.Join(p.bin, a.url)
	url := fmt.Sprintf("https://github.com/astral-sh/uv/releases/download/%s/%s", uvVersion, a.url)
	if err := download(url, arc, a.sha256); err != nil {
		return "", err
	}
	defer os.Remove(arc)
	if err := extractMember(arc, exeName("uv"), dest); err != nil {
		return "", err
	}
	return dest, os.WriteFile(stamp, []byte(uvVersion), 0o644)
}

var ffVersionRe = regexp.MustCompile(`ffmpeg version n?(\d+)`)

func systemFFmpeg() (string, string, bool) {
	ff, err1 := exec.LookPath("ffmpeg")
	fp, err2 := exec.LookPath("ffprobe")
	if err1 != nil || err2 != nil {
		return "", "", false
	}
	out, err := hidden(exec.Command(ff, "-version")).Output()
	if err != nil {
		return "", "", false
	}
	m := ffVersionRe.FindSubmatch(out)
	if m == nil {
		return "", "", false
	}
	major, _ := strconv.Atoi(string(m[1]))
	return ff, fp, major >= 6
}

// ensureFFmpeg returns ffmpeg + ffprobe: the system's when it is version 6 or newer, else a pinned static build.
func ensureFFmpeg(p paths) (string, string, error) {
	if ff, fp, ok := systemFFmpeg(); ok {
		return ff, fp, nil
	}
	b, ok := ffmpegBuilds[platform()]
	if !ok && runtime.GOOS == "windows" {
		b, ok = ffmpegBuilds["windows/amd64"]
	}
	if !ok {
		return "", "", fmt.Errorf("no FFmpeg build for %s: install FFmpeg 6 or newer and start aiklips again", platform())
	}
	var out [2]string
	for i, name := range []string{"ffmpeg", "ffprobe"} {
		dest := filepath.Join(p.bin, exeName(name))
		stamp := dest + ".version"
		out[i] = dest
		if v, err := os.ReadFile(stamp); err == nil && string(v) == ffmpegTag && exists(dest) {
			continue
		}
		status("Downloading FFmpeg…")
		gz := dest + ".gz"
		url := fmt.Sprintf("https://github.com/eugeneware/ffmpeg-static/releases/download/%s/%s", ffmpegTag, b[i].url)
		if err := download(url, gz, b[i].sha256); err != nil {
			return "", "", err
		}
		err := gunzipTo(gz, dest)
		os.Remove(gz)
		if err != nil {
			return "", "", err
		}
		if err := os.WriteFile(stamp, []byte(ffmpegTag), 0o644); err != nil {
			return "", "", err
		}
	}
	return out[0], out[1], nil
}
