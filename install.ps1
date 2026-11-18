# Install / uninstall the ticket tracker on Windows.
# Runs it in the background at login with Task Scheduler (no console window).
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1              install (or update) and start
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall   stop and remove (keeps your config/data)
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Port 9000   other dashboard port (default 8765)
param(
  [switch]$Uninstall,
  [int]$Port = 8765
)
$ErrorActionPreference = "Stop"

$App   = "doomsday-tracker"
$Task  = "Ticket Tracker ($App)"
$Src   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data  = if ($env:TRACKER_HOME) { $env:TRACKER_HOME } else { Join-Path $env:LOCALAPPDATA $App }
$Log   = Join-Path $Data "tracker.log"
$Files = @("district_tracker.py", "tracker_server.py", "seatmap.py", "district_api.py", "tracker_ui.html")

function Stop-Tracker {
  if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
  }
  # also stop a copy started by hand or left over from an earlier install
  Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$App*tracker_server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
  Stop-Tracker
  if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false
  }
  Write-Host "Removed the background task. Your config and data are still in: $Data"
  exit 0
}

# --- find Python 3.8+ (the "py" launcher from python.org, or python on PATH)
$py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
  $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
  if (-not $exe) { continue }
  try {
    $args_ = @($cand | Select-Object -Skip 1) + @("-c", "import sys; print(sys.executable if sys.version_info >= (3, 8) else '')")
    $path = (& $exe.Source @args_ 2>$null | Select-Object -Last 1)
    if ($path -and (Test-Path $path)) { $py = $path; break }
  } catch { }
}
if (-not $py) {
  Write-Error "Python 3.8+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again."
}
# pythonw.exe = same Python without a console window
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }

# --- copy the app next to its data
New-Item -ItemType Directory -Force -Path $Data | Out-Null
foreach ($f in $Files) { Copy-Item -Force (Join-Path $Src $f) $Data }
Write-Host "Installed app files to: $Data"

# --- background task: start at login, restart if it stops
Stop-Tracker
$action   = New-ScheduledTaskAction -Execute $pyw `
              -Argument "`"$Data\tracker_server.py`" --port $Port --log `"$Log`"" -WorkingDirectory $Data
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
              -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings `
  -Description "Movie ticket tracker (District / TicketNew). Dashboard on http://127.0.0.1:$Port" -Force | Out-Null
Start-ScheduledTask -TaskName $Task

Write-Host "Started background task '$Task'   (log: $Log)"
Write-Host ""
Write-Host "Dashboard: http://127.0.0.1:$Port"
