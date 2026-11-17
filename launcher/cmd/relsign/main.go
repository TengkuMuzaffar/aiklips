// relsign: release signing for aiklips (used by .github/workflows/release.yml).
//
//	relsign keygen                          new key pair: prints the private key (store it as the GitHub secret
//	                                        RELEASE_SIGNING_KEY) and writes the public key to launcher/pubkey.txt
//	relsign manifest -version V -payload F [-notes FILE] [-schema N] [-min-launcher N] > manifest.json
//	relsign sign manifest.json > manifest.sig      (private key in $RELEASE_SIGNING_KEY)
//	relsign verify manifest.json manifest.sig PUBKEY_FILE
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func die(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "relsign: "+format+"\n", a...)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		die("usage: relsign keygen | manifest | sign | verify")
	}
	switch os.Args[1] {
	case "keygen":
		keygen(os.Args[2:])
	case "manifest":
		mkManifest(os.Args[2:])
	case "sign":
		sign(os.Args[2:])
	case "verify":
		verify(os.Args[2:])
	default:
		die("unknown command %q", os.Args[1])
	}
}

func keygen(args []string) {
	fs := flag.NewFlagSet("keygen", flag.ExitOnError)
	pubOut := fs.String("pub", "launcher/pubkey.txt", "where to write the public key")
	fs.Parse(args)
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		die("%v", err)
	}
	if err := os.WriteFile(*pubOut, []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
		die("%v", err)
	}
	fmt.Fprintf(os.Stderr, "Public key written to %s (commit it).\nPrivate key below: add it as the GitHub Actions secret "+
		"RELEASE_SIGNING_KEY, keep a backup somewhere safe, and never commit it.\n\n", *pubOut)
	fmt.Println(base64.StdEncoding.EncodeToString(priv.Seed()))
}

func mkManifest(args []string) {
	fs := flag.NewFlagSet("manifest", flag.ExitOnError)
	version := fs.String("version", "", "release version, e.g. 0.4.2")
	payload := fs.String("payload", "", "path to the payload .tar.gz")
	notes := fs.String("notes", "", "file with release notes")
	schema := fs.Int("schema", 1, "project.json format version this release writes")
	minLauncher := fs.Int("min-launcher", 1, "oldest launcher API that can install it")
	fs.Parse(args)
	if *version == "" || *payload == "" {
		die("manifest needs -version and -payload")
	}
	f, err := os.Open(*payload)
	if err != nil {
		die("%v", err)
	}
	h := sha256.New()
	n, err := io.Copy(h, f)
	f.Close()
	if err != nil {
		die("%v", err)
	}
	m := map[string]any{"version": strings.TrimPrefix(*version, "v"), "payload": filepath.Base(*payload),
		"sha256": hex.EncodeToString(h.Sum(nil)), "size": n, "schema": *schema, "min_launcher": *minLauncher, "notes": ""}
	if *notes != "" {
		b, err := os.ReadFile(*notes)
		if err != nil {
			die("%v", err)
		}
		m["notes"] = strings.TrimSpace(string(b))
	}
	out, _ := json.MarshalIndent(m, "", "  ")
	os.Stdout.Write(append(out, '\n'))
}

func privKey() ed25519.PrivateKey {
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(os.Getenv("RELEASE_SIGNING_KEY")))
	if err != nil || len(seed) != ed25519.SeedSize {
		die("RELEASE_SIGNING_KEY is missing or malformed (expected the base64 seed printed by `relsign keygen`)")
	}
	return ed25519.NewKeyFromSeed(seed)
}

func sign(args []string) {
	if len(args) != 1 {
		die("usage: relsign sign manifest.json")
	}
	raw, err := os.ReadFile(args[0])
	if err != nil {
		die("%v", err)
	}
	fmt.Println(base64.StdEncoding.EncodeToString(ed25519.Sign(privKey(), raw)))
}

func verify(args []string) {
	if len(args) != 3 {
		die("usage: relsign verify manifest.json manifest.sig pubkey.txt")
	}
	raw, err1 := os.ReadFile(args[0])
	sigB, err2 := os.ReadFile(args[1])
	pubB, err3 := os.ReadFile(args[2])
	if err1 != nil || err2 != nil || err3 != nil {
		die("can't read the files")
	}
	sig, err1 := base64.StdEncoding.DecodeString(strings.TrimSpace(string(sigB)))
	pub, err2 := base64.StdEncoding.DecodeString(strings.TrimSpace(string(pubB)))
	if err1 != nil || err2 != nil || len(pub) != ed25519.PublicKeySize || !ed25519.Verify(pub, raw, sig) {
		die("signature NOT valid")
	}
	fmt.Println("signature valid")
}
