<#
.SYNOPSIS
  One-time: make the Android release signing key, outside the repo.

.DESCRIPTION
  Creates %USERPROFILE%\.hondalog-keys\hondalog-release.jks (PKCS12, RSA 2048,
  alias "hondalog", valid ~27 years) with the JDK's keytool, and prints its
  SHA-256 fingerprint (for the TWA's assetlinks.json).

  The password is never written to disk or to the repo: it comes from the
  HONDALOG_KS_PASS environment variable, or you are asked for it (twice). A
  PKCS12 keystore uses the same password for the key. build-all.ps1 reads the
  same variable (or asks) when it signs the APK.

  Back the .jks up somewhere safe (and the password in a password manager):
  an app signed with a lost key can never be updated in place.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\create-keystore.ps1
#>
param(
    [string]$KeyDir = (Join-Path $env:USERPROFILE ".hondalog-keys"),
    [string]$Name = "hondalog-release.jks",
    [string]$Alias = "hondalog",
    [string]$DName = "CN=hondalog, O=hon-diy-data"
)

. "$PSScriptRoot\common.ps1"

function Find-Keytool {
    if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\keytool.exe"))) { return (Join-Path $env:JAVA_HOME "bin\keytool.exe") }
    $c = Get-Command keytool -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    Fail "keytool not found: install a JDK 17+ (e.g. winget install EclipseAdoptium.Temurin.21.JDK) and set JAVA_HOME"
}

$Keytool = Find-Keytool
$Ks = Join-Path $KeyDir $Name
$repo = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\') + '\'
if ([System.IO.Path]::GetFullPath($Ks).StartsWith($repo, [System.StringComparison]::OrdinalIgnoreCase)) {
    Fail "the keystore must live outside the repo ($Ks is inside $RepoRoot)"
}
if (Test-Path $Ks) { Fail "$Ks already exists: not overwriting a signing key (move it away first if you really mean it)" }

$setHere = $false
if (-not $env:HONDALOG_KS_PASS) {
    $p1 = Read-Host -AsSecureString "New keystore password (6+ characters)"
    $p2 = Read-Host -AsSecureString "Again"
    $b1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p1))
    $b2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p2))
    if ($b1 -ne $b2) { Fail "the passwords differ" }
    if ($b1.Length -lt 6) { Fail "keytool needs at least 6 characters" }
    $env:HONDALOG_KS_PASS = $b1      # this process only; keytool reads it with -storepass:env
    $setHere = $true
}

try {
    New-Item -ItemType Directory -Force $KeyDir | Out-Null
    Write-Step "creating $Ks"
    Invoke-Native $Keytool @("-genkeypair", "-keystore", $Ks, "-storetype", "PKCS12", "-alias", $Alias,
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", $DName,
        "-storepass:env", "HONDALOG_KS_PASS") "keytool -genkeypair"
    # only the current user may read the key folder
    & icacls $KeyDir /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null

    Write-Step "SHA-256 fingerprint (for assetlinks.json)"
    $list = & $Keytool -list -v -keystore $Ks -alias $Alias -storepass:env HONDALOG_KS_PASS 2>&1
    ($list | Select-String "SHA256:") | ForEach-Object { Write-Host "   $($_.Line.Trim())" }
} finally {
    if ($setHere) { Remove-Item Env:\HONDALOG_KS_PASS -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Ok "keystore: $Ks (alias $Alias)"
Write-Host "   Back it up now. For builds, set the password per session:"
Write-Host "     `$env:HONDALOG_KS_PASS = Read-Host -MaskInput 'keystore password'    (PowerShell 7)"
Write-Host "   or let build-all.ps1 ask for it."
