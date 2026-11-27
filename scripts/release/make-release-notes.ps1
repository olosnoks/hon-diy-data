<#
.SYNOPSIS
  Write the release notes for v<version>: release-notes-template.md filled with
  the commits since the last tag.

.DESCRIPTION
  publish-release.ps1 calls this; run it alone to preview the notes.
  Placeholders: {{VERSION}} {{DATE}} {{PREVIOUS}} {{CHANGES}} {{PAGES_URL}}.
  Rows of the download table whose file is not in release\<version>\ are dropped.

.EXAMPLE
  scripts\release\make-release-notes.ps1 -Version 0.2.0
#>
param(
    [string]$Version,
    [string]$Out,                  # write here; default: print to the console
    [string]$Template = (Join-Path $PSScriptRoot "release-notes-template.md"),
    [string]$Since,                # tag or commit to start from; default the newest tag
    [string]$AssetDir              # default release\<version>
)

. "$PSScriptRoot\common.ps1"

$Version = Get-ReleaseVersion $Version
if (-not $AssetDir) { $AssetDir = Join-Path $RepoRoot "release\$Version" }

if (-not $Since) {
    try { $Since = Invoke-Git describe --tags --abbrev=0 HEAD } catch { $Since = "" }
}
if ($Since) {
    $log = Invoke-Git log "$Since..HEAD" --no-merges "--format=- %s (%h)"
    $previous = $Since
} else {
    $log = Invoke-Git log --no-merges -n 60 "--format=- %s (%h)"
    $previous = "the start (first tagged release; the newest 60 commits)"
}
if (-not $log) { $log = "- no commits since $previous" }

$names = @()
if (Test-Path $AssetDir) { $names = Get-ChildItem $AssetDir -File | ForEach-Object { $_.Name } }
$lines = (Get-Content $Template -Raw) -split "`r?`n" | Where-Object {
    # drop table rows for files that were not built
    if ($_ -match '^\| `([^`]+)`') {
        $f = $Matches[1] -replace '\{\{VERSION\}\}', $Version
        return ($names.Count -eq 0) -or ($names -contains $f)
    }
    return $true
}
$text = ($lines -join "`n").
    Replace("{{VERSION}}", $Version).
    Replace("{{DATE}}", (Get-Date -Format "yyyy-MM-dd")).
    Replace("{{PREVIOUS}}", $previous).
    Replace("{{PAGES_URL}}", $PagesUrl).
    Replace("{{CHANGES}}", $log)

if ($Out) {
    [System.IO.File]::WriteAllText($Out, $text.TrimEnd() + "`n")
    Write-Host "notes: $Out"
} else {
    $text
}
