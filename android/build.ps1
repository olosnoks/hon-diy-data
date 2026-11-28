<#
.SYNOPSIS
  Build the standalone hondalog APK on Windows and sign it with the release key.

.DESCRIPTION
  The web app (web\) is built into the APK and runs in the app's own WebView,
  with the native USB bridge for the KKL cable (see android\README.md). Uses
  the Android SDK you already have (build-tools with d8, any platforms\android-*),
  a JDK 17+, and Node for the web build. No Gradle.

  Signs with %USERPROFILE%\.hondalog-keys\hondalog-release.jks through
  scripts\release\sign-apk.ps1 (asks for the password unless HONDALOG_KS_PASS is set).

.EXAMPLE
  android\build.ps1                 # -> android\build\hondalog-<v>-android.apk (signed)
  android\build.ps1 -NoSign         # -> android\build\hondalog-<v>-android-unsigned.apk only
  android\build.ps1 -Out release\0.2.0\hondalog-0.2.0-android.apk
#>
param(
    [switch]$NoSign,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$build = Join-Path $here 'build'

function Step($m) { Write-Host "== $m" -ForegroundColor Cyan }
# Tool output (stderr too) goes to the console as plain text. Windows PowerShell 5.1
# turns a native tool's stderr into errors when the caller redirects it (build-all.ps1
# does), so a javac "Note:" or a d8 warning would stop the build. Exit codes decide.
function Run($exe, [string[]]$argv, $what) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $exe @argv 2>&1 | ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    if ($code) { throw "$what failed (exit $code)" }
}

# ---- tools
$sdk = @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA 'Android\Sdk')) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $sdk) { throw 'Android SDK not found (set ANDROID_HOME)' }
$bt = Get-ChildItem (Join-Path $sdk 'build-tools') -Directory |
    Where-Object { (Test-Path (Join-Path $_.FullName 'd8.bat')) -and (Test-Path (Join-Path $_.FullName 'aapt.exe')) } |
    Sort-Object { try { [version]($_.Name -replace '-.*$', '') } catch { [version]'0.0' } } -Descending | Select-Object -First 1
if (-not $bt) { throw 'no build-tools with d8 and aapt (sdkmanager "build-tools;34.0.0")' }
$plat = Get-ChildItem (Join-Path $sdk 'platforms') -Directory -Filter 'android-*' |
    Where-Object { Test-Path (Join-Path $_.FullName 'android.jar') } |
    Sort-Object { [int](($_.Name -replace '^android-', '') -replace '\D.*$', '') } -Descending | Select-Object -First 1
if (-not $plat) { throw 'no platforms\android-*\android.jar (sdkmanager "platforms;android-34")' }
$jar = Join-Path $plat.FullName 'android.jar'
$aapt = Join-Path $bt.FullName 'aapt.exe'
$d8 = Join-Path $bt.FullName 'd8.bat'
$zipalign = Join-Path $bt.FullName 'zipalign.exe'
$jdkBin = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME 'bin' } else { Split-Path (Get-Command javac).Source }
$javac = Join-Path $jdkBin 'javac.exe'
$jartool = Join-Path $jdkBin 'jar.exe'
foreach ($t in @($javac, $jartool)) { if (-not (Test-Path $t)) { throw "JDK tool missing: $t (install a JDK 17+, set JAVA_HOME)" } }
if (-not (Test-Path (Join-Path $repo 'web\node_modules'))) { throw 'web\node_modules missing: cd web; npm ci' }

$version = (Get-Content (Join-Path $repo 'web\package.json') -Raw | ConvertFrom-Json).version
$p = ($version -replace '-.*$', '').Split('.')
$code = [int]$p[0] * 10000 + [int]$p[1] * 100 + [int]$p[2]   # 0.2.0 -> 200 (the old TWA was 1)
if ($code -le 1) { $code = 2 }
Write-Host "hondalog $version (versionCode $code), build-tools $($bt.Name), $($plat.Name)"

# ---- clean
if (Test-Path $build) { Remove-Item $build -Recurse -Force }
$assets = Join-Path $build 'assets'
New-Item -ItemType Directory -Force (Join-Path $assets 'www'), (Join-Path $build 'gen'), (Join-Path $build 'classes') | Out-Null

Step 'web build'
$webOut = Join-Path $build 'web'
Push-Location (Join-Path $repo 'web')
$oldBase = $env:HONDALOG_BASE
try {
    $env:HONDALOG_BASE = '/'
    Run 'npx.cmd' @('vite', 'build', '--outDir', $webOut, '--emptyOutDir', '--logLevel', 'warn') 'vite build'
} finally { $env:HONDALOG_BASE = $oldBase; Pop-Location }
Get-ChildItem $webOut -Recurse -File | Where-Object { $_.Extension -ne '.map' -and $_.Name -ne 'sw.js' } | ForEach-Object {
    $rel = $_.FullName.Substring($webOut.Length + 1)
    $dest = Join-Path (Join-Path $assets 'www') $rel
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest
}
Copy-Item (Join-Path $here 'assets\hondalog-android.js') $assets

Step 'resources'
$manifest = Join-Path $build 'AndroidManifest.xml'
(Get-Content (Join-Path $here 'AndroidManifest.xml') -Raw).Replace('@VERSION_CODE@', "$code").Replace('@VERSION_NAME@', $version) |
    Set-Content -Encoding utf8 $manifest
$res = Join-Path $build 'resources.ap_'
Run $aapt @('package', '-f', '-M', $manifest, '-S', (Join-Path $here 'res'), '-A', $assets, '-I', $jar,
    '-m', '-J', (Join-Path $build 'gen'), '-F', $res, '--ignore-assets', '!.svn:!.git:.*:!CVS:*~',
    '-0', 'wasm', '-0', 'png', '-0', 'otf', '-0', 'ttf') 'aapt package'

Step 'java'
$sources = @(Get-ChildItem (Join-Path $here 'src') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
$sources += (Join-Path $build 'gen\com\olosnoks\hondalog\R.java')
$srcList = Join-Path $build 'sources.txt'
# javac @file: one path per line, quoted, forward slashes
($sources | ForEach-Object { '"' + ($_ -replace '\\', '/') + '"' }) | Set-Content -Encoding ascii $srcList
Run $javac @('-nowarn', '-Xlint:none', '-XDsuppressNotes', '-source', '8', '-target', '8', '-encoding', 'UTF-8',
    '-bootclasspath', $jar, '-d', (Join-Path $build 'classes'), "@$srcList") 'javac'
$classesJar = Join-Path $build 'classes.jar'
Run $jartool @('cf', $classesJar, '-C', (Join-Path $build 'classes'), '.') 'jar'
Run $d8 @('--release', '--min-api', '23', '--lib', $jar, '--output', $build, $classesJar) 'd8'

Step 'apk'
$apk = Join-Path $build 'app.apk'
Copy-Item $res $apk
Push-Location $build
try { Run $aapt @('add', '-f', 'app.apk', 'classes.dex') 'aapt add' } finally { Pop-Location }
$unsigned = Join-Path $build "hondalog-$version-android-unsigned.apk"
Run $zipalign @('-f', '-p', '4', $apk, $unsigned) 'zipalign'
Write-Host "unsigned: $unsigned"
if ($NoSign) { return }

Step 'sign (release key)'
if (-not $Out) { $Out = Join-Path $build "hondalog-$version-android.apk" }
& (Join-Path $repo 'scripts\release\sign-apk.ps1') -Apk $unsigned -Out $Out
Write-Host "APK: $Out" -ForegroundColor Green
