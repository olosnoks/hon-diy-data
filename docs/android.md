# hondalog on the car's Android radio

The radio runs the same app as the browser version: Live, Drive, Events,
Replay, GPS, Bytes and Setup, all on the radio. The KKL cable plugs into one
of the radio's USB ports.

- **No Chrome and no internet needed.** The whole app is inside the APK.
- **It keeps logging behind other apps.** Switch to maps or music and the ECU
  polling, the recording and the stall catcher keep running. A notification
  shows while it runs.
- **It opens by itself when you plug in the cable**, and connects with no taps.
- **Drives stay on the radio** until you export them for the laptop.

Made for a 1280×720 touch head unit: the app always uses the full layout on a
1280×720 panel, whatever density the radio is set to, and shows no keyboard
hints. Needs Android 6 or newer (made for Android 10+ head units), with
"Android System WebView" version 100 or newer. The app checks this and tells you
if it's too old.

## Putting it on the radio (free)

You need the signed APK, `hondalog-<version>-android.apk`. `android\build.ps1`
on the PC makes it and asks for the signing-key password, or take it from a
GitHub release. Use the signed one. A test-signed APK can't be updated by a
signed one later without uninstalling it first, and uninstalling deletes the
drives on the radio.

### Way 1: USB stick (simplest, no tools)

1. Copy the APK onto a USB stick (FAT32 or exFAT) on the PC.
2. Plug the stick into the radio. Open the radio's **File Manager** (or its
   "USB" app) and tap the APK.
3. Android asks to allow **Install unknown apps** for the File Manager. Allow
   it, go back, and tap **Install**.
4. Take the stick out. You can turn "Install unknown apps" off again.

If the File Manager can't open APKs, install a free file manager from the Play
Store (Files by Google, or Material Files if there's no Play Store, from
F-Droid as an APK on the same stick) and open the APK from it.

### Way 2: adb over Wi-Fi from the PC (best for updates and debugging)

adb is already on the PC (scoop). Do this once:

1. On the radio: **Settings → About → tap "Build number" 7 times.** Some head
   units hide it, or ask for a factory code to show it. Common codes are
   `8888`, `1234`, `0000`, `3368`, `123456`; check yours with the seller.
2. **Developer options → USB debugging on.** If the radio has **Wireless
   debugging** (Android 11+) or **ADB over network**, turn that on too.
3. Put the PC and the radio on the same Wi-Fi (a phone hotspot works).

Then, every time:

```powershell
# Android 11+ "Wireless debugging": pair once with the code it shows, then connect
adb pair 192.168.x.y:PAIRPORT
adb connect 192.168.x.y:PORT
# older radios with "ADB over network": port 5555
adb connect 192.168.x.y:5555

adb install -r android\build\hondalog-0.2.0-android.apk   # -r keeps the drives
```

An update installs over the old version and keeps the drives, as long as it's
signed with the same key and has a higher version.

### Way 3: download on the radio

Every GitHub release has the APK (`scripts\release\build-all.ps1` then
`publish-release.ps1`). On the radio, over a hotspot, open
https://github.com/olosnoks/hon-diy-data/releases/latest in the browser, download
`hondalog-<version>-android.apk` and tap it. Steps 3–4 of Way 1 apply.

Once hondalog is on the radio, updates are easier: **F8 Setup → Downloads**
shows the newest release, marks the radio's file and says **UPDATE AVAILABLE**.
Tap Download, then open the APK. It installs over the running version and
keeps the drives.

## First start (2 minutes, once)

1. **Open hondalog** from the app list, before plugging in the cable.
2. **Plug in the KKL cable.** Android asks *"Open hondalog when FT232R USB UART
   is connected?"*. Tick **Use by default** and tap **OK**. From now on,
   plugging in the cable opens the app and connects with no questions.
3. **Pull down the notification → App settings:**
   - **Battery: allow background.** Android then won't stop logging behind
     other apps.
   - **Start hondalog with the radio** (optional). On Android 10+ it then asks
     for **Display over other apps**. Allow it, or the radio won't let the app
     open at boot.
   - Check the line **WebView NNN**: it should be 100 or more.
4. **The radio's own app killer.** Most Chinese head units (FYT/UIS, TopWay,
   PX5/PX6 and others) close apps when the car sleeps. Look in Settings (often
   under *Factory*, *Extra* or *Car settings*) for *Background apps*, *Keep
   alive*, *Whitelist* or *Sleep* and add hondalog. Without this, recording
   stops when the radio sleeps. When it wakes, the app reopens on the cable
   plug-in event.

## Using it

- Top bar: **ECU** shows the poll rate once the cable answers, **REC** the
  recording time, **Stall armed** the stall catcher.
- **Back** sends the app to the background and it keeps running. To stop it,
  use **Quit** in the notification.
- Plugging in the CYD (CH340) works the same way as the cable.
- Exporting for the laptop: **F8 Setup → Files → Export hondalog.db** saves to
  **Download/hondalog/** on the radio. Copy it onto a USB stick with the File
  Manager, or pull it with adb:

  ```powershell
  adb pull /sdcard/Download/hondalog/hondalog.db
  ```

  On the laptop it is the same file format as `~/hondalog/hondalog.db`. Open it
  with `hondalog-log --db hondalog.db list` (or `report`, `stalls`, `export`),
  or import it in the web app (Replay → Import file). GPS, CSV and other exports
  from the app's screens land in the same folder.

## When something's wrong

| What you see | What to do |
|---|---|
| "App not installed" | Another hondalog signed with a different key is on the radio. Export the drives, uninstall it, install again. |
| "Android System WebView is too old" | Update *Android System WebView* in the Play Store. With no Play Store, sideload a newer WebView APK for the radio's CPU (usually arm64-v8a) the same way as hondalog. |
| Blank screen and "hondalog did not start" | Same as above. If the WebView is new enough, connect adb and open `chrome://inspect` on the PC to see the error. |
| ECU stays off with the cable in | Unplug and replug the cable, and tap OK on the prompt. Setup → Cable shows the port. The app has drivers for FTDI FT232R and CH340 cables only. |
| Logging stops when the screen shows maps | App settings → Battery: allow background, and add hondalog to the radio's keep-alive list (step 4 above). |
| Only one USB port and it's taken | Use a powered USB hub. Radios supply little current. |

Developers: [android/README.md](../android/README.md).
