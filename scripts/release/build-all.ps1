<#
.SYNOPSIS
  Build every hondalog release artifact into release\<version>\ with SHA256SUMS.

.DESCRIPTION
  Targets (all by default; pick with -Only, drop with -Skip):

    web       the React app (web/), base "./" so it runs from any folder or host
              -> hondalog-<ver>-web.zip            (GitHub Pages: deploy-pages.ps1)
    windows   the PySide6 desktop app, packaging\windows\build-windows.ps1 (PyInstaller)
              -> hondalog-<ver>-windows-x64.zip
    linux     the same app in WSL, packaging/linux/build-linux.sh (PyInstaller)
              -> hondalog-<ver>-linux-x64.tar.gz (+ .AppImage when appimagetool is there)
    android   the standalone app: android\build.ps1 (web build inside the APK, native USB
              bridge); else web\android-twa\build-apk.ps1 (Bubblewrap TWA),
              or a Tauri Android project if web\src-tauri\gen\android exists; signed with
              %USERPROFILE%\.hondalog-keys\hondalog-release.jks
              -> hondalog-<ver>-android.apk
    firmware  the CYD black box in WSL: scripts/build-firmware.sh handheld (PlatformIO),
              merged with esptool 5 (bootloader 0x0, partitions 0x8000, app 0x10000)
              -> hondalog-handheld-<ver>.bin, FLASH-handheld.txt

  Version: -Version, else web\package.json. Every target's prerequisites are
  checked first; a missing tool stops the run before anything is built, with
  what to install. -KeepGoing builds what it can and reports the rest.

  Passwords: the keystore password comes from $env:HONDALOG_KS_PASS or is asked
  for once; it is never written anywhere.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\build-all.ps1
.EXAMPLE
  scripts\release\build-all.ps1 -Only web,firmware -Version 0.2.1
.EXAMPLE
  scripts\release\build-all.ps1 -Skip android -KeepGoing
#>
param(
    [string]$Version,
    [string[]]$Only,                # web, windows, linux, android, firmware (comma list)
    [string[]]$Skip,
    [string]$Distro = "Ubuntu",     # WSL distribution for linux + firmware
    [string]$Python = "python",     # Windows Python 3.11+ for the PyInstaller build
    [switch]$SkipTypecheck,         # web: vite build without tsc -b
    [switch]$StrictTests,           # firmware: a failing host test fails the build
    [switch]$Clean,                 # fresh venvs / build folders
    [switch]$KeepGoing,             # build the targets whose prerequisites are met
    [switch]$CheckOnly              # only check prerequisites
)

. "$PSScriptRoot\common.ps1"

$All = @("web", "windows", "linux", "android", "firmware")
# "-Only web,firmware" arrives as one string under powershell -File: split it
$Only = @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$Skip = @($Skip | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
foreach ($t in $Only + $Skip) { if ($All -notcontains $t) { Fail "unknown target '$t' (use: $($All -join ', '))" } }
$Targets = if ($Only.Count) { $All | Where-Object { $Only -contains $_ } } else { $All }
if ($Skip.Count) { $Targets = $Targets | Where-Object { $Skip -notcontains $_ } }
$Targets = @($Targets)
if ($Targets.Count -eq 0) { Fail "no targets left to build" }

$Version = Get-ReleaseVersion $Version
$Rev = Invoke-Git rev-parse --short HEAD
$Dirty = (@(Get-TreeChanges).Count -gt 0)
$Out = Join-Path $RepoRoot "release\$Version"
$AppScript = Join-Path $RepoRoot "android\build.ps1"
$TwaScript = Join-Path $WebDir "android-twa\build-apk.ps1"
$TwaApk = Join-Path $env:USERPROFILE ".hondalog-build\twa\app-release-signed.apk"
$TauriAndroid = Join-Path $WebDir "src-tauri\gen\android"
$Keystore = Join-Path $env:USERPROFILE ".hondalog-keys\hondalog-release.jks"

Write-Host "hondalog release $Version from $Rev$(if ($Dirty) { ' (uncommitted changes in the tree)' })" -ForegroundColor Cyan
Write-Host "targets: $($Targets -join ', ')   ->  $Out"
$py = Get-PythonAppVersion
if ($py -and $py -ne $Version -and ($Targets -contains "windows" -or $Targets -contains "linux")) {
    Write-Warn2 "the desktop app (pyproject.toml) says $py; its archives are named $Version. Bump pyproject.toml to match."
}

# ---------------------------------------------------------------- prerequisites
function Get-AndroidSdkDir {
    foreach ($d in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA "Android\Sdk"))) {
        if ($d -and (Test-Path $d)) { return $d }
    }
    return $null
}

function Test-Prereqs([string]$t) {
    $p = @()
    switch ($t) {
        "web" {
            if (-not (Test-Command "npx")) { $p += "Node.js 20+ (winget install OpenJS.NodeJS.LTS)" }
            if (-not (Test-Path (Join-Path $WebDir "node_modules"))) { $p += "web\node_modules: run 'npm ci' in web\" }
        }
        "windows" {
            $v = $null
            try { $v = (& $Python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null) } catch { }
            if (-not $v) { $p += "Python 3.11+ on PATH (or -Python C:\Python313\python.exe)" }
            elseif ([version]$v -lt [version]"3.11") { $p += "Python 3.11+ (found $v at $Python)" }
        }
        { $_ -in "linux", "firmware" } {
            if (-not (Test-WslDistro $Distro)) { $p += "WSL distribution '$Distro' (wsl --install -d Ubuntu, or -Distro <name>)" }
        }
        "android" {
            if (Test-Path $AppScript) {
                if (-not (Test-Command "npx")) { $p += "Node.js (the web build)" }
                if (-not (Test-Path (Join-Path $WebDir "node_modules"))) { $p += "web\node_modules (cd web; npm ci)" }
            } elseif (Test-Path $TwaScript) {
                if (-not (Test-Command "npx")) { $p += "Node.js (Bubblewrap runs through npx)" }
            } elseif (Test-Path $TauriAndroid) {
                if (-not (Test-Command "cargo")) { $p += "Rust (rustup) for the Tauri Android build" }
                if (-not $env:NDK_HOME) { $p += "NDK_HOME (Android NDK) for the Tauri Android build" }
            } else {
                $p += "an Android project: web\android-twa\build-apk.ps1 or web\src-tauri\gen\android"
            }
            if (-not ($env:JAVA_HOME -or (Test-Command "java"))) { $p += "a JDK 17+ (JAVA_HOME)" }
            $sdk = Get-AndroidSdkDir
            if (-not $sdk) { $p += "the Android SDK (ANDROID_HOME)" }
            elseif (-not (Get-ChildItem (Join-Path $sdk "build-tools") -Recurse -Filter apksigner.bat -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                $p += "Android build-tools (sdkmanager --licenses; sdkmanager `"platform-tools`" `"platforms;android-34`" `"build-tools;34.0.0`")"
            }
            if (-not (Test-Path $Keystore)) { $p += "the signing key $Keystore (scripts\release\create-keystore.ps1)" }
        }
    }
    return $p
}

Write-Step "checking prerequisites"
$ready = @(); $blocked = @{}
foreach ($t in $Targets) {
    $p = @(Test-Prereqs $t)
    if ($p.Count -eq 0) { Write-Ok $t; $ready += $t }
    else {
        $blocked[$t] = $p
        Write-Host "   MISSING $t" -ForegroundColor Red
        $p | ForEach-Object { Write-Host "           - $_" }
    }
}
if ($CheckOnly) { if ($blocked.Count) { exit 1 } else { exit 0 } }
if ($blocked.Count -and -not $KeepGoing) {
    Fail "prerequisites missing for: $($blocked.Keys -join ', ') (install them, drop them with -Skip, or pass -KeepGoing)"
}

New-Item -ItemType Directory -Force $Out | Out-Null
$OutWsl = ConvertTo-WslPath $Out

# ---------------------------------------------------------------- targets
function Build-Web {
    Write-Step "web: vite build (base ./)"
    $dist = Invoke-WebBuild -Base "./" -SkipTypecheck:$SkipTypecheck
    $zip = Join-Path $Out "hondalog-$Version-web.zip"
    # source maps stay out of the zip (they are on Pages for debugging)
    New-ZipFromDir -Dir $dist -Zip $zip -ExcludeExt @(".map")
    Write-Ok (Split-Path -Leaf $zip)
    Write-Host "   note: web\dist now holds the './' build; deploy-pages.ps1 rebuilds with the Pages base"
}

function Build-Windows {
    Write-Step "windows: packaging\windows\build-windows.ps1"
    $script = Join-Path $RepoRoot "packaging\windows\build-windows.ps1"
    $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Python", $Python)
    if ($Clean) { $a += "-Clean" }
    $shell = if (Test-Command "pwsh") { "pwsh" } else { "powershell" }
    Invoke-Native $shell $a "build-windows.ps1"
    $zip = Get-ChildItem (Join-Path $RepoRoot "dist") -Filter "hondalog-*-windows-x64.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $zip) { Fail "build-windows.ps1 made no dist\hondalog-*-windows-x64.zip" }
    Copy-Item $zip.FullName (Join-Path $Out "hondalog-$Version-windows-x64.zip") -Force
    Write-Ok "hondalog-$Version-windows-x64.zip (from $($zip.Name))"
}

function Build-Linux {
    Write-Step "linux: packaging/linux/build-linux.sh in WSL ($Distro)"
    $a = @("--out", $OutWsl, "--version", $Version, "--rev", $Rev)
    if ($Clean) { $a += "--clean" }
    $code = Invoke-WslScript $Distro (Join-Path $RepoRoot "packaging\linux\build-linux.sh") $a
    if ($code -ne 0) { Fail "build-linux.sh failed (exit $code)" }
}

function Read-KeystorePassword {
    if ($env:HONDALOG_KS_PASS) { return $false }
    $s = Read-Host -AsSecureString "Android keystore password ($Keystore)"
    $env:HONDALOG_KS_PASS = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
    return $true
}

function Build-Android {
    $apkOut = Join-Path $Out "hondalog-$Version-android.apk"
    $setHere = Read-KeystorePassword
    try {
        if (Test-Path $AppScript) {
            # The standalone app: the web build inside the APK, native USB bridge (android\README.md).
            Write-Step "android: standalone app (android\build.ps1)"
            $shell = if (Test-Command "pwsh") { "pwsh" } else { "powershell" }
            Invoke-Native $shell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $AppScript, "-Out", $apkOut) "android\build.ps1"
            if (-not (Test-Path $apkOut)) { Fail "android\build.ps1 made no $apkOut" }
        } elseif (Test-Path $TwaScript) {
            Write-Step "android: Bubblewrap TWA (web\android-twa\build-apk.ps1)"
            $m = Get-Content (Join-Path $WebDir "android-twa\twa-manifest.json") -Raw
            if ($m -match '"appVersionName"\s*:\s*"([^"]+)"' -and $Matches[1] -ne $Version) {
                Write-Warn2 "twa-manifest.json appVersionName is $($Matches[1]), not $Version (bump it and appVersionCode)"
            }
            # build-apk.ps1 builds unsigned and signs with sign-apk.ps1 (HONDALOG_KS_PASS)
            Remove-Item $TwaApk -ErrorAction SilentlyContinue
            $shell = if (Test-Command "pwsh") { "pwsh" } else { "powershell" }
            Invoke-Native $shell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $TwaScript) "build-apk.ps1"
            if (-not (Test-Path $TwaApk)) { Fail "Bubblewrap made no $TwaApk" }
            Copy-Item $TwaApk $apkOut -Force
            & (Join-Path $PSScriptRoot "sign-apk.ps1") -Apk $apkOut -VerifyOnly
        } else {
            Write-Step "android: Tauri (npx tauri android build --apk)"
            Push-Location $WebDir
            try { Invoke-Native "npx.cmd" @("tauri", "android", "build", "--apk") "tauri android build" } finally { Pop-Location }
            $unsigned = Get-ChildItem (Join-Path $TauriAndroid "app\build\outputs\apk") -Recurse -Filter "*release-unsigned.apk" |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if (-not $unsigned) { Fail "no *release-unsigned.apk under $TauriAndroid\app\build\outputs\apk" }
            & (Join-Path $PSScriptRoot "sign-apk.ps1") -Apk $unsigned.FullName -Out $apkOut
        }
    } finally {
        if ($setHere) { Remove-Item Env:\HONDALOG_KS_PASS -ErrorAction SilentlyContinue }
    }
    Write-Ok (Split-Path -Leaf $apkOut)
}

function Build-Firmware {
    Write-Step "firmware: CYD black box in WSL ($Distro)"
    $a = @("--out", $OutWsl, "--version", $Version, "--rev", $Rev)
    if ($StrictTests) { $a += "--strict-tests" }
    if ($Clean) { $a += "--clean" }
    $code = Invoke-WslScript $Distro (Join-Path $PSScriptRoot "build-firmware-wsl.sh") $a
    if ($code -ne 0) { Fail "build-firmware-wsl.sh failed (exit $code)" }
}

$results = [ordered]@{}
foreach ($t in $Targets) {
    if ($blocked.ContainsKey($t)) { $results[$t] = "skipped: prerequisites missing"; continue }
    try {
        switch ($t) {
            "web" { Build-Web }
            "windows" { Build-Windows }
            "linux" { Build-Linux }
            "android" { Build-Android }
            "firmware" { Build-Firmware }
        }
        $results[$t] = "ok"
    } catch {
        $results[$t] = "FAILED: $($_.Exception.Message)"
        Write-Host "   $t failed: $($_.Exception.Message)" -ForegroundColor Red
        if (-not $KeepGoing) { break }
    }
}

# ---------------------------------------------------------------- checksums + summary
@(
    "hondalog $Version",
    "source: $Rev$(if ($Dirty) { ' + uncommitted changes' })",
    "built:  $(Get-Date -Format 'yyyy-MM-dd HH:mm') on $env:COMPUTERNAME",
    ""
) + ($results.GetEnumerator() | ForEach-Object { "{0,-9} {1}" -f $_.Key, $_.Value }) |
    Set-Content -Encoding ascii (Join-Path $Out "BUILD-INFO.txt")

Write-Step "SHA256SUMS"
$sums = Write-Sha256Sums $Out
$sums | ForEach-Object { Write-Host "   $_" }

Write-Step "summary ($Out)"
$failed = 0
foreach ($k in $results.Keys) {
    $v = $results[$k]
    if ($v -eq "ok") { Write-Ok $k } else { Write-Host "   --   $k  $v" -ForegroundColor Yellow; $failed++ }
}
foreach ($t in $Targets) { if (-not $results.Contains($t)) { Write-Host "   --   $t  not run (stopped earlier)" -ForegroundColor Yellow; $failed++ } }
if ($Dirty) { Write-Warn2 "built from a tree with uncommitted changes: commit before publish-release.ps1" }
if ($failed) { exit 1 }
Write-Host "next: scripts\release\publish-release.ps1 -Version $Version [-Draft]" -ForegroundColor Green
