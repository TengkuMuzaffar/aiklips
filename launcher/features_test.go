package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestFeaturesDefaultsAndExtrasFile(t *testing.T) {
	if got := validFeatures([]string{"mask-ai", "nope", " captions", "captions"}); !reflect.DeepEqual(got, []string{"captions", "mask-ai"}) {
		t.Fatalf("validFeatures: %v", got)
	}
	t.Setenv("AIKLIPS_EXTRAS", "ai,captions")
	if got := defaultFeatures(); !reflect.DeepEqual(got, []string{"captions", "ai"}) {
		t.Fatalf("env default: %v", got)
	}
	t.Setenv("AIKLIPS_EXTRAS", "none")
	if got := defaultFeatures(); len(got) != 0 {
		t.Fatalf("none: %v", got)
	}
	p := paths{app: t.TempDir()}
	if err := writeExtras(p, []string{"mask-ai", "captions"}); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(extras(p), " "); got != "--extra captions --extra mask-ai" {
		t.Fatalf("extras: %q", got)
	}
	if sizeOf(featureKeys()) <= sizeOf(nil) || expectOf(featureKeys()) <= expectOf(nil) {
		t.Fatal("features should add size and time")
	}
}

func TestFeatureAndUninstallEndpoints(t *testing.T) {
	u := newSetupUI(false, "")
	u.token = "tk"
	srv := httptest.NewServer(u.handler())
	defer srv.Close()
	u.choosing, u.picked = true, featureKeys()
	if s := u.snap(); len(s.Features) != len(features) || len(s.Picked) != len(features) {
		t.Fatalf("all features listed and ticked while choosing: %+v", s)
	}
	post := func(path, body string) int {
		r, err := http.Post(srv.URL+path+"?t=tk", "application/json", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		return r.StatusCode
	}
	if code := post("/features", `{"extras":["captions","bogus"]}`); code != 204 {
		t.Fatalf("features: %d", code)
	}
	if got := <-u.chooseCh; !reflect.DeepEqual(got, []string{"captions"}) {
		t.Fatalf("picked: %v", got)
	}
	if code := post("/uninstall", `{"go":true,"delete_data":true}`); code != 204 {
		t.Fatalf("uninstall: %d", code)
	}
	if a := <-u.answerCh; !a.ok || !a.data {
		t.Fatalf("answer: %+v", a)
	}
	if r, _ := http.Post(srv.URL+"/features?t=wrong", "application/json", strings.NewReader(`{}`)); r.StatusCode != 404 {
		t.Fatalf("needs the token: %d", r.StatusCode)
	}
	u.choosing = false
	if s := u.snap(); s.Features != nil {
		t.Fatal("features only while choosing")
	}
}

func TestChooseWithoutWindowUsesDefault(t *testing.T) {
	u := newSetupUI(false, "")
	got, asked := u.chooseFeatures([]string{"ai"})
	if asked || !reflect.DeepEqual(got, []string{"ai"}) {
		t.Fatalf("got %v asked %v", got, asked)
	}
}

func TestUninstallKeepsDataUnlessAsked(t *testing.T) {
	root := t.TempDir()
	mk := func(rel string) string {
		f := filepath.Join(root, rel)
		os.MkdirAll(filepath.Dir(f), 0o755)
		os.WriteFile(f, []byte("x"), 0o644)
		return f
	}
	p := paths{data: filepath.Join(root, "data"), app: filepath.Join(root, "data", "app"), bin: filepath.Join(root, "data", "bin"),
		projects: filepath.Join(root, "projects")}
	venv := mk("data/app/versions/0.2.5/.venv/lib/x.py")
	uv := mk("data/bin/uv")
	lib := mk("data/library.db")
	proj := mk("projects/p1/project.json")
	ui = newSetupUI(false, "")
	if err := uninstall(p, false); err != nil {
		t.Fatal(err)
	}
	if exists(venv) || exists(uv) || exists(p.app+".removing") {
		t.Fatal("app and tools should be gone")
	}
	if !exists(lib) || !exists(proj) {
		t.Fatal("data and projects must stay without the box ticked")
	}
	if err := uninstall(p, true); err != nil {
		t.Fatal(err)
	}
	if exists(p.data) || exists(p.projects) {
		t.Fatal("ticked: data and projects deleted")
	}
	if defaultDirs(p) {
		t.Fatal("custom folders are never the default ones")
	}
}
