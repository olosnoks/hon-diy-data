# Build the hondalog Android APK: a Trusted Web Activity around the deployed
# web app (https://olosnoks.github.io/hon-diy-data/). Serial runs in Chrome via
# WebUSB (src/platform/webusb: FTDI FT232R + CH340 drivers), offline via sw.js.
#
# One-time owner steps (the porting agent could not do them for you):
#   1. Accept the Android SDK licences and install the packages:
#        & "$env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" --licenses
#        & "$env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
#   2. Make the signing key (kept OUT of the repo):
#        mkdir $env:USERPROFILE\.hondalog-keys
#        keytool -genkeypair -v -keystore $env:USERPROFILE\.hondalog-keys\hondalog-release.jks -alias hondalog -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=hondalog"
#   3. Publish assetlinks.json at https://olosnoks.github.io/.well-known/assetlinks.json
#      (domain root = the olosnoks.github.io repo, not hon-diy-data) with the
#      fingerprint from:  keytool -list -v -keystore $env:USERPROFILE\.hondalog-keys\hondalog-release.jks -alias hondalog
#      Without it the app still runs, but shows a Chrome URL bar (Custom Tab fallback).
#   4. Deploy the web app (scripts/release) so the manifest URL above is live.
# Then run this script from web\android-twa.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk  = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { "$env:LOCALAPPDATA\Android\Sdk" }
$jdk  = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { (Get-Item (Get-Command java).Source).Directory.Parent.FullName }
$out  = Join-Path $env:USERPROFILE '.hondalog-build\twa'   # generated Gradle project, outside the repo

New-Item -ItemType Directory -Force (Join-Path $env:USERPROFILE '.bubblewrap') | Out-Null
@{ jdkPath = $jdk; androidSdkPath = $sdk } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $env:USERPROFILE '.bubblewrap\config.json')

New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item (Join-Path $here 'twa-manifest.json') (Join-Path $out 'twa-manifest.json') -Force
# Bubblewrap runs a bare `gradlew.bat` through cmd: that needs cmd to look in the
# current folder, which this setting (inherited from some shells) turns off.
Remove-Item Env:NoDefaultCurrentDirectoryInExePath -ErrorAction SilentlyContinue
Push-Location $out
try {
  npx -y @bubblewrap/cli@1 update --skipVersionUpgrade
  if ($LASTEXITCODE) { throw "bubblewrap update failed ($LASTEXITCODE)" }
  # Bubblewrap signs by putting the password on apksigner's command line (and
  # echoes it in its error text), so build unsigned here and sign with
  # scripts\release\sign-apk.ps1, which passes it as env:HONDALOG_KS_PASS.
  $unsigned = Join-Path $out 'app-release-unsigned-aligned.apk'
  Remove-Item $unsigned -ErrorAction SilentlyContinue
  npx -y @bubblewrap/cli@1 build --skipPwaValidation --skipSigning
  if ($LASTEXITCODE -or -not (Test-Path $unsigned)) { throw "bubblewrap build failed: no $unsigned" }
  $apk = Join-Path $out 'app-release-signed.apk'
  & (Join-Path $here '..\..\scripts\release\sign-apk.ps1') -Apk $unsigned -Out $apk
  Write-Host "APK: $apk"
} finally { Pop-Location }
