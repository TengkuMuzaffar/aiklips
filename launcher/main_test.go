package main

import (
	"archive/tar"
	"compress/gzip"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func withKey(t *testing.T) ed25519.PrivateKey {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	old := pubKeyB64
	pubKeyB64 = base64.StdEncoding.EncodeToString(pub)
	t.Cleanup(func() { pubKeyB64 = old })
	return priv
}

const goodManifest = `{"version":"1.2.3","payload":"aiklips-1.2.3.tar.gz","sha256":"ab","size":2,"schema":1,"min_launcher":1}`

func TestSignatureIsRequiredAndChecked(t *testing.T) {
	priv := withKey(t)
	sig := []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte(goodManifest))))
	m, err := verifyManifest([]byte(goodManifest), sig)
	if err != nil || m.Version != "1.2.3" {
		t.Fatalf("valid manifest rejected: %v", err)
	}
	tampered := strings.Replace(goodManifest, `"ab"`, `"cd"`, 1)
	if _, err := verifyManifest([]byte(tampered), sig); err == nil {
		t.Fatal("tampered manifest accepted")
	}
	if _, err := verifyManifest([]byte(goodManifest), []byte("bm9wZQ==")); err == nil {
		t.Fatal("bad signature accepted")
	}
	evil := `{"version":"../../x","payload":"a.tar.gz","sha256":"ab"}`
	if _, err := verifyManifest([]byte(evil), []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte(evil))))); err == nil {
		t.Fatal("path-like version accepted")
	}
}

func TestDevLauncherRefusesUnsignedUnlessInsecure(t *testing.T) {
	old := pubKeyB64
	pubKeyB64 = ""
	t.Cleanup(func() { pubKeyB64 = old })
	t.Setenv("AIKLIPS_DEV_INSECURE", "")
	if _, err := verifyManifest([]byte(goodManifest), nil); err == nil {
		t.Fatal("dev launcher installed unsigned code")
	}
	t.Setenv("AIKLIPS_DEV_INSECURE", "1")
	if _, err := verifyManifest([]byte(goodManifest), nil); err != nil {
		t.Fatal(err)
	}
}

func writeTarGz(t *testing.T, path string, files map[string]string) {
	f, _ := os.Create(path)
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for name, body := range files {
		tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(body)), Typeflag: tar.TypeReg})
		tw.Write([]byte(body))
	}
	tw.Close()
	gz.Close()
	f.Close()
}

func TestExtractStripsTopFolderAndBlocksEscapes(t *testing.T) {
	dir := t.TempDir()
	ok := filepath.Join(dir, "ok.tar.gz")
	writeTarGz(t, ok, map[string]string{"aiklips/pyproject.toml": "x", "aiklips/src/vedit/a.py": "y"})
	out := filepath.Join(dir, "out")
	if err := extractTarGz(ok, out, true); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(out, "src", "vedit", "a.py")); string(b) != "y" {
		t.Fatal("member not extracted to the stripped path")
	}
	bad := filepath.Join(dir, "bad.tar.gz")
	writeTarGz(t, bad, map[string]string{"aiklips/../../evil": "z"})
	if err := extractTarGz(bad, filepath.Join(dir, "out2"), true); err == nil {
		t.Fatal("path traversal accepted")
	}
	if exists(filepath.Join(dir, "evil")) {
		t.Fatal("wrote outside the target")
	}
}

func TestRollbackAndPrune(t *testing.T) {
	root := t.TempDir()
	p := paths{app: root}
	for _, v := range []string{"0.1.0", "0.2.0", "0.3.0"} {
		os.MkdirAll(filepath.Join(root, "versions", v), 0o755)
		os.WriteFile(filepath.Join(root, "versions", v, ".installed"), []byte("x"), 0o644)
	}
	os.WriteFile(filepath.Join(root, "current"), []byte("0.3.0"), 0o644)
	os.WriteFile(filepath.Join(root, "previous"), []byte("0.2.0"), 0o644)
	prune(p)
	if exists(filepath.Join(root, "versions", "0.1.0")) || !exists(filepath.Join(root, "versions", "0.2.0")) {
		t.Fatal("prune should keep exactly current + previous")
	}
	prev, err := rollback(p)
	if err != nil || prev != "0.2.0" || readTrim(filepath.Join(root, "current")) != "0.2.0" || readTrim(filepath.Join(root, "skip")) != "0.3.0" {
		t.Fatalf("rollback: %v %s", err, prev)
	}
}
