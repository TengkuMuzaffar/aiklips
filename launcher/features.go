package main

// Optional features (the app's extras) the person picks on a first install. All are ticked by default; whatever is
// left out still installs itself the first time it's used (updater.install_extra). The choice goes into
// app/extras.txt, which every later `uv sync` reads (extras()).

import (
	"os"
	"path/filepath"
	"strings"
	"time"
)

type feature struct {
	Key    string `json:"key"`
	Label  string `json:"label"`
	Detail string `json:"detail"`
	MB     int    `json:"mb"`     // download size of its libraries (uv.lock wheels, rounded up)
	Expect int    `json:"expect"` // seconds it adds to the install step
}

var features = []feature{
	{"captions", "Auto-subtitles", "Speech to timed captions. Its speech model (~480 MB) comes on first use.", 75, 25},
	{"ai", "Face & body tracking", "Fast background removal, face mesh and face stickers.", 100, 25},
	{"bg-quality", "Studio background removal", "Cleaner cut-outs around hair, slower. Its model comes on first use.", 100, 30},
	{"mask-ai", "Object masks", "Exact outlines to hide or spotlight moving things. Model on first use.", 150, 60},
}

const baseMB = 200 // the app, Python and its required libraries

func featureKeys() []string {
	var out []string
	for _, f := range features {
		out = append(out, f.Key)
	}
	return out
}

// validFeatures keeps known keys only, in the registry's order (no duplicates).
func validFeatures(keys []string) []string {
	want := map[string]bool{}
	for _, k := range keys {
		want[strings.TrimSpace(k)] = true
	}
	var out []string
	for _, f := range features {
		if want[f.Key] {
			out = append(out, f.Key)
		}
	}
	return out
}

// defaultFeatures: what a first install gets when nobody is asked. AIKLIPS_EXTRAS ("captions,ai" or "none") sets
// it; quiet runs (tests, CI) get none, a person whose setup window couldn't open gets all of them.
func defaultFeatures() []string {
	if v, ok := os.LookupEnv("AIKLIPS_EXTRAS"); ok {
		return validFeatures(strings.Split(v, ","))
	}
	if quiet {
		return nil
	}
	return featureKeys()
}

func writeExtras(p paths, keys []string) error {
	return os.WriteFile(filepath.Join(p.app, "extras.txt"), []byte(strings.Join(validFeatures(keys), "\n")+"\n"), 0o644)
}

// sizeOf is the download for these features plus the base install, in MB; expectOf the install step's length.
func sizeOf(keys []string) int {
	n := baseMB
	for _, f := range features {
		for _, k := range keys {
			if f.Key == k {
				n += f.MB
			}
		}
	}
	return n
}

func expectOf(keys []string) time.Duration {
	d := 100 * time.Second
	for _, f := range features {
		for _, k := range keys {
			if f.Key == k {
				d += time.Duration(f.Expect) * time.Second
			}
		}
	}
	return d
}
