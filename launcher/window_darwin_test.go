package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"
)

// Builds the real macOS window off screen (AIKLIPS_WINDOW_TEST=1 keeps it hidden and quits after 1.5 s) and checks
// the WKWebView loaded the page. Opt-in: it starts a GUI process.
func TestMacWindowLoadsPage(t *testing.T) {
	if os.Getenv("AIKLIPS_WINDOW_TEST") != "1" {
		t.Skip("set AIKLIPS_WINDOW_TEST=1")
	}
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Write([]byte("<html><body>ok</body></html>"))
	}))
	defer srv.Close()
	icon, _ := uiFiles.ReadFile("ui/icon-256.png")
	cmd, err := openWindow(srv.URL, icon)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("window script failed: %v", err)
		}
	case <-time.After(15 * time.Second):
		cmd.Process.Kill()
		t.Fatal("window script didn't quit")
	}
	if hits.Load() == 0 {
		t.Fatal("the web view never requested the page")
	}
}
