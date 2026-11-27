<#
.SYNOPSIS
  Driving mode for the laptop: screen stays on (dimmer) so the sensors stay
  visible, everything else is tuned for battery life. -Off puts it all back.

.DESCRIPTION
  On:
    - a power plan "hondalog driving" (a copy of Power saver), made active:
      screen never turns off and the laptop never sleeps or hibernates on battery,
      closing the lid does nothing, CPU capped (default 60 %), PCIe link power
      savings on, USB selective suspend OFF (it can drop the KKL cable / CYD),
    - screen brightness set (default 40 %),
    - Windows battery saver kicks in at once (it dims and holds back background apps),
    - optionally Wi-Fi and Bluetooth off (-Radios; the web app then runs offline,
      the GPS map shows cached tiles only).
  Off: the plan that was active before comes back, brightness too; radios on again
  if this script turned them off. Nothing is uninstalled; run again any time.

  No admin needed for the power plan. -Radios uses the radio switches every user has.

.EXAMPLE
  tools\driving-mode.ps1            # on
  tools\driving-mode.ps1 -Brightness 30 -CpuMax 50 -Radios
  tools\driving-mode.ps1 -Off
#>
param(
    [switch]$Off,
    [ValidateRange(5, 100)][int]$Brightness = 40,
    [ValidateRange(20, 100)][int]$CpuMax = 60,
    [switch]$Radios
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'hondalog'
$stateFile = Join-Path $stateDir 'driving-mode.json'
$planName = 'hondalog driving'
$powerSaver = 'a1841308-3541-4fab-bc81-f71556f20b4a'

# powercfg aliases and GUIDs (documented by Microsoft; the GUIDs work on every language of Windows)
$SUB_VIDEO = '7516b95f-f776-4464-8c53-06167f40cc99'; $VIDEOIDLE = '3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e'
$BRIGHT = 'aded5e82-b909-4619-9949-f5d71dac0bcb'; $DIMBRIGHT = 'f1fbfde2-a960-4165-9f88-50667911ce96'
$ADAPTIVE = 'fbd9aa66-9553-4097-ba44-ed6e9d65eab8'
$SUB_SLEEP = '238c9fa8-0aad-41ed-83f4-97be242c8f20'; $STANDBY = '29f6c1db-86da-48c5-9fdb-f2b67b1f44da'; $HIBER = '9d7815a6-7ee4-497e-8888-515a05f02364'
$SUB_BUTTONS = '4f971e89-eebd-4455-a8de-9e59040e7347'; $LID = '5ca83367-6e45-459f-a27b-476b1d01c936'
$SUB_PROC = '54533251-82be-4824-96c1-47b60b740d00'; $PMAX = 'bc5038f7-23e0-4960-96da-33abaf5935ec'; $PMIN = '893dee8e-2bef-41e0-89c6-b55d0929964c'
$SUB_PCIE = '501a4d13-42af-4429-9fd1-a8218c268e20'; $ASPM = 'ee12f906-d277-404b-b6da-e5fa1a576df5'
$SUB_USB = '2a737441-1930-4402-8d77-b2bebba308a3'; $USBSUSP = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
$SUB_ES = 'de830923-a562-41af-a086-e3a2c6bad2da'; $ESTHRESH = 'e69653ca-cf7f-4f05-aa73-cb833fa90ad4'

function Get-ActivePlan { ((powercfg /getactivescheme) -replace '.*GUID:\s*([0-9a-f-]+).*', '$1').Trim() }
function Get-PlanByName($name) {
    foreach ($l in powercfg /list) { if ($l -match 'GUID:\s*([0-9a-f-]+)\s+\((.+?)\)' -and $Matches[2] -eq $name) { return $Matches[1] } }
    return $null
}
function Set-Both($plan, $sub, $setting, [int]$ac, [int]$dc) {
    powercfg /setacvalueindex $plan $sub $setting $ac | Out-Null
    powercfg /setdcvalueindex $plan $sub $setting $dc | Out-Null
}
function Get-Brightness { try { (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness } catch { $null } }
function Set-Brightness([int]$pct) {
    try { (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | Select-Object -First 1) | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{ Timeout = 1; Brightness = [byte]$pct } | Out-Null; $true }
    catch { Write-Warning "could not set brightness here (external monitor?): $($_.Exception.Message)"; $false }
}
# Wi-Fi / Bluetooth through Windows.Devices.Radios (the same switches as the quick settings; no admin)
function Set-Radios([string]$state) {
    try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
        function Await($op, [type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
        [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
        Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        foreach ($r in $radios) {
            if ($r.Kind -in 'WiFi', 'Bluetooth') { Await ($r.SetStateAsync($state)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null; Write-Host "  $($r.Kind): $state" }
        }
    } catch { Write-Warning "radios: $($_.Exception.Message)" }
}

if ($Off) {
    if (-not (Test-Path $stateFile)) { Write-Host 'Driving mode is not on (no saved state).'; exit 0 }
    $s = Get-Content $stateFile -Raw | ConvertFrom-Json
    if ($s.previousPlan) { powercfg /setactive $s.previousPlan; Write-Host "Power plan restored." }
    $p = Get-PlanByName $planName
    powercfg /setdcvalueindex SCHEME_CURRENT $SUB_ES $ESTHRESH ([int]$s.esThreshold) | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    if ($null -ne $s.brightness) { [void](Set-Brightness ([int]$s.brightness)) }
    if ($s.radiosOff) { Set-Radios 'On' }
    Remove-Item $stateFile
    Write-Host 'Driving mode off.'
    exit 0
}

New-Item -ItemType Directory -Force $stateDir | Out-Null
$prev = Get-ActivePlan
$plan = Get-PlanByName $planName
if (-not $plan) {
    # Power saver is hidden on some laptops (this one lists only Balanced / Acer / High
    # performance): fall back to a copy of Balanced; the settings below do the saving.
    $out = powercfg /duplicatescheme $powerSaver 2>$null
    if ($LASTEXITCODE -ne 0 -or "$out" -notmatch 'GUID') { $out = powercfg /duplicatescheme '381b4222-f694-41f0-9685-ff5bb260df2e' }
    $plan = ("$out" -replace '.*GUID:\s*([0-9a-f-]+).*', '$1').Trim()
    powercfg /changename $plan $planName 'hondalog: screen on, battery first (tools\driving-mode.ps1)' | Out-Null
}
if (-not (Test-Path $stateFile)) {
    # remember what to put back (only the first time: running "on" twice keeps the original)
    $es = ((powercfg /query $prev $SUB_ES $ESTHRESH) | Select-String 'Current DC Power Setting Index:\s*0x([0-9a-f]+)' | Select-Object -First 1)
    $esVal = if ($es) { [Convert]::ToInt32($es.Matches[0].Groups[1].Value, 16) } else { 20 }
    @{ previousPlan = $(if ($prev -ne $plan) { $prev } else { $null }); brightness = Get-Brightness; esThreshold = $esVal; radiosOff = [bool]$Radios } |
        ConvertTo-Json | Set-Content -Encoding utf8 $stateFile
}

Set-Both $plan $SUB_VIDEO $VIDEOIDLE 0 0              # screen never off
Set-Both $plan $SUB_SLEEP $STANDBY 0 0                # never sleep
Set-Both $plan $SUB_SLEEP $HIBER 0 0                  # never hibernate
Set-Both $plan $SUB_BUTTONS $LID 0 0                  # lid: do nothing
Set-Both $plan $SUB_PROC $PMAX 100 $CpuMax            # CPU cap on battery
Set-Both $plan $SUB_PROC $PMIN 5 5
Set-Both $plan $SUB_PCIE $ASPM 2 2                    # PCIe: maximum power savings
Set-Both $plan $SUB_USB $USBSUSP 0 0                  # USB selective suspend OFF: keeps the cable / CYD alive
Set-Both $plan $SUB_VIDEO $ADAPTIVE 0 0               # no adaptive brightness jumping around
Set-Both $plan $SUB_VIDEO $BRIGHT $Brightness $Brightness
Set-Both $plan $SUB_VIDEO $DIMBRIGHT $Brightness $Brightness
powercfg /setactive $plan
powercfg /setdcvalueindex SCHEME_CURRENT $SUB_ES $ESTHRESH 100 | Out-Null   # battery saver on now (on battery)
powercfg /setactive SCHEME_CURRENT | Out-Null
[void](Set-Brightness $Brightness)
if ($Radios) { Set-Radios 'Off' }

Write-Host "Driving mode on: plan '$planName', screen $Brightness %, CPU max $CpuMax % on battery, no sleep, lid does nothing, USB kept awake$(if ($Radios) { ', Wi-Fi/Bluetooth off' })."
Write-Host 'Turn it off with:  tools\driving-mode.ps1 -Off'
