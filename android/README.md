# hondalog for Android head units (developer notes)

The user guide, with how to put the app on the radio, is [docs/android.md](../docs/android.md).

The APK is the web app (`web/`) built into the APK and run in the app's own
WebView, with a small Java layer for what a WebView lacks. Nothing loads from
the internet, and Chrome doesn't have to be installed. It replaces the TWA in
`web/android-twa`, which needed Chrome, needed internet on first start, and
stopped polling the ECU whenever the radio showed another app.

```
web build (assets/www) ──► AssetServer ── https://appassets.androidplatform.net/
                                         (index.html gets assets/hondalog-android.js first)
page ── navigator.usb (shim) ─┐
page ── NativeFileStore ──────┼─► window.HondalogAndroid (Bridge.java)
page ── <a download> (shim) ──┘        ├─ UsbBridge: Android USB host API
                                       └─ FileBridge: app storage, Download/hondalog
```

| File | Role |
|---|---|
| `assets/hondalog-android.js` | `navigator.usb`: the WebUSB subset `web/src/platform/webusb` uses (open, claim, control in/out, bulk in/out, clearHalt, connect/disconnect events). It also saves downloads. Plain ES5. |
| `src/.../UsbBridge.java` | Does the WebUSB calls with `UsbManager`. All calls are async: the page passes a callback id and `Js.done` answers. Bulk IN polls `bulkTransfer` (100 ms) until data comes, like WebUSB's `transferIn`. The FTDI's 2-byte status-only packets are dropped there, not sent to the page. `releaseInterface`/`close` end a pending read. A known chip (FTDI 0403:6001, CH340 1a86:7523/5523) gets a permission request when plugged in. |
| `src/.../FileBridge.java` | The page's `FileStore` in `files/hondalog/`. Each write goes to a temp file, is synced, then renamed over the old one, so a power cut keeps the last good `hondalog.db`. Exports go to `Download/hondalog` (MediaStore on 10+). |
| `src/.../AssetServer.java` | Serves `assets/www` on a real https origin, so the page is a secure context and ES modules and wasm load the same way they do on Pages. Paths without an extension get the app shell. |
| `src/.../AppWebView.java` | Always reports the window as visible. Without this, Chromium marks the page hidden and throttles its timers behind other apps. |
| `src/.../DashService.java` | Foreground service with a partial wake lock. Its notification has App settings and Quit. |
| `src/.../MainActivity.java` | Sets up the WebView and file chooser. Opens on USB attach (`res/xml/device_filter.xml`). Back moves the app to the background without closing it. Persists the DB on `onStop` (the page's own pagehide/visibility flush never fires here). Warns if the WebView is older than 100 or if the page stays blank. |
| `src/.../BootReceiver.java` | Opens the app at boot if App settings says to. On Android 10+ this also needs "Display over other apps". |
| `src/.../Compat.java` | APIs newer than 23 (notification channel, startForegroundService, renderer priority) called by reflection. That way it compiles against the distro's API 23 jar. |

Web side: `web/src/platform/native.ts` (`NativeFileStore`, `isNativeApp`).
`platform/index.ts` picks the native store and the USB bridge when
`window.HondalogAndroid` is there. `pwa.ts` skips the service worker there.
`ui/shell/Stage.tsx` picks full vs phone layout by device pixels in the app (a 1280×720 radio at any density gets the
full stage). `ui/shell/touch.ts` treats the app as touch-only (no key hints). The shim adds touch CSS (no tap flash,
no long-press selection, no overscroll). Setup > Downloads (`ui/setup/DownloadsTab.tsx`) links the latest release.
Links leave the app through `shouldOverrideUrlLoading` to the radio's browser.

## Build

Windows (the release PC), signed with the release key:

```powershell
android\build.ps1            # android\build\hondalog-<v>-android.apk
android\build.ps1 -NoSign    # unsigned only
scripts\release\build-all.ps1 -Only android   # into release\<v>\
```

Linux (Debian/Ubuntu packages, no SDK download):

```bash
sudo apt-get install aapt apksigner dalvik-exchange android-sdk-platform-23 zipalign default-jdk-headless
(cd web && npm ci)
android/build.sh             # unsigned + a test-signed copy
```

`versionName` is `web/package.json` "version". `versionCode` is
major·10000 + minor·100 + patch (0.2.0 → 200). Bump the web version for every
APK you hand out, or Android won't install it as an update.

## Tests

- `web/src/platform/androidshim.test.ts`: the shim with a fake `UsbBridge` in front of the P2P simulator. The app's
  own `Link` → `UsbSerialTransport` → FTDI driver → shim wakes the ECU at 10400 and reads RAM. Also checks the vendor
  requests (latency 1 ms, the 10400 divisor), close cancelling the pending read, events, errors and downloads.
- `web/src/platform/native.test.ts`: the file store's chunked writes, errors and platform choice.
- By hand: serve `android/build/assets` the way `AssetServer` does, with the bridge faked and the simulator behind
  it, in Chrome at 1024×600 with an Android user agent. The app connects by itself, polls about 13 Hz and records.

Not tested yet: a real radio (its WebView version, its USB host, how it handles power when the ignition goes off)
and the real cable on the car.

## Debugging on the radio

`chrome://inspect` on the PC, with the radio on adb (USB or Wi-Fi), shows the page's console and network. WebView
debugging is always on in this app. Java side: `adb logcat -s hondalog hondalog-usb`.
