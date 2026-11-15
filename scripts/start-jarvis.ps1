# Starts Jarvis (API + web UI) with a valid Ollama URL and any cloud keys from
# ~\.openjarvis\cloud-keys.env  (lines like OPENROUTER_API_KEY=...).
$env:OLLAMA_HOST = 'http://127.0.0.1:11434'
$keys = Join-Path $env:USERPROFILE '.openjarvis\cloud-keys.env'
if (Test-Path $keys) {
    Get-Content $keys | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$') { Set-Item "Env:$($Matches[1])" $Matches[2] }
    }
}
Set-Location (Join-Path $PSScriptRoot '..')
uv run jarvis gui @args
