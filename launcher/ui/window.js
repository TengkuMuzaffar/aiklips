// The macOS setup window: an NSWindow holding a WKWebView, run by `osascript -l JavaScript` (no cgo needed in the
// launcher). __URL__, __ICON__ and __HIDDEN__ are filled in by platform_darwin.go.
ObjC.import('Cocoa');
ObjC.import('WebKit');
var URL = __URL__, ICON = __ICON__, HIDDEN = __HIDDEN__;
var app = $.NSApplication.sharedApplication;
app.setActivationPolicy(HIDDEN ? $.NSApplicationActivationPolicyProhibited : $.NSApplicationActivationPolicyRegular);
if (ICON) {
  var img = $.NSImage.alloc.initWithContentsOfFile(ICON);
  if (img && !img.isNil()) app.setApplicationIconImage(img);
}
ObjC.registerSubclass({
  name: 'AiklipsSetupDelegate',
  protocols: ['NSWindowDelegate'],
  methods: {
    'windowWillClose:': { types: ['void', ['id']], implementation: function () { $.NSApp.terminate(null); } },
  },
});
var W = 520, H = 640;
var style = $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable | $.NSWindowStyleMaskMiniaturizable |
  $.NSWindowStyleMaskFullSizeContentView;
var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(0, 0, W, H), style,
  $.NSBackingStoreBuffered, false);
win.title = 'aiklips';
win.titlebarAppearsTransparent = true;
win.titleVisibility = $.NSWindowTitleHidden;
win.movableByWindowBackground = true;
win.backgroundColor = $.NSColor.colorWithSRGBRedGreenBlueAlpha(6 / 255, 38 / 255, 28 / 255, 1);
win.releasedWhenClosed = false;
var delegate = $.AiklipsSetupDelegate.alloc.init;
win.delegate = delegate;
var web = $.WKWebView.alloc.initWithFrameConfiguration($.NSMakeRect(0, 0, W, H), $.WKWebViewConfiguration.alloc.init);
web.setValueForKey($.NSNumber.numberWithBool(false), 'drawsBackground'); // no white flash before the page paints
web.autoresizingMask = $.NSViewWidthSizable | $.NSViewHeightSizable;
win.contentView = web;
web.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(URL)));
win.center;
if (HIDDEN) {
  $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(1.5, false, function () { $.NSApp.terminate(null); });
} else {
  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);
}
app.run;
