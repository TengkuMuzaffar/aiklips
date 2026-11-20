package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestProgressAggregatesDownloadsAndSteps(t *testing.T) {
	u := newSetupUI(false, "")
	u.begin("Setting up aiklips", "", []stepSpec{
		{Key: "tools", Label: "tools", Bytes: true, Weight: 50},
		{Key: "install", Label: "install", Expect: time.Hour, Weight: 50},
	})
	a, b := u.track("uv", 100), u.track("FFmpeg", 100)
	u.set("tools", "active")
	a.setTotal(200) // the server's real size replaces the estimate
	a.Write(make([]byte, 150))
	b.Write(make([]byte, 50))
	s := u.snap()
	if s.Done != 200 || s.Total != 300 || !s.Bytes {
		t.Fatalf("bytes: %+v", s)
	}
	if want := 0.5 * 200 / 300; s.Overall < want-1e-9 || s.Overall > want+1e-9 {
		t.Fatalf("overall %v, want %v", s.Overall, want)
	}
	if !strings.Contains(s.Label, "uv") || !strings.Contains(s.Label, "FFmpeg") {
		t.Fatalf("label should name the running downloads: %q", s.Label)
	}
	b.reset() // a failed attempt doesn't count
	a.finish()
	if s = u.snap(); s.Done != 200 || strings.Contains(s.Label, "uv") {
		t.Fatalf("after reset/finish: %+v", s)
	}
	u.set("tools", "done")
	u.set("install", "active")
	if s = u.snap(); s.Overall < 0.5 || s.Overall >= 0.51 || s.Bytes {
		t.Fatalf("time step just started: %v", s.Overall)
	}
	u.finish()
	if s = u.snap(); s.Overall != 1 || !s.Finished {
		t.Fatalf("finished: %+v", s)
	}
}

func TestSpeedAndETA(t *testing.T) {
	u := newSetupUI(false, "")
	u.begin("x", "", []stepSpec{{Key: "d", Bytes: true, Weight: 1}})
	u.set("d", "active")
	tr := u.track("aiklips", 10<<20)
	t0 := u.lastT
	tr.Write(make([]byte, 1<<20))
	u.sample(t0.Add(time.Second))
	s := u.snap()
	if s.Speed < 1<<20-1 || s.ETA < 8 || s.ETA > 10 {
		t.Fatalf("speed %v eta %v", s.Speed, s.ETA)
	}
}

func TestEventsNeedTokenAndStreamSnapshots(t *testing.T) {
	u := newSetupUI(false, "")
	u.begin("Updating to 1.2.3", "", []stepSpec{{Key: "install", Label: "Install aiklips 1.2.3", Expect: time.Second, Weight: 1}})
	srv := httptest.NewServer(u.handler())
	defer srv.Close()
	if r, _ := http.Get(srv.URL + "/events"); r.StatusCode != 404 {
		t.Fatal("events served without the token")
	}
	if r, _ := http.Get(srv.URL + "/?t=" + u.token); r.StatusCode != 200 {
		t.Fatal("page not served")
	}
	resp, err := http.Get(srv.URL + "/events?t=" + u.token)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content type %q", ct)
	}
	rd := bufio.NewReader(resp.Body)
	next := func() snapshot {
		for {
			line, err := rd.ReadString('\n')
			if err != nil {
				t.Fatal(err)
			}
			if strings.HasPrefix(line, "data: ") {
				var s snapshot
				if err := json.Unmarshal([]byte(line[6:]), &s); err != nil {
					t.Fatal(err)
				}
				return s
			}
		}
	}
	if s := next(); s.Title != "Updating to 1.2.3" || len(s.Steps) != 1 || s.Steps[0].State != "pending" {
		t.Fatalf("first snapshot: %+v", s)
	}
	for i := 0; i < 50; i++ { // wait for the handler to subscribe
		u.mu.Lock()
		n := len(u.subs)
		u.mu.Unlock()
		if n > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	u.set("install", "active")
	u.broadcast()
	if s := next(); s.Steps[0].State != "active" {
		t.Fatalf("update not streamed: %+v", s)
	}
	r, _ := http.Post(srv.URL+"/retry?t="+u.token, "", nil)
	if r.StatusCode != 204 || len(u.retry) != 1 {
		t.Fatal("retry not delivered")
	}
}

func TestFailWithoutWindowFallsBackToDialog(t *testing.T) {
	u := newSetupUI(false, "/tmp/x.log") // quiet: no window
	retry, shown := u.fail(errors.New("download x: dial tcp: no such host (are you online?)"))
	if retry || shown {
		t.Fatal("a quiet launcher can't show or retry")
	}
	if s := u.snap(); !strings.Contains(s.Error, "internet") || !strings.Contains(s.Details, "no such host") {
		t.Fatalf("%+v", s)
	}
}

func TestPlanShowsNothingForAnOrdinaryStart(t *testing.T) {
	if _, _, steps := plan(false, false, true, true, ""); steps != nil {
		t.Fatal("an ordinary start must not open the setup window")
	}
	if title, _, steps := plan(true, false, false, false, ""); len(steps) != 4 || title != "Setting up aiklips" {
		t.Fatalf("first install: %s %v", title, steps)
	}
	if title, _, steps := plan(false, true, true, true, "0.3.0"); len(steps) != 3 || !strings.Contains(title, "0.3.0") {
		t.Fatalf("update: %s %v", title, steps)
	}
}

func TestUVLabels(t *testing.T) {
	if uvLabel("Downloading numpy (15.6MiB)") != "Downloading numpy (15.6MiB)" || uvLabel(" + numpy==2.0") != "" {
		t.Fatal("uv label mapping")
	}
}
