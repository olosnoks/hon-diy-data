<#
.SYNOPSIS
  Build the hondalog web app for GitHub Pages and publish it to the gh-pages branch.

.DESCRIPTION
  1. Builds web/ with HONDALOG_BASE=/hon-diy-data/ (vite.config.ts reads it).
  2. Checks out the gh-pages branch in a temporary git worktree (an orphan
     branch on the first run), replaces its contents with web/dist, adds
     .nojekyll and 404.html (a copy of index.html, the SPA fallback).
  3. Commits and pushes gh-pages. No GitHub Actions: Pages serves the branch.

  -DryRun does everything except the push: the commit stays on the local
  gh-pages branch so you can inspect it (git log gh-pages --stat). A later run
  rebuilds gh-pages from origin/gh-pages, so a dry-run commit is thrown away.

  One-time setup (the owner runs it, after the first real push):
    scripts\release\deploy-pages.ps1 -ConfigurePages
  which calls the GitHub REST API through gh to serve gh-pages / (root):
    gh api -X POST repos/olosnoks/hon-diy-data/pages -f build_type=legacy -f "source[branch]=gh-pages" -f "source[path]=/"
  (or -X PUT with the same fields if Pages is already enabled).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\deploy-pages.ps1 -DryRun
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\deploy-pages.ps1
#>
param(
    [switch]$DryRun,          # commit locally, do not push
    [switch]$SkipBuild,       # publish the existing web/dist (must have been built with the Pages base)
    [switch]$SkipTypecheck,   # vite build without tsc -b first
    [switch]$KeepWorktree,    # leave the temporary worktree on disk (its path is printed)
    [switch]$ConfigurePages,  # one-time: point GitHub Pages at gh-pages / (root), then exit
    [string]$Remote = "origin",
    [string]$Branch = "gh-pages",
    [string]$Version          # for the commit message; default web/package.json
)

. "$PSScriptRoot\common.ps1"

if ($ConfigurePages) {
    Assert-Command "gh" "Install the GitHub CLI (winget install GitHub.cli) and run 'gh auth login'."
    $fields = @("-f", "build_type=legacy", "-f", "source[branch]=$Branch", "-f", "source[path]=/")
    Write-Step "GitHub Pages: serve $Branch / (root) for $GitHubRepo"
    $ErrorActionPreference = "Continue"
    & gh api "repos/$GitHubRepo/pages" --silent 2>$null
    $exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
    if ($exists) { Invoke-Native "gh" (@("api", "-X", "PUT", "repos/$GitHubRepo/pages") + $fields) "gh api PUT pages" }
    else { Invoke-Native "gh" (@("api", "-X", "POST", "repos/$GitHubRepo/pages") + $fields) "gh api POST pages (does $Branch exist on GitHub yet?)" }
    Write-Ok "Pages source set. Site: $PagesUrl (first publish takes a minute or two)"
    exit 0
}

Assert-Command "git" "Install Git for Windows."
$Version = Get-ReleaseVersion $Version
$Rev = Invoke-Git rev-parse --short HEAD

# ---- 1. build
if ($SkipBuild) {
    $Dist = Join-Path $WebDir "dist"
    if (-not (Test-Path (Join-Path $Dist "index.html"))) { Fail "-SkipBuild but web/dist/index.html does not exist" }
    Write-Step "using the existing web/dist (-SkipBuild)"
} else {
    Write-Step "building web/ with HONDALOG_BASE=$PagesBase"
    $Dist = Invoke-WebBuild -Base $PagesBase -SkipTypecheck:$SkipTypecheck
}
$index = Get-Content (Join-Path $Dist "index.html") -Raw
if ($index -notmatch [regex]::Escape($PagesBase)) {
    Fail "web/dist/index.html does not reference ${PagesBase}: it was built for another base (drop -SkipBuild)"
}
Write-Ok "web/dist ready ($((Get-ChildItem $Dist -Recurse -File | Measure-Object).Count) files)"

# ---- 2. gh-pages worktree
Write-Step "preparing $Branch in a temporary worktree"
Invoke-Git worktree prune | Out-Null
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("hondalog-gh-pages-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

$ErrorActionPreference = "Continue"
$null = & git -C $RepoRoot ls-remote --exit-code --heads $Remote $Branch 2>&1
$lsCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($lsCode -ne 0 -and $lsCode -ne 2) { Write-Warn2 "could not reach $Remote (git ls-remote exit $lsCode): treating $Branch as new on the remote" }
$remoteHas = ($lsCode -eq 0)
$localHas = $true
try { Invoke-Git show-ref --verify --quiet "refs/heads/$Branch" | Out-Null } catch { $localHas = $false }

$pushed = $false
try {
    if ($remoteHas) {
        Invoke-Git fetch $Remote "+refs/heads/${Branch}:refs/remotes/$Remote/$Branch" | Out-Null
        # -B: start from what is published, dropping any old local dry-run commit
        Invoke-Git worktree add -B $Branch $Tmp "$Remote/$Branch" | Out-Null
        Write-Ok "from $Remote/$Branch"
    } elseif ($localHas) {
        Invoke-Git worktree add $Tmp $Branch | Out-Null
        Write-Ok "from the local $Branch (not on $Remote yet)"
    } else {
        Invoke-Git worktree add --orphan -b $Branch $Tmp | Out-Null
        Write-Ok "new orphan branch $Branch"
    }

    Get-ChildItem -Path $Tmp -Force | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $Dist "*") -Destination $Tmp -Recurse -Force
    Copy-Item (Join-Path $Tmp "index.html") (Join-Path $Tmp "404.html") -Force   # SPA fallback
    [System.IO.File]::WriteAllText((Join-Path $Tmp ".nojekyll"), "")
    $readme = "hondalog web app, built from $Rev by scripts/release/deploy-pages.ps1.`nGenerated: do not edit this branch by hand. Site: $PagesUrl`n"
    [System.IO.File]::WriteAllText((Join-Path $Tmp "README.md"), $readme)

    $r = Invoke-GitIn $Tmp add -A
    if ($r.Code -ne 0) { Fail "git add in the worktree: $($r.Text)" }
    $r = Invoke-GitIn $Tmp diff --cached --quiet
    if ($r.Code -eq 0) {
        Write-Ok "nothing changed since the last deploy"
    } else {
        $msg = "pages: hondalog web $Version ($Rev)"
        $r = Invoke-GitIn $Tmp commit -q -m $msg
        if ($r.Code -ne 0) { Fail "git commit in the worktree: $($r.Text)" }
        $stat = Invoke-GitIn $Tmp show --stat --oneline --format="%h %s" HEAD
        Write-Ok "committed on ${Branch}:"
        $stat.Text -split "`n" | Select-Object -Last 6 | ForEach-Object { Write-Host "        $_" }
    }

    # ---- 3. push
    if ($DryRun) {
        Write-Step "dry run: not pushed"
        Write-Host "   inspect:  git log $Branch --stat -1"
        Write-Host "   publish:  git push $Remote $Branch   (or run this script without -DryRun)"
        Write-Host "   discard:  git branch -D $Branch      (only if $Branch was never pushed)"
    } else {
        Write-Step "pushing $Branch to $Remote"
        $r = Invoke-GitIn $Tmp push $Remote "${Branch}:${Branch}"
        Write-Host $r.Text
        if ($r.Code -ne 0) { Fail "git push failed" }
        $pushed = $true
        if (-not $remoteHas) {
            Write-Warn2 "first publish: set the Pages source once with  scripts\release\deploy-pages.ps1 -ConfigurePages"
        }
    }
} finally {
    if ($KeepWorktree) {
        Write-Host "   worktree kept: $Tmp  (remove: git worktree remove --force `"$Tmp`")"
    } elseif (Test-Path $Tmp) {
        $ErrorActionPreference = "Continue"
        & git -C $RepoRoot worktree remove --force $Tmp 2>&1 | Out-Null
        if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue; & git -C $RepoRoot worktree prune 2>&1 | Out-Null }
    }
}

Write-Host ""
if ($pushed) { Write-Host "Published: $PagesUrl" -ForegroundColor Green }
else { Write-Host "Pages URL (once pushed): $PagesUrl" -ForegroundColor Green }
