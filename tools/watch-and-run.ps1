# Keep hondalog running on the laptop and relaunch it when new commits land.
#
#   powershell -ExecutionPolicy Bypass -File tools\watch-and-run.ps1 [-Port COM21] [-Branch obd2-live]
#
# Every -Interval seconds: fetch the branch; if it moved, fast-forward,
# reinstall when pyproject.toml changed, close the app properly (so the drive
# being recorded ends cleanly) and start it again. Starts the app if it is not
# running. While tools\.watch-pause exists it leaves the port alone (for the
# probe or the cross-check tool, which need the cable to themselves).
# Log: tools\watch-and-run.log

param(
    [string]$Port = "COM21",
    [string]$Branch = "obd2-live",
    [int]$Interval = 20,
    [switch]$LaptopGps
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Log = Join-Path $PSScriptRoot "watch-and-run.log"
$Pause = Join-Path $PSScriptRoot ".watch-pause"
$Exe = Join-Path $Root ".venv\Scripts\hondalog.exe"
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Say($text) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $text
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function App-Processes {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -match "hondalog\.exe" -and $_.CommandLine -notmatch "\-m "
    }
}

function Stop-App {
    Get-Process | Where-Object { $_.MainWindowTitle -match "^hondalog" } | ForEach-Object { [void]$_.CloseMainWindow() }
    for ($i = 0; $i -lt 15 -and (App-Processes); $i++) { Start-Sleep 1 }
    $left = App-Processes
    if ($left) {
        Say "app did not close in 15 s; stopping it"
        $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "hondalog.exe" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
}

function Start-App {
    if (-not $LaptopGps) { $env:HONDALOG_LAPTOP_GPS = "off" } else { Remove-Item Env:HONDALOG_LAPTOP_GPS -ErrorAction SilentlyContinue }
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -FilePath $Exe -ArgumentList "--port", $Port, "--connect" -WorkingDirectory $Root `
        -RedirectStandardError (Join-Path $env:TEMP "hondalog-err.txt") `
        -RedirectStandardOutput (Join-Path $env:TEMP "hondalog-out.txt") -WindowStyle Normal
    Say ("started the app on {0} at {1}" -f $Port, (git rev-parse --short HEAD))
}

Say "watching origin/$Branch every $Interval s (port $Port)"
while ($true) {
    try {
        git fetch -q origin $Branch 2>$null
        $here = (git rev-parse HEAD).Trim()
        $there = (git rev-parse "origin/$Branch").Trim()
        $paused = Test-Path $Pause
        if ($here -ne $there) {
            $changed = git diff --name-only $here $there
            git merge -q --ff-only "origin/$Branch" 2>&1 | ForEach-Object { Say "git: $_" }
            if ((git rev-parse HEAD).Trim() -eq $there) {
                Say ("updated to {0}: {1}" -f (git log --oneline -1), (($changed | Select-Object -First 8) -join ", "))
                if ($changed -contains "pyproject.toml") {
                    & $Py -m pip install -q -e ".[gl,test]" 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "pip: $_" }
                }
                if (-not $paused) { Stop-App; Start-App }
            }
        }
        if (-not $paused -and -not (App-Processes)) { Start-App }
    } catch {
        Say "error: $_"
    }
    Start-Sleep $Interval
}
