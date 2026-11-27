# Release and deploy (local scripts, no GitHub Actions)

Everything here runs on the owner's Windows PC (PowerShell 5.1 or 7) plus WSL
Ubuntu for the Linux app and the CYD firmware. Nothing runs in the cloud: Pages
serves the `gh-pages` branch and releases are made with the `gh` CLI.

| Script | What it does |
|---|---|
| `deploy-pages.ps1` | builds `web/` with `HONDALOG_BASE=/hon-diy-data/`, puts it on the `gh-pages` branch (temporary git worktree, `.nojekyll`, `404.html` SPA fallback), commits and pushes. `-DryRun` commits locally only. `-ConfigurePages` is the one-time Pages setup. |
| `build-all.ps1` | builds every artifact into `release/<version>/` with `SHA256SUMS` (see the table below). `-Only`, `-Skip`, `-KeepGoing`, `-CheckOnly`. |
| `publish-release.ps1` | tags `v<version>`, pushes the tag, `gh release create` with the notes and every file in `release/<version>/`. `-Draft`, `-Prerelease`, `-WhatIf`. Refuses on a dirty tree or an existing tag/release. |
| `make-release-notes.ps1` | `release-notes-template.md` + `git log` since the last tag (publish-release calls it; run it alone to preview). |
| `create-keystore.ps1` | one time: the Android signing key in `%USERPROFILE%\.hondalog-keys\`. |
| `sign-apk.ps1` | zipalign + apksigner with that key (`-VerifyOnly` to check an APK). |
| `build-firmware-wsl.sh` | the CYD firmware in WSL: `scripts/build-firmware.sh handheld`, then the merged image. |
| `../../packaging/linux/build-linux.sh` | the PySide6 desktop app for Linux with PyInstaller (tar.gz, AppImage when appimagetool is there). |

Run the PowerShell scripts from the repo root, e.g.
`powershell -ExecutionPolicy Bypass -File scripts\release\build-all.ps1`.

## What gets built

The version is `web/package.json` "version" unless you pass `-Version x.y.z`.

| Target | How | File in `release/<version>/` |
|---|---|---|
| web | `vite build` with base `./` (runs from any folder) | `hondalog-<v>-web.zip` (no source maps) |
| windows | `packaging\windows\build-windows.ps1` (PySide6 app, PyInstaller) | `hondalog-<v>-windows-x64.zip` |
| linux | `packaging/linux/build-linux.sh` in WSL (same app, PyInstaller) | `hondalog-<v>-linux-x64.tar.gz`, `hondalog-<v>-x86_64.AppImage` if appimagetool is installed |
| android | `web\android-twa\build-apk.ps1` (Bubblewrap TWA of the Pages site), or `npx tauri android build --apk` + `sign-apk.ps1` if a Tauri Android project exists | `hondalog-<v>-android.apk` |
| firmware | `build-firmware-wsl.sh`: PlatformIO build of `firmware/handheld`, then `esptool --chip esp32s3 merge-bin --flash-mode keep --flash-freq keep --flash-size keep 0x0 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin` (esptool 5) | `hondalog-handheld-<v>.bin`, `FLASH-handheld.txt` |

Plus `SHA256SUMS` (LF, `sha256sum -c SHA256SUMS` works on Linux) and
`BUILD-INFO.txt` (revision and per-target result of the last run; not uploaded).

The WSL builds copy the checkout from `/mnt/c/...` into `~/hondalog-release/`
(rsync, CRLF text files turned back to LF) and build there; later runs are
incremental. The firmware's PC simulator checks are skipped automatically when
`libpng-dev` is missing; a failing host test is reported but does not stop the
image unless you pass `-StrictTests`.

## One-time setup (owner)

1. **Node modules for the web app**: `cd web; npm ci` (the scripts never install packages).
2. **GitHub CLI**: `winget install GitHub.cli`, then `gh auth login`.
3. **GitHub Pages source** (after the first real `deploy-pages.ps1` has pushed `gh-pages`):

   ```powershell
   scripts\release\deploy-pages.ps1 -ConfigurePages
   ```

   It runs (POST the first time, PUT if Pages is already on):

   ```powershell
   gh api -X POST repos/olosnoks/hon-diy-data/pages -f build_type=legacy -f "source[branch]=gh-pages" -f "source[path]=/"
   ```

   The equivalent by hand: repo Settings > Pages > Source "Deploy from a branch", branch `gh-pages`, folder `/ (root)`.
   Site: https://olosnoks.github.io/hon-diy-data/
4. **Android signing key** (kept outside the repo, password never stored):

   ```powershell
   scripts\release\create-keystore.ps1        # asks for the password, or reads $env:HONDALOG_KS_PASS
   ```

   Back up `%USERPROFILE%\.hondalog-keys\hondalog-release.jks` and its password: an APK signed with a lost key can't be updated in place.
   The script prints the SHA-256 fingerprint for the TWA's `assetlinks.json` (see `web/android-twa/build-apk.ps1`).
5. **Android SDK** (for the APK): a JDK 17+ (`JAVA_HOME`), then

   ```powershell
   & "$env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" --licenses
   & "$env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
   ```

6. **WSL Ubuntu packages** (the scripts never call sudo; they list what is missing):

   ```bash
   sudo apt-get update
   # build tools for the firmware and the Linux app
   sudo apt-get install -y build-essential git rsync python3-venv python3-dev binutils
   # the firmware's PC simulator checks (optional; skipped without it)
   sudo apt-get install -y libpng-dev
   # Qt runtime, to run or smoke-test the Linux app in WSL (the build itself goes on without them)
   sudo apt-get install -y libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
     libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
     libfontconfig1 libdbus-1-3
   # AppImage (optional): FUSE 2 for appimagetool; without it the script uses APPIMAGE_EXTRACT_AND_RUN=1
   sudo apt-get install -y libfuse2t64 file
   ```

   On this PC (Ubuntu 24.04) only `libpng-dev`, `libxcb-cursor0` and `libfuse2t64` are missing today.
   For the AppImage put `appimagetool` (https://github.com/AppImage/appimagetool/releases) on the WSL `PATH`
   or point `APPIMAGETOOL` at it; without it only the tar.gz is made.
   The first firmware build installs PlatformIO into `~/.hondalog-pio` (~1.5 GB download);
   the merge installs esptool 5 into `~/.hondalog-esptool` (pip, no sudo).

7. **`.gitignore`**: `release/` must be ignored (the integrator adds it); `publish-release.ps1` ignores it when checking for a dirty tree either way.

## Release checklist

1. Bump the version: `web/package.json` "version", `pyproject.toml` `version` (the desktop app),
   and `web/android-twa/twa-manifest.json` `appVersionName` + `appVersionCode` (+1 every release).
   Update `CHANGELOG.md`. Commit.
2. Tests: `cd web; npx vitest run` and `npx tsc -b`; `pytest` for the desktop app;
   firmware host tests run inside `build-all.ps1`.
3. Check the tools: `scripts\release\build-all.ps1 -CheckOnly`.
4. Build: `scripts\release\build-all.ps1` (asks for the keystore password once, or set
   `$env:HONDALOG_KS_PASS` for the session). Check the summary says `OK` for every target.
5. Smoke test: unzip the Windows build and start `hondalog.exe`; flash `hondalog-handheld-<v>.bin`
   (`FLASH-handheld.txt`); install the APK on the phone.
6. Deploy the web app: `scripts\release\deploy-pages.ps1 -DryRun`, look at `git log gh-pages --stat -1`,
   then `scripts\release\deploy-pages.ps1`. Open https://olosnoks.github.io/hon-diy-data/.
   (The TWA APK loads this site, so deploy before handing out a new APK.)
7. Preview the release: `scripts\release\publish-release.ps1 -WhatIf` (checks, notes, plan; changes nothing).
8. Publish: `scripts\release\publish-release.ps1 -Draft`, read the draft on GitHub, publish it there.
   (Without `-Draft` it goes live at once.)
9. Push the branch if it isn't yet: `git push`.

Undo a bad tag before anything was downloaded: `gh release delete v<v> --yes; git push origin :refs/tags/v<v>; git tag -d v<v>`.
