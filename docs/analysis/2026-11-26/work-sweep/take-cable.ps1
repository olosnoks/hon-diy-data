# Take COM21 from hondalog, run the read-only sweep, always give the cable back.
param([int]$Seconds = 210)
$Root = "C:\Users\skons\Documents\hon-diy-data"
Set-Location $Root
$Pause = Join-Path $Root "tools\.watch-pause"
$Work = Join-Path $Root "docs\analysis\2026-11-26\work-sweep"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
function AppProcs { Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "hondalog.exe" -or ($_.Name -eq "python.exe" -and $_.CommandLine -match "hondalog\.exe" -and $_.CommandLine -notmatch "\-m ") } }
$tTake = Get-Date
"take: $($tTake.ToString('o'))"
try {
    New-Item -ItemType File -Path $Pause -Force | Out-Null
    Get-Process | Where-Object { $_.MainWindowTitle -match '^hondalog' -and $_.Path -notlike '*dist*' } | ForEach-Object { [void]$_.CloseMainWindow() }
    $i = 0
    while ((AppProcs) -and $i -lt 30) { Start-Sleep 1; $i++ }
    if (AppProcs) {
        "app still running after 30 s; stopping it"
        AppProcs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    "app closed after $i s at $((Get-Date).ToString('o'))"
    $stamp = Get-Date -Format "HHmmss"
    & $Py (Join-Path $Work "sweep.py") --port COM21 --seconds $Seconds --period 1.0 --out (Join-Path $Work "car-$stamp")
} finally {
    Remove-Item $Pause -Force -ErrorAction SilentlyContinue
    $tGive = Get-Date
    "give back: $($tGive.ToString('o'))  held $([math]::Round(($tGive - $tTake).TotalSeconds,1)) s"
}
