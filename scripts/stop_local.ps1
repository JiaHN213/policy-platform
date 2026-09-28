param([switch]$KeepDatabase)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
Set-Location -LiteralPath $projectRoot
$patterns = @{
    api = '*apps/api/manage.py*runserver*127.0.0.1:8000*'
    worker = '*apps/api/manage.py*local_worker*'
    aiworker = '*apps/api/manage.py*local_ai_worker*'
    wikiworker = '*apps/api/manage.py*local_wiki_worker*'
    'nanning-crawl' = '*apps/api/manage.py*crawl_nanning*'
    web = '*apps/web/node_modules/next/dist/bin/next*dev*apps/web*'
    opensearch = '*opensearch-3.8.0*opensearch.bat*'
}
function Stop-ServiceTree($processInfo) {
    # Only descendants of the validated project service; never stop by image name.
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($processInfo.ProcessId)")
    foreach ($child in $children) {
        if ($child.CreationDate -ge $processInfo.CreationDate) { Stop-ServiceTree $child }
    }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($processInfo.ProcessId)"
    if ($current -and $current.CreationDate -eq $processInfo.CreationDate) {
        Stop-Process -Id $current.ProcessId -ErrorAction SilentlyContinue
    }
}
foreach ($serviceName in @('web', 'nanning-crawl', 'wikiworker', 'aiworker', 'worker', 'api', 'opensearch')) {
    $pidFile = Join-Path $projectRoot ".local\$serviceName.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $serviceProcessId = [int](Get-Content -LiteralPath $pidFile)
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId"
    if ($serviceProcess) {
        if ($serviceProcess.CommandLine -notlike $patterns[$serviceName]) {
            throw "PID for $serviceName belongs to another command; refusing to stop it."
        }
        Stop-ServiceTree $serviceProcess
    }
    Remove-Item -LiteralPath $pidFile
}
if (-not $KeepDatabase) {
    $pgBin = Join-Path $projectRoot '.local\postgres\Library\bin'
    $env:PATH = "$pgBin;$env:PATH"
    & (Join-Path $pgBin 'pg_ctl.exe') stop -D (Join-Path $projectRoot '.local\pgdata') -m fast -w
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL shutdown failed or it was already stopped.' }
}
Write-Output 'Local application services stopped; database files were preserved.'
