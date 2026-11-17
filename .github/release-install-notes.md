
## Install

| System | Download | First start |
|---|---|---|
| macOS 12+ (Apple silicon or Intel) | `aiklips-mac.zip` | Unzip, drag **aiklips.app** to Applications. The first time, **right-click → Open → Open** (the app isn't notarised yet). |
| Windows 10/11 | `aiklips-windows-x64.exe` | Run it. If SmartScreen appears: **More info → Run anyway**. It adds Start-menu and desktop shortcuts. |
| Linux (x86-64 / arm64) | `aiklips-linux-x86_64` / `-arm64` | `chmod +x` it and run it once; it adds itself to your app menu. For the native window: `sudo apt install gir1.2-webkit2-4.1` (otherwise it opens in your browser). |

The first start downloads Python, the app's libraries and FFmpeg (about 150 MB) and takes a few minutes. After that,
updates are small: the app tells you when one is ready (**Settings → Updates**) and installs it on restart. Every
update is signed and checked before it's installed, and if a new version fails to start, aiklips goes back to
the previous one by itself. Your projects (in `~/aiklips-projects`) are never touched by updates.
