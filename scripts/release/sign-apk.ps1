<#
.SYNOPSIS
  Sign (or re-check) an Android APK with the release key outside the repo.

.DESCRIPTION
  zipalign + apksigner from the newest Android SDK build-tools. The keystore is
  %USERPROFILE%\.hondalog-keys\hondalog-release.jks (create-keystore.ps1); its
  password comes from HONDALOG_KS_PASS or is asked for, and is handed to
  apksigner as "env:HONDALOG_KS_PASS", never on a command line or in a file.

  -VerifyOnly checks an already signed APK and prints its signer.

.EXAMPLE
  scripts\release\sign-apk.ps1 -Apk app-release-unsigned.apk -Out release\0.2.0\hondalog-0.2.0-android.apk
#>
param(
    [Parameter(Mandatory = $true)][string]$Apk,
    [string]$Out,
    [switch]$VerifyOnly,
    [string]$Keystore = (Join-Path $env:USERPROFILE ".hondalog-keys\hondalog-release.jks"),
    [string]$Alias = "hondalog"
)

. "$PSScriptRoot\common.ps1"

function Get-AndroidSdk {
    foreach ($d in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA "Android\Sdk"))) {
        if ($d -and (Test-Path $d)) { return $d }
    }
    Fail "Android SDK not found (set ANDROID_HOME)"
}

# newest build-tools that has apksigner
function Get-BuildTools {
    $sdk = Get-AndroidSdk
    $bt = Join-Path $sdk "build-tools"
    $dir = Get-ChildItem $bt -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "apksigner.bat") } |
        Sort-Object { try { [version]($_.Name -replace '-.*$', '') } catch { [version]"0.0" } } -Descending |
        Select-Object -First 1
    if (-not $dir) {
        Fail "no Android build-tools with apksigner in $bt. Install them once:  & `"$sdk\cmdline-tools\latest\bin\sdkmanager.bat`" `"build-tools;34.0.0`""
    }
    return $dir.FullName
}

if (-not (Test-Path $Apk)) { Fail "$Apk not found" }
$Bt = Get-BuildTools
$Apksigner = Join-Path $Bt "apksigner.bat"
$Zipalign = Join-Path $Bt "zipalign.exe"

if ($VerifyOnly) {
    Invoke-Native $Apksigner @("verify", "--print-certs", $Apk) "apksigner verify"
    exit 0
}

if (-not $Out) { Fail "-Out is required when signing" }
if (-not (Test-Path $Keystore)) { Fail "keystore $Keystore not found: run scripts\release\create-keystore.ps1 once" }

$setHere = $false
if (-not $env:HONDALOG_KS_PASS) {
    $s = Read-Host -AsSecureString "Password for $Keystore"
    $env:HONDALOG_KS_PASS = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
    $setHere = $true
}
try {
    $aligned = "$Out.aligned.tmp"
    Remove-Item $aligned, $Out -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Split-Path -Parent ([System.IO.Path]::GetFullPath($Out))) | Out-Null
    Invoke-Native $Zipalign @("-f", "-p", "4", $Apk, $aligned) "zipalign"
    Invoke-Native $Apksigner @("sign", "--ks", $Keystore, "--ks-key-alias", $Alias,
        "--ks-pass", "env:HONDALOG_KS_PASS", "--key-pass", "env:HONDALOG_KS_PASS",
        "--out", $Out, $aligned) "apksigner sign"
    Remove-Item $aligned, "$Out.idsig" -ErrorAction SilentlyContinue
    Invoke-Native $Apksigner @("verify", $Out) "apksigner verify"
    Write-Ok "signed: $Out"
} finally {
    if ($setHere) { Remove-Item Env:\HONDALOG_KS_PASS -ErrorAction SilentlyContinue }
}
