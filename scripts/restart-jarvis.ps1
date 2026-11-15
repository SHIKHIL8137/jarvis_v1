# Stops everything Jarvis (API :8000, UI :5173, wake-word listener), then starts it again.
#   .\scripts\restart-jarvis.ps1          restart all
#   .\scripts\restart-jarvis.ps1 -Stop    only stop
param([switch]$Stop)
$root = Join-Path $PSScriptRoot '..'
Get-NetTCPConnection -LocalPort 8000, 5173 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'openjarvis\.wake' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($Stop) { 'Jarvis stopped.'; return }
Start-Process -WindowStyle Hidden (Join-Path $root '.venv\Scripts\pythonw.exe') '-m', 'openjarvis.wake'
& (Join-Path $PSScriptRoot 'start-jarvis.ps1')
