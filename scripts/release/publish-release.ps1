<#
.SYNOPSIS
  Tag v<version> and publish release\<version>\ as a GitHub release (gh CLI, no Actions).

.DESCRIPTION
  Refuses when:
    - the working tree has uncommitted changes (release\ itself is ignored),
    - the tag v<version> already exists locally or on the remote, or a GitHub
      release with that tag exists,
    - release\<version>\ is missing, empty, or its SHA256SUMS does not match the files.
  Then: makes the annotated tag, pushes it, and runs
    gh release create v<version> --verify-tag --title "hondalog <version>" --notes-file <notes> <files>
  The notes are release-notes-template.md plus the commits since the last tag
  (make-release-notes.ps1; preview with -WhatIf or that script alone).
  It asks for confirmation before tagging (-Confirm:$false to skip).

  -Draft creates a draft release (the tag is still pushed; publish the draft
  on GitHub, or delete it and the tag to start over).

.EXAMPLE
  scripts\release\publish-release.ps1 -Version 0.2.0 -Draft
.EXAMPLE
  scripts\release\publish-release.ps1 -WhatIf      # checks + notes + plan, changes nothing
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]   # asks before tagging; -Confirm:$false skips the question
param(
    [string]$Version,
    [switch]$Draft,
    [switch]$Prerelease,
    [string]$NotesFile,        # use this file instead of generating the notes
    [string]$Remote = "origin"
)

. "$PSScriptRoot\common.ps1"

Assert-Command "git" "Install Git for Windows."
Assert-Command "gh" "Install the GitHub CLI (winget install GitHub.cli), then 'gh auth login'."
$Version = Get-ReleaseVersion $Version
$Tag = "v$Version"
$Dir = Join-Path $RepoRoot "release\$Version"

Write-Step "checks for $Tag"
$ErrorActionPreference = "Continue"
& gh auth status --hostname github.com *> $null
$authOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $authOk) { Fail "gh is not logged in: run 'gh auth login'" }

$changes = @(Get-TreeChanges)
if ($changes.Count) {
    $changes | Select-Object -First 15 | ForEach-Object { Write-Host "   $_" }
    Fail "the working tree has uncommitted changes: commit or stash them, then rebuild with build-all.ps1"
}
Write-Ok "working tree clean"

$localTag = $true
try { Invoke-Git rev-parse -q --verify "refs/tags/$Tag" | Out-Null } catch { $localTag = $false }
if ($localTag) { Fail "tag $Tag already exists locally (git tag -d $Tag if it was a mistake)" }
$ErrorActionPreference = "Continue"
$remoteTag = (& git -C $RepoRoot ls-remote --tags $Remote "refs/tags/$Tag" 2>&1 | Out-String).Trim()
$lsCode = $LASTEXITCODE
& gh release view $Tag --repo $GitHubRepo *> $null
$relExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if ($lsCode -ne 0) { Fail "cannot reach $Remote to check the tag: $remoteTag" }
if ($remoteTag) { Fail "tag $Tag already exists on $Remote" }
if ($relExists) { Fail "a GitHub release $Tag already exists" }
Write-Ok "tag $Tag is free"

if (-not (Test-Path $Dir)) { Fail "$Dir does not exist: run build-all.ps1 -Version $Version first" }
$sumsFile = Join-Path $Dir "SHA256SUMS"
if (-not (Test-Path $sumsFile)) { Fail "$sumsFile missing: run build-all.ps1" }
$assets = @(Get-ChildItem $Dir -File | Where-Object { $_.Name -notin @("BUILD-INFO.txt") })
foreach ($line in (Get-Content $sumsFile | Where-Object { $_ })) {
    if ($line -notmatch '^([0-9a-f]{64})\s+\*?(.+)$') { Fail "bad line in SHA256SUMS: $line" }
    $f = Join-Path $Dir $Matches[2]
    if (-not (Test-Path $f)) { Fail "SHA256SUMS lists $($Matches[2]), which is not in $Dir" }
    if ((Get-FileHash -Algorithm SHA256 $f).Hash.ToLower() -ne $Matches[1]) { Fail "$($Matches[2]) does not match SHA256SUMS (rebuilt after the sums?)" }
}
$listed = @(Get-Content $sumsFile | Where-Object { $_ } | ForEach-Object { ($_ -split '\s+\*?', 2)[1] })
$unlisted = @($assets | Where-Object { $_.Name -ne "SHA256SUMS" -and $listed -notcontains $_.Name })
if ($unlisted.Count) { Fail "not in SHA256SUMS: $($unlisted.Name -join ', ') (run build-all.ps1 again)" }
if ($assets.Count -lt 2) { Fail "$Dir has no artifacts besides SHA256SUMS" }
Write-Ok "$($assets.Count) files match SHA256SUMS"
$assets | ForEach-Object { Write-Host ("        {0,-44} {1,10:N0} bytes" -f $_.Name, $_.Length) }

$notes = $NotesFile
if (-not $notes) {
    $notes = Join-Path ([System.IO.Path]::GetTempPath()) "hondalog-$Tag-notes.md"
    & (Join-Path $PSScriptRoot "make-release-notes.ps1") -Version $Version -Out $notes | Out-Null
}
if (-not (Test-Path $notes)) { Fail "notes file $notes not found" }

$Head = Invoke-Git rev-parse --short HEAD
$ghArgs = @("release", "create", $Tag, "--repo", $GitHubRepo, "--verify-tag", "--title", "hondalog $Version", "--notes-file", $notes)
if ($Draft) { $ghArgs += "--draft" }
if ($Prerelease) { $ghArgs += "--prerelease" }
$ghArgs += @($assets | ForEach-Object { $_.FullName })

Write-Step "plan"
Write-Host "   git tag -a $Tag $Head -m `"hondalog $Version`""
Write-Host "   git push $Remote $Tag"
Write-Host "   gh release create $Tag --verify-tag --title `"hondalog $Version`" --notes-file $notes$(if ($Draft) { ' --draft' })$(if ($Prerelease) { ' --prerelease' }) <$($assets.Count) files>"
Write-Host "   notes:"
Get-Content $notes | Select-Object -First 40 | ForEach-Object { Write-Host "     | $_" }

if (-not $PSCmdlet.ShouldProcess("$GitHubRepo", "tag $Tag at $Head, push it and create the GitHub release")) {
    Write-Host "nothing changed (-WhatIf or declined)"
    exit 0
}

Write-Step "tagging and publishing"
Invoke-Native "git" @("-C", $RepoRoot, "tag", "-a", $Tag, "-m", "hondalog $Version") "git tag"
try {
    Invoke-Native "git" @("-C", $RepoRoot, "push", $Remote, "refs/tags/$Tag") "git push $Tag"
} catch {
    Write-Warn2 "push failed; removing the local tag again"
    & git -C $RepoRoot tag -d $Tag | Out-Null
    throw
}
Invoke-Native "gh" $ghArgs "gh release create (the tag $Tag is pushed: rerun only this step, or delete the tag with 'git push $Remote :refs/tags/$Tag; git tag -d $Tag')"

Write-Host ""
Write-Host "Released: https://github.com/$GitHubRepo/releases/tag/$Tag$(if ($Draft) { ' (draft: publish it on GitHub)' })" -ForegroundColor Green
