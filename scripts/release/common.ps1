# Shared helpers for the release scripts. Dot-source it:  . "$PSScriptRoot\common.ps1"
# Works in Windows PowerShell 5.1 and PowerShell 7.

$ErrorActionPreference = "Stop"

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:WebDir = Join-Path $script:RepoRoot "web"
$script:PagesUrl = "https://olosnoks.github.io/hon-diy-data/"
$script:PagesBase = "/hon-diy-data/"
$script:GitHubRepo = "olosnoks/hon-diy-data"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) { Write-Host "   OK   $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "   WARN $Text" -ForegroundColor Yellow }

# Stops with a clear message; the caller's try/finally still runs.
function Fail([string]$Text) { throw "release: $Text" }

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# Require a program on PATH or stop, saying how to get it.
function Assert-Command([string]$Name, [string]$Hint) {
    if (-not (Test-Command $Name)) { Fail "$Name not found on PATH. $Hint" }
}

# Runs a native program, throws if it fails. Arguments as an array.
# Exit codes decide: Windows PowerShell 5.1 turns a tool's stderr into errors when the
# caller's output is redirected, so git push's "To https://..." (stderr) or a javac note
# would otherwise count as a failure. stderr is shown as plain text instead.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$What) {
    if (-not $What) { $What = "$Exe $($Arguments -join ' ')" }
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host   # keep tool output out of the function result
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    if ($code -ne 0) { Fail "$What failed (exit $code)" }
}

# git in the repo; returns trimmed stdout, throws on failure unless -AllowFail.
# (a simple function on purpose: $args keeps "--porcelain" and the like as text)
function Invoke-Git {
    $ErrorActionPreference = "Continue"   # PS 5.1 turns redirected stderr into errors
    $out = & git -C $script:RepoRoot @args 2>&1
    $code = $LASTEXITCODE
    $text = ($out | ForEach-Object { "$_" }) -join "`n"
    if ($code -ne 0) { Fail "git $($args -join ' ') failed: $text" }
    return $text.Trim()
}

# Same, in another directory (a worktree), with the exit code returned instead of thrown.
function Invoke-GitIn {
    $dir = $args[0]; $rest = @($args | Select-Object -Skip 1)
    $ErrorActionPreference = "Continue"
    $out = & git -C $dir @rest 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Code = $code; Text = (($out | ForEach-Object { "$_" }) -join "`n").Trim() }
}

# Release version: the argument if given, else "version" in web/package.json.
function Get-ReleaseVersion([string]$Override) {
    if ($Override) { $v = $Override.TrimStart("v") }
    else {
        $pkg = Get-Content (Join-Path $script:WebDir "package.json") -Raw | ConvertFrom-Json
        $v = $pkg.version
    }
    if ($v -notmatch '^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$') { Fail "version '$v' is not semver (x.y.z)" }
    return $v
}

# Version of the Python desktop app (pyproject.toml [project] version).
function Get-PythonAppVersion {
    $m = Select-String -Path (Join-Path $script:RepoRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

# C:\Users\x\y -> /mnt/c/Users/x/y (for paths handed to WSL)
function ConvertTo-WslPath([string]$WinPath) {
    $full = [System.IO.Path]::GetFullPath($WinPath)
    if ($full -notmatch '^([A-Za-z]):\\(.*)$') { Fail "cannot map $full into WSL" }
    return "/mnt/" + $Matches[1].ToLower() + "/" + ($Matches[2] -replace '\\', '/')
}

# Builds the web app into web/dist with the given base. Needs web/node_modules
# (the integrator runs npm install; these scripts never do).
function Invoke-WebBuild([string]$Base, [switch]$SkipTypecheck) {
    Assert-Command "npx" "Install Node.js 20+ (https://nodejs.org)."
    if (-not (Test-Path (Join-Path $script:WebDir "node_modules"))) {
        Fail "web/node_modules is missing: run 'npm ci' in web/ first"
    }
    $old = $env:HONDALOG_BASE
    $env:HONDALOG_BASE = $Base
    Push-Location $script:WebDir
    try {
        if (-not $SkipTypecheck) { Invoke-Native "npx.cmd" @("tsc", "-b") "typecheck (tsc -b; -SkipTypecheck to build anyway)" }
        Invoke-Native "npx.cmd" @("vite", "build", "--emptyOutDir") "vite build"
    } finally {
        Pop-Location
        $env:HONDALOG_BASE = $old
    }
    $dist = Join-Path $script:WebDir "dist"
    if (-not (Test-Path (Join-Path $dist "index.html"))) { Fail "vite build produced no web/dist/index.html" }
    return $dist
}

function Test-WslDistro([string]$Distro) {
    if (-not (Test-Command "wsl")) { return $false }
    # wsl -l prints UTF-16; -q gives bare names
    $names = (& wsl -l -q 2>$null) -replace "`0", "" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    return ($names -contains $Distro)
}

# Runs a bash script inside WSL with arguments; returns the exit code.
function Invoke-WslScript([string]$Distro, [string]$WinScript, [string[]]$Arguments) {
    $s = ConvertTo-WslPath $WinScript
    & wsl -d $Distro -- bash $s @Arguments | Out-Host
    return $LASTEXITCODE
}

function Write-Sha256Sums([string]$Dir) {
    $lines = Get-ChildItem -Path $Dir -File | Where-Object { $_.Name -notin @("SHA256SUMS", "BUILD-INFO.txt") } | Sort-Object Name | ForEach-Object {
        "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower(), $_.Name
    }
    # LF line endings and no BOM, so 'sha256sum -c SHA256SUMS' works on Linux
    [System.IO.File]::WriteAllText((Join-Path $Dir "SHA256SUMS"), (($lines -join "`n") + "`n"))
    return $lines
}

# Uncommitted changes, ignoring the release\ output folder (not tracked).
# Returns the porcelain lines that count; empty when the tree is clean.
function Get-TreeChanges {
    $s = Invoke-Git status --porcelain --untracked-files=normal
    if (-not $s) { return @() }
    return @($s -split "`n" | Where-Object { $_ -and (($_ -replace '^\s*\S+\s+', '') -notmatch '^"?release/') })
}

# Zip the contents of a folder with "/" in entry names (Compress-Archive in
# Windows PowerShell 5.1 writes "\", which unzip on Linux and some browsers mangle).
function New-ZipFromDir([string]$Dir, [string]$Zip, [string[]]$ExcludeExt = @()) {
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    if (Test-Path $Zip) { Remove-Item $Zip }
    $root = (Resolve-Path $Dir).Path.TrimEnd('\') + '\'
    $z = [System.IO.Compression.ZipFile]::Open($Zip, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Get-ChildItem $Dir -Recurse -File | Where-Object { $ExcludeExt -notcontains $_.Extension } | ForEach-Object {
            $name = $_.FullName.Substring($root.Length).Replace('\', '/')
            [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, $_.FullName, $name, [System.IO.Compression.CompressionLevel]::Optimal)
        }
    } catch {
        $z.Dispose(); $z = $null
        Remove-Item $Zip -ErrorAction SilentlyContinue   # no half-written zip in the release folder
        throw
    } finally { if ($z) { $z.Dispose() } }
}
