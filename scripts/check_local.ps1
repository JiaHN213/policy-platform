$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$unavailable = @()
foreach ($endpoint in @(
    @{ Name='API and database'; Url='http://127.0.0.1:8000/api/v1/health' },
    @{ Name='Web and API proxy'; Url='http://127.0.0.1:3000/api/v1/health' }
)) {
    try {
        $result = Invoke-RestMethod -Uri $endpoint.Url -TimeoutSec 5
        if ($result.status -ne 'ok') { throw 'Not ready' }
        Write-Output "OK: $($endpoint.Name)"
    } catch { $unavailable += $endpoint.Name; Write-Warning "Unavailable: $($endpoint.Name)" }
}
foreach ($name in @('worker', 'aiworker', 'wikiworker')) {
    $pidFile = Join-Path $projectRoot ".local\$name.pid"
    $serviceProcessId = 0
    $alive = $false
    if ((Test-Path -LiteralPath $pidFile) -and [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$serviceProcessId)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId" -ErrorAction SilentlyContinue
        $command = @{ worker='local_worker'; aiworker='local_ai_worker'; wikiworker='local_wiki_worker' }[$name]
        $alive = $process -and $process.ExecutablePath -eq (Join-Path $projectRoot '.venv\Scripts\python.exe') -and $process.CommandLine.Contains($command)
    }
    if ($alive) { Write-Output "OK: $name process" }
    else { $unavailable += $name; Write-Warning "Unavailable: $name process" }
}
try {
    $search = Invoke-RestMethod -Uri 'http://127.0.0.1:9200/_cluster/health' -TimeoutSec 5
    Write-Output "OpenSearch: $($search.status)"
} catch { Write-Warning 'OpenSearch unavailable; database search fallback remains available.' }
if ($unavailable.Count) { throw "Services need attention: $($unavailable -join ', '). Run scripts/start_local.ps1." }
Write-Output 'All required local processes are running. AI review still follows its saved on/off setting.'
