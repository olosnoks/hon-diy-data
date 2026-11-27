# Build hondalog for Windows: a folder with hondalog.exe and the command-line
# tools, and a zip of it. Needs Python 3.11 or newer on PATH (or -Python).
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build-windows.ps1 [-Python C:\Python313\python.exe] [-Clean]
#
# Uses its own environment in build\winvenv (never the .venv the app runs from).
# Output: dist\hondalog\ (run hondalog.exe) and dist\hondalog-<version>-windows-x64.zip

param(
    [string]$Python = "python",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
$Venv = Join-Path $Root "build\winvenv"
$Py = Join-Path $Venv "Scripts\python.exe"

if ($Clean) { Remove-Item -Recurse -Force build\winvenv, build\pyinstaller, dist\hondalog -ErrorAction SilentlyContinue }

if (-not (Test-Path $Py)) {
    Write-Host "== creating $Venv"
    & $Python -m venv $Venv
    if ($LASTEXITCODE) { throw "venv failed" }
}
Write-Host "== installing hondalog and PyInstaller"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q ".[gl]" pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

# the window and taskbar icon, made from the app's own SVG
$Ico = Join-Path $PSScriptRoot "hondalog.ico"
if (-not (Test-Path $Ico)) {
    Write-Host "== making hondalog.ico"
    & $Py -c @"
import sys
from PySide6.QtGui import QGuiApplication, QIcon, QImage
app = QGuiApplication(sys.argv)
icon = QIcon(r'$Root\hondalog\data\hondalog.svg')
icon.pixmap(256, 256).toImage().save(r'$Ico', 'ICO')
"@
}

Write-Host "== PyInstaller"
& $Py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\windows\hondalog.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$Version = (& $Py -c "import importlib.metadata as m; print(m.version('hondalog'))").Trim()
$Rev = (git rev-parse --short HEAD).Trim()
"hondalog $Version ($Rev), built $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Set-Content dist\hondalog\BUILD.txt
$Zip = "dist\hondalog-$Version-windows-x64.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path dist\hondalog -DestinationPath $Zip
Write-Host "== done: dist\hondalog\hondalog.exe and $Zip"
