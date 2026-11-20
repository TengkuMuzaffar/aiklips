package main

import (
	"errors"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"
)

// Serves the setup page in fixed states for screenshots (opt-in: AIKLIPS_PREVIEW=1).
func TestPreviewStates(t *testing.T) {
	if os.Getenv("AIKLIPS_PREVIEW") != "1" {
		t.Skip()
	}
	_, _, steps := plan(true, false, false, false, "")
	mk := func(port int) *setupUI {
		u := newSetupUI(false, "/Users/me/.aiklips/launcher.log")
		u.token = "x"
		u.begin("Setting up aiklips", "A one-time download of about 150 MB. Later starts take seconds.", steps)
		go http.ListenAndServe(fmt.Sprintf("127.0.0.1:%d", port), u.handler())
		return u
	}
	a := mk(47811)
	a.set("tools", "done")
	a.set("download", "active")
	tr := a.track("aiklips", 9<<20)
	tr.Write(make([]byte, 6<<20))
	a.track("FFmpeg", 30<<20).finish()
	a.speed = 7.4e6
	b := mk(47812)
	b.set("tools", "done")
	b.set("download", "active")
	b.fail(errors.New("download https://x: dial tcp: lookup github.com: no such host (are you online?)"))
	c := mk(47813)
	c.finish()
	go func() {
		for {
			a.broadcast()
			b.broadcast()
			time.Sleep(200 * time.Millisecond)
		}
	}()
	time.Sleep(300 * time.Second)
}
