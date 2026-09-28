$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run uv sync first.' }
& $pythonExe scripts/local_postgres.py
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
& $pythonExe apps/api/manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
& $pythonExe apps/api/manage.py bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Source setup failed.' }
$opensearchHome = Join-Path (Split-Path -Parent $projectRoot) '.services\opensearch-3.8.0'
$opensearchPidFile = Join-Path $projectRoot '.local\opensearch.pid'
$opensearchReady = $false
try {
    $null = Invoke-RestMethod -UseBasicParsing -Uri 'http://127.0.0.1:9200' -TimeoutSec 2
    $opensearchReady = $true
} catch {}
if (-not $opensearchReady -and (Test-Path -LiteralPath (Join-Path $opensearchHome 'bin\opensearch.bat'))) {
    $logRoot = Join-Path (Split-Path -Parent $projectRoot) '.services\opensearch-logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    try {
        $searchProcess = Start-Process -FilePath (Join-Path $opensearchHome 'bin\opensearch.bat') -WorkingDirectory $opensearchHome -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'service-stdout.log') -RedirectStandardError (Join-Path $logRoot 'service-stderr.log')
        Set-Content -LiteralPath $opensearchPidFile -Value $searchProcess.Id
    } catch { Write-Warning 'OpenSearch could not start. Policy search will use the database fallback.' }
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-RestMethod -UseBasicParsing -Uri 'http://127.0.0.1:9200' -TimeoutSec 2
            $opensearchReady = $true
            break
        } catch {}
    }
    if (-not $opensearchReady) { Write-Warning 'OpenSearch is not ready yet. Continuing with database search; inspect .services/opensearch-logs.' }
}
$services = @(
    @{ Name='api'; Exe=$pythonExe; Args=@('apps/api/manage.py','runserver','127.0.0.1:8000','--noreload') },
    @{ Name='worker'; Exe=$pythonExe; Args=@('apps/api/manage.py','local_worker') },
    @{ Name='aiworker'; Exe=$pythonExe; Args=@('apps/api/manage.py','local_ai_worker') },
    @{ Name='wikiworker'; Exe=$pythonExe; Args=@('apps/api/manage.py','local_wiki_worker') },
    @{ Name='web'; Exe=(Get-Command node.exe).Source; Args=@('apps/web/node_modules/next/dist/bin/next','dev','apps/web','--hostname','127.0.0.1') }
)
foreach ($service in $services) {
    $pidFile = Join-Path $projectRoot ".local\$($service.Name).pid"
    if (Test-Path -LiteralPath $pidFile) {
        $savedProcessId = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedProcessId)) {
            $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $savedProcessId" -ErrorAction SilentlyContinue
            $matchingArgs = $existing -and @($service.Args | Where-Object { -not $existing.CommandLine.Contains($_) }).Count -eq 0
            if ($matchingArgs -and $existing.ExecutablePath -eq $service.Exe) { Write-Output "$($service.Name) already running."; continue }
        }
    }
    $process = Start-Process -FilePath $service.Exe -ArgumentList $service.Args -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput ".local\$($service.Name).log" -RedirectStandardError ".local\$($service.Name).error.log"
    Set-Content -LiteralPath $pidFile -Value $process.Id
    Write-Output "$($service.Name) started (PID $($process.Id))."
}
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/v1/health' -TimeoutSec 3
        if ($health.status -eq 'ok') { break }
    } catch {}
    Start-Sleep -Seconds 1
}
& (Join-Path $PSScriptRoot 'check_local.ps1')
Write-Output 'Preview: http://127.0.0.1:3000 — logs in .local. Create your user with manage.py createsuperuser.'
