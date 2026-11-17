package main

// Downloads with checksum verification, and archive extraction that can't write outside its target folder.

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var httpClient = &http.Client{Timeout: 30 * time.Minute}

// download fetches url into dest (via dest.part), and checks its sha256 when want is not empty.
func download(url, dest, want string) error {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "aiklips-launcher/"+LauncherVersion)
	resp, err := httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("download %s: %w (are you online?)", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("download %s: HTTP %d", url, resp.StatusCode)
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp := dest + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	h := sha256.New()
	_, err = io.Copy(io.MultiWriter(f, h), resp.Body)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return fmt.Errorf("download %s: %w", url, err)
	}
	if got := hex.EncodeToString(h.Sum(nil)); want != "" && !strings.EqualFold(got, want) {
		os.Remove(tmp)
		return fmt.Errorf("download %s: checksum mismatch (got %s, want %s)", url, got, want)
	}
	return os.Rename(tmp, dest)
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// safeJoin joins an archive member name onto dir, refusing names that escape it ("../", absolute paths).
func safeJoin(dir, name string) (string, error) {
	p := filepath.Join(dir, filepath.FromSlash(name))
	if p != filepath.Clean(dir) && !strings.HasPrefix(p, filepath.Clean(dir)+string(os.PathSeparator)) {
		return "", fmt.Errorf("archive member %q escapes the target folder", name)
	}
	return p, nil
}

// stripFirst drops the leading folder of an archive path ("aiklips/src/x.py" -> "src/x.py").
func stripFirst(name string) string {
	name = strings.TrimPrefix(name, "./")
	if i := strings.Index(name, "/"); i >= 0 {
		return name[i+1:]
	}
	return ""
}

// extractTarGz unpacks a .tar.gz into dir. strip=true removes the archive's top folder.
func extractTarGz(src, dir string, strip bool) error {
	f, err := os.Open(src)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		name := hdr.Name
		if strip {
			name = stripFirst(name)
		}
		if name == "" || hdr.Typeflag == tar.TypeXGlobalHeader {
			continue
		}
		dest, err := safeJoin(dir, name)
		if err != nil {
			return err
		}
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(dest, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := writeFile(dest, tr, os.FileMode(hdr.Mode)&0o777|0o600); err != nil {
				return err
			}
		case tar.TypeSymlink:
			// payloads have none; skip rather than risk a link pointing outside the folder
		}
	}
}

// extractMember pulls one file (matched by base name) out of a .tar.gz or .zip into dest.
func extractMember(src, base, dest string) error {
	if strings.HasSuffix(src, ".zip") {
		zr, err := zip.OpenReader(src)
		if err != nil {
			return err
		}
		defer zr.Close()
		for _, f := range zr.File {
			if filepath.Base(f.Name) == base {
				rc, err := f.Open()
				if err != nil {
					return err
				}
				defer rc.Close()
				return writeFile(dest, rc, 0o755)
			}
		}
		return fmt.Errorf("%s not found in %s", base, filepath.Base(src))
	}
	f, err := os.Open(src)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return fmt.Errorf("%s not found in %s", base, filepath.Base(src))
		}
		if err != nil {
			return err
		}
		if hdr.Typeflag == tar.TypeReg && filepath.Base(hdr.Name) == base {
			return writeFile(dest, tr, 0o755)
		}
	}
}

// gunzipTo decompresses a single-file .gz into dest (executable).
func gunzipTo(src, dest string) error {
	f, err := os.Open(src)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	return writeFile(dest, gz, 0o755)
}

func writeFile(dest string, r io.Reader, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp := dest + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	_, err = io.Copy(f, r)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, dest)
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
