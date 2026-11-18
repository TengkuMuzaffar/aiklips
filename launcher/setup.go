package main

// The setup window: a small branded page (ui/setup.html) served on 127.0.0.1 and shown in a chromeless window
// (Edge --app on Windows, a WKWebView via osascript on macOS, Chromium --app on Linux) while the launcher installs or
// updates. It only opens when there is work that takes longer than showDelay, so ordinary starts show nothing.
//
// Progress: every download is a task with a byte counter; the bar is the weighted sum of the steps, byte steps by
// bytes, install steps by elapsed time against an expected duration. The page gets snapshots over SSE (/events).

import (
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

//go:embed ui
var uiFiles embed.FS

const showDelay = 900 * time.Millisecond

type stepSpec struct {
	Key, Label string
	Bytes      bool          // progress = bytes of all download tasks
	Expect     time.Duration // otherwise: progress eases towards 1 over about this long
	Weight     float64
}

type step struct {
	stepSpec
	state   string // pending | active | done | error
	started time.Time
	frac    float64 // set explicitly (0 = use time)
}

type task struct {
	name        string
	done, total int64
	finished    bool
}

// tracker counts bytes of one download (an io.Writer for io.MultiWriter).
type tracker struct {
	ui *setupUI
	t  *task
}

func (tr *tracker) Write(b []byte) (int, error) {
	if tr != nil {
		tr.ui.mu.Lock()
		tr.t.done += int64(len(b))
		tr.ui.dirty = true
		tr.ui.mu.Unlock()
	}
	return len(b), nil
}

func (tr *tracker) setTotal(n int64) {
	if tr != nil && n > 0 {
		tr.ui.mu.Lock()
		tr.t.total = n
		tr.ui.mu.Unlock()
	}
}

// reset forgets the bytes of a failed attempt (a retry counts from zero again)
func (tr *tracker) reset() {
	if tr != nil {
		tr.ui.mu.Lock()
		tr.t.done = 0
		tr.ui.mu.Unlock()
	}
}

func (tr *tracker) finish() {
	if tr != nil {
		tr.ui.mu.Lock()
		if tr.t.total < tr.t.done {
			tr.t.total = tr.t.done
		}
		tr.t.done, tr.t.finished = tr.t.total, true
		tr.ui.dirty = true
		tr.ui.mu.Unlock()
	}
}

type stepView struct {
	Key   string `json:"key"`
	Label string `json:"label"`
	State string `json:"state"`
}

type snapshot struct {
	Title    string     `json:"title"`
	Sub      string     `json:"sub"`
	Steps    []stepView `json:"steps"`
	Label    string     `json:"label"`
	Done     int64      `json:"done"`
	Total    int64      `json:"total"`
	Speed    float64    `json:"speed"` // bytes/s
	ETA      float64    `json:"eta"`   // seconds; -1 unknown
	Overall  float64    `json:"overall"`
	Bytes    bool       `json:"bytes"` // a download step is running
	Error    string     `json:"error,omitempty"`
	Details  string     `json:"details,omitempty"`
	Finished bool       `json:"finished"`
	Close    bool       `json:"close"`
	Version  string     `json:"version"`
}

type setupUI struct {
	mu       sync.Mutex
	title    string
	sub      string
	steps    []*step
	label    string
	tasks    []*task
	speed    float64
	lastDone int64
	lastT    time.Time
	errMsg   string
	details  string
	finished bool
	closing  bool
	dirty    bool
	subs     map[chan []byte]bool
	lastSub  time.Time // when the page was last connected (browser-tab fallback: tells if it was closed)

	enabled  bool
	logPath  string
	token    string
	url      string
	ln       net.Listener
	timer    *time.Timer
	shown    bool
	win      *exec.Cmd
	winGone  chan struct{}
	retry    chan struct{}
	stopTick chan struct{}
}

func newSetupUI(enabled bool, logPath string) *setupUI {
	b := make([]byte, 16)
	rand.Read(b)
	return &setupUI{enabled: enabled, logPath: logPath, token: hex.EncodeToString(b), subs: map[chan []byte]bool{},
		retry: make(chan struct{}, 1)}
}

// ui is the launcher's one setup window (replaced in main once the log path is known)
var ui = newSetupUI(false, "")

// begin starts a new run of work with these steps; the window appears if it isn't done within showDelay.
func (u *setupUI) begin(title, sub string, specs []stepSpec) {
	u.mu.Lock()
	u.title, u.sub, u.label, u.errMsg, u.details = title, sub, "", "", ""
	u.finished, u.closing, u.tasks, u.speed, u.lastDone, u.lastT = false, false, nil, 0, 0, time.Now()
	u.steps = nil
	for _, s := range specs {
		u.steps = append(u.steps, &step{stepSpec: s, state: "pending"})
	}
	u.dirty = true
	enabled, shown := u.enabled, u.shown
	if enabled && !shown && u.timer == nil {
		u.timer = time.AfterFunc(showDelay, u.show)
	}
	u.mu.Unlock()
	logf("%s", title)
}

func (u *setupUI) find(key string) *step {
	for _, s := range u.steps {
		if s.Key == key {
			return s
		}
	}
	return nil
}

// set changes a step's state (unknown keys are ignored: not every run has every step)
func (u *setupUI) set(key, state string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if s := u.find(key); s != nil && s.state != state {
		s.state = state
		if state == "active" {
			s.started = time.Now()
		}
		u.dirty = true
	}
}

func (u *setupUI) setFrac(key string, f float64) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if s := u.find(key); s != nil && f > s.frac {
		s.frac = math.Min(f, 1)
		u.dirty = true
	}
}

func (u *setupUI) setLabel(s string) {
	u.mu.Lock()
	if u.label != s {
		u.label, u.dirty = s, true
	}
	u.mu.Unlock()
}

// track registers a download; estimate keeps the bar from jumping back before the server sends the real size.
func (u *setupUI) track(name string, estimate int64) *tracker {
	u.mu.Lock()
	defer u.mu.Unlock()
	t := &task{name: name, total: estimate}
	u.tasks = append(u.tasks, t)
	u.dirty = true
	return &tracker{ui: u, t: t}
}

// sample updates the download speed (exponential moving average); called by the ticker.
func (u *setupUI) sample(now time.Time) {
	u.mu.Lock()
	defer u.mu.Unlock()
	var done int64
	for _, t := range u.tasks {
		done += t.done
	}
	dt := now.Sub(u.lastT).Seconds()
	if dt <= 0 {
		return
	}
	inst := float64(done-u.lastDone) / dt
	if inst < 0 {
		inst = 0
	}
	if u.speed == 0 {
		u.speed = inst
	} else {
		u.speed = 0.7*u.speed + 0.3*inst
	}
	u.lastDone, u.lastT = done, now
}

func easeTime(elapsed, expect time.Duration) float64 {
	if expect <= 0 {
		return 0
	}
	return 1 - math.Exp(-1.6*elapsed.Seconds()/expect.Seconds()) // ~80% at the expected time, never 100%
}

// snap builds what the page shows. Caller holds no lock.
func (u *setupUI) snap() snapshot {
	u.mu.Lock()
	defer u.mu.Unlock()
	s := snapshot{Title: u.title, Sub: u.sub, Label: u.label, Error: u.errMsg, Details: u.details, Finished: u.finished,
		Close: u.closing, ETA: -1, Version: LauncherVersion}
	var active []string
	for _, t := range u.tasks {
		s.Done += t.done
		s.Total += max(t.total, t.done)
		if !t.finished {
			active = append(active, t.name)
		}
	}
	byteFrac := 0.0
	if s.Total > 0 {
		byteFrac = math.Min(1, float64(s.Done)/float64(s.Total))
	}
	var sum, got float64
	for _, st := range u.steps {
		s.Steps = append(s.Steps, stepView{st.Key, st.Label, st.state})
		sum += st.Weight
		switch st.state {
		case "done":
			got += st.Weight
		case "active", "error":
			f := st.frac
			if st.Bytes {
				f = byteFrac
				s.Bytes = s.Bytes || st.state == "active"
			} else if f == 0 {
				f = easeTime(time.Since(st.started), st.Expect)
			}
			got += st.Weight * f
		}
	}
	if sum > 0 {
		s.Overall = got / sum
	}
	if u.finished {
		s.Overall = 1
	}
	if s.Bytes {
		s.Speed = u.speed
		if u.speed > 1024 && s.Total > s.Done {
			s.ETA = float64(s.Total-s.Done) / u.speed
		}
		if s.Label == "" && len(active) > 0 {
			s.Label = "Downloading " + strings.Join(active, " · ")
		}
	}
	return s
}

func (u *setupUI) broadcast() {
	b, _ := json.Marshal(u.snap())
	msg := []byte("data: " + string(b) + "\n\n")
	u.mu.Lock()
	for c := range u.subs {
		select {
		case c <- msg:
		default: // a slow page skips a frame; the next one is complete anyway
		}
	}
	u.dirty = false
	u.mu.Unlock()
}

// ---- HTTP ----

func (u *setupUI) handler() http.Handler {
	mux := http.NewServeMux()
	ok := func(r *http.Request) bool { return r.URL.Query().Get("t") == u.token }
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" || !ok(r) {
			http.NotFound(w, r)
			return
		}
		b, _ := uiFiles.ReadFile("ui/setup.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Write(b)
	})
	for _, f := range []string{"logo.svg", "icon-256.png"} {
		name := f
		mux.HandleFunc("/"+name, func(w http.ResponseWriter, r *http.Request) {
			b, _ := uiFiles.ReadFile("ui/" + name)
			if strings.HasSuffix(name, ".svg") {
				w.Header().Set("Content-Type", "image/svg+xml")
			} else {
				w.Header().Set("Content-Type", "image/png")
			}
			w.Write(b)
		})
	}
	mux.HandleFunc("/events", func(w http.ResponseWriter, r *http.Request) {
		if !ok(r) {
			http.NotFound(w, r)
			return
		}
		fl, can := w.(http.Flusher)
		if !can {
			http.Error(w, "no streaming", 500)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-store")
		c := make(chan []byte, 8)
		u.mu.Lock()
		u.subs[c] = true
		u.lastSub = time.Now()
		u.mu.Unlock()
		defer func() {
			u.mu.Lock()
			delete(u.subs, c)
			u.lastSub = time.Now()
			u.mu.Unlock()
		}()
		b, _ := json.Marshal(u.snap())
		fmt.Fprintf(w, "data: %s\n\n", b)
		fl.Flush()
		ping := time.NewTicker(15 * time.Second)
		defer ping.Stop()
		for {
			select {
			case m := <-c:
				if _, err := w.Write(m); err != nil {
					return
				}
				fl.Flush()
			case <-ping.C:
				fmt.Fprint(w, ": ping\n\n")
				fl.Flush()
			case <-r.Context().Done():
				return
			}
		}
	})
	mux.HandleFunc("/retry", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || !ok(r) {
			http.NotFound(w, r)
			return
		}
		select {
		case u.retry <- struct{}{}:
		default:
		}
		w.WriteHeader(204)
	})
	mux.HandleFunc("/open-log", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || !ok(r) {
			http.NotFound(w, r)
			return
		}
		openPath(u.logPath)
		w.WriteHeader(204)
	})
	return mux
}

// serve starts the local server once (idempotent).
func (u *setupUI) serve() error {
	if u.ln != nil {
		return nil
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	u.ln = ln
	u.url = fmt.Sprintf("http://%s/?t=%s", ln.Addr(), u.token)
	go http.Serve(ln, u.handler())
	u.stopTick = make(chan struct{})
	go func() {
		t := time.NewTicker(150 * time.Millisecond)
		defer t.Stop()
		n := 0
		for {
			select {
			case now := <-t.C:
				n++
				if n%3 == 0 {
					u.sample(now)
				}
				u.mu.Lock()
				live := u.dirty || len(u.tasks) > 0 || u.anyActive()
				u.mu.Unlock()
				if live {
					u.broadcast() // time-based steps move without events, so active steps refresh every tick
				}
			case <-u.stopTick:
				return
			}
		}
	}()
	return nil
}

func (u *setupUI) anyActive() bool {
	for _, s := range u.steps {
		if s.state == "active" {
			return true
		}
	}
	return false
}

// show opens the window (timer callback; also called directly to show an error).
func (u *setupUI) show() {
	u.mu.Lock()
	u.timer = nil
	if u.shown || !u.enabled || (u.finished && u.errMsg == "") {
		u.mu.Unlock()
		return
	}
	u.shown = true
	u.mu.Unlock()
	if err := u.serve(); err != nil {
		logf("setup window: %v", err)
		return
	}
	icon, _ := uiFiles.ReadFile("ui/icon-256.png")
	cmd, err := openWindow(u.url, icon)
	if err != nil {
		logf("setup window: %v", err)
		u.mu.Lock()
		u.shown = false
		u.mu.Unlock()
		return
	}
	gone := make(chan struct{})
	u.mu.Lock()
	u.win, u.winGone, u.lastSub = cmd, gone, time.Now()
	u.mu.Unlock()
	if cmd != nil {
		go func() { cmd.Wait(); close(gone) }()
	}
	logf("setup window open (%s)", map[bool]string{true: "own window", false: "browser tab"}[cmd != nil])
}

func (u *setupUI) isShown() bool {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.shown
}

// alive tells whether the person can still see the window.
func (u *setupUI) alive() bool {
	u.mu.Lock()
	defer u.mu.Unlock()
	if !u.shown {
		return false
	}
	if u.win != nil {
		select {
		case <-u.winGone:
			return false
		default:
			return true
		}
	}
	return len(u.subs) > 0 || time.Since(u.lastSub) < 20*time.Second // a browser tab: connected recently
}

// fail shows err in the window and waits. Returns retry=true when the person pressed Retry, and shown=true when the
// error was on screen (so no second dialog is needed).
func (u *setupUI) fail(err error) (retry, shown bool) {
	u.mu.Lock()
	if u.timer != nil {
		u.timer.Stop()
		u.timer = nil
	}
	u.errMsg = friendly(err)
	u.details = fmt.Sprintf("%v\n\naiklips launcher %s on %s\nLog: %s", err, LauncherVersion, platform(), u.logPath)
	for _, s := range u.steps {
		if s.state == "active" {
			s.state = "error"
		}
	}
	u.dirty = true
	enabled := u.enabled
	u.mu.Unlock()
	if !enabled {
		return false, false
	}
	u.show()
	u.broadcast()
	if !u.alive() {
		return false, false
	}
	select {
	case <-u.retry: // drop a stale press
	default:
	}
	for {
		select {
		case <-u.retry:
			u.mu.Lock()
			u.errMsg, u.details, u.dirty = "", "", true
			u.mu.Unlock()
			return true, true
		case <-time.After(time.Second):
			if !u.alive() {
				return false, true
			}
		}
	}
}

// friendly turns the common failures into one plain sentence (the details hold the full error).
func friendly(err error) string {
	m := err.Error()
	switch {
	case strings.Contains(m, "are you online"), strings.Contains(m, "no such host"), strings.Contains(m, "timeout"):
		return "aiklips couldn't reach the internet to download what it needs. Check your connection and try again."
	case strings.Contains(m, "checksum"), strings.Contains(m, "signature"):
		return "A download didn't match its fingerprint, so nothing was installed. Trying again usually fixes it."
	case strings.Contains(m, "no space"), strings.Contains(m, "not enough space"):
		return "Your disk is full. aiklips needs about 1 GB free to install."
	case strings.Contains(m, "already installing"):
		return "aiklips is already installing in another window. Wait for it to finish."
	}
	return "Something went wrong while setting up aiklips."
}

// finish marks every step done and closes the window shortly after (the app's own window takes over).
func (u *setupUI) finish() {
	u.mu.Lock()
	if u.timer != nil { // done before the window was ever needed
		u.timer.Stop()
		u.timer = nil
	}
	for _, s := range u.steps {
		s.state = "done"
	}
	u.finished, u.label, u.dirty = true, "", true
	shown := u.shown
	u.mu.Unlock()
	if !shown {
		return
	}
	u.broadcast()
	time.Sleep(900 * time.Millisecond) // let the last tick animate
	u.close()
}

// close removes the window (the page closes itself too) and resets so a later update can show it again.
func (u *setupUI) close() {
	u.mu.Lock()
	if !u.shown {
		u.mu.Unlock()
		return
	}
	u.closing, u.dirty = true, true
	u.mu.Unlock()
	u.broadcast()
	time.Sleep(300 * time.Millisecond)
	u.mu.Lock()
	if u.win != nil && u.win.Process != nil {
		u.win.Process.Kill()
	}
	u.win, u.shown = nil, false
	u.mu.Unlock()
	cleanupWindow()
}

// parallel runs fns at once and returns the first error (after all have stopped).
func parallel(fns ...func() error) error {
	errs := make([]error, len(fns))
	var wg sync.WaitGroup
	for i, f := range fns {
		wg.Add(1)
		go func() {
			defer wg.Done()
			errs[i] = f()
		}()
	}
	wg.Wait()
	for _, e := range errs {
		if e != nil {
			return e
		}
	}
	return nil
}

// windowProfile is a throwaway browser profile for the --app window (so it opens as its own process, which the
// launcher can close, and never touches the person's browser profile)
func windowProfile() string {
	d, err := os.MkdirTemp("", "aiklips-setup-")
	if err != nil {
		return ""
	}
	profileDir = d
	return d
}

var profileDir string

func cleanupWindow() {
	if profileDir != "" {
		go func(d string) { time.Sleep(2 * time.Second); os.RemoveAll(d) }(profileDir)
		profileDir = ""
	}
}

// chromiumApp starts a Chromium-family browser as a small app window. Returns nil when none is installed.
func chromiumApp(candidates []string, url string) *exec.Cmd {
	for _, c := range candidates {
		exe, err := exec.LookPath(c)
		if err != nil {
			continue
		}
		cmd := exec.Command(exe, "--app="+url, "--user-data-dir="+windowProfile(), "--window-size=520,640",
			"--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
			"--disable-features=Translate,msEdgeSidebarV2,msUndersideButton")
		if cmd.Start() == nil {
			return cmd
		}
	}
	return nil
}

// setStepLabel renames a step (e.g. once the version being downloaded is known).
func (u *setupUI) setStepLabel(key, label string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if s := u.find(key); s != nil {
		s.Label, u.dirty = label, true
	}
}
