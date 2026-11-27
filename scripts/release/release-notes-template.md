hondalog {{VERSION}} ({{DATE}})

Live data logger for pre-2002 Honda ECUs over a KKL K-line cable, with the CYD black box.

## Downloads

| File | What |
|---|---|
| `hondalog-{{VERSION}}-windows-x64.zip` | Windows desktop app: unzip, run `hondalog\hondalog.exe` |
| `hondalog-{{VERSION}}-linux-x64.tar.gz` | Linux desktop app (x86-64): untar, run `hondalog/hondalog` (see INSTALL.txt for serial port access) |
| `hondalog-{{VERSION}}-x86_64.AppImage` | Linux desktop app as one file, when present |
| `hondalog-{{VERSION}}-android.apk` | Android (the web app as an app; USB serial through Chrome) |
| `hondalog-{{VERSION}}-web.zip` | The web app to host yourself (any folder; Chrome or Edge on desktop for Web Serial) |
| `hondalog-handheld-{{VERSION}}.bin` | CYD black box firmware, one image written at 0x0 (see FLASH-handheld.txt) |
| `SHA256SUMS` | checksums: `sha256sum -c SHA256SUMS` |

The web app runs without installing anything at {{PAGES_URL}} (Chrome or Edge).

## Changes since {{PREVIOUS}}

{{CHANGES}}
