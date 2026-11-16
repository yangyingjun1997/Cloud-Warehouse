param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [int]$Port = 8766,
    [int]$RequestsPerStep = 80,
    [string]$ConcurrencyRamp = "1,4,8,12"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workDirectory = Join-Path $repositoryRoot ".worktmp"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$databasePath = Join-Path $workDirectory "capacity-e2e-$stamp.sqlite3"
$reportPath = Join-Path $workDirectory "capacity-local-$stamp.json"
$stdoutPath = Join-Path $workDirectory "capacity-server-$stamp.stdout.log"
$stderrPath = Join-Path $workDirectory "capacity-server-$stamp.stderr.log"
$pythonTemp = Join-Path $workDirectory "python-temp"

New-Item -ItemType Directory -Force -Path $workDirectory, $pythonTemp | Out-Null
$pythonPath = (Resolve-Path (Join-Path $repositoryRoot $Python)).Path
$backendPath = Join-Path $repositoryRoot "backend"
$baseUrl = "http://127.0.0.1:$Port/"

$env:TEMP = $pythonTemp
$env:TMP = $pythonTemp
$env:DEBUG = "1"
$env:SECRET_KEY = "capacity-$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))"
$env:DB_ENGINE = "django.db.backends.sqlite3"
$env:DB_NAME = $databasePath
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"
$env:QA_TEST_PASSWORD = "Qa-$([guid]::NewGuid().ToString('N'))!"
$env:WAREHOUSE_LOAD_USERNAME = "qa_admin"
$env:WAREHOUSE_LOAD_PASSWORD = $env:QA_TEST_PASSWORD

& $pythonPath (Join-Path $backendPath "manage.py") migrate --noinput | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Disposable capacity database migration failed."
}
& $pythonPath (Join-Path $repositoryRoot "tools\prepare_e2e_fixture.py") | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Disposable capacity fixture creation failed."
}

$server = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @("manage.py", "runserver", "127.0.0.1:$Port", "--noreload") `
    -WorkingDirectory $backendPath `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

try {
    $ready = $false
    foreach ($attempt in 1..30) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "${baseUrl}health/" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "Disposable capacity server did not become ready."
    }

    & $pythonPath (Join-Path $repositoryRoot "tools\read_only_load_test.py") `
        --base-url $baseUrl `
        --path "/" `
        --path "/warehouse/" `
        --path "/transactions/" `
        --path "/reports/" `
        --path "/requests/" `
        --path "/approvals/" `
        --path "/warehouse/quick-process/" `
        --path "/api/inventory/assets/" `
        --requests $RequestsPerStep `
        --concurrency-ramp $ConcurrencyRamp `
        --warmup 8 `
        --timeout 15 `
        --output $reportPath
    $loadExitCode = $LASTEXITCODE

    $errorLines = @(
        Select-String `
            -Path $stderrPath `
            -Pattern "Internal Server Error|Traceback|ERROR| 500 " `
            -ErrorAction SilentlyContinue
    )
    Write-Output "Capacity exit code: $loadExitCode"
    Write-Output "Capacity report: $reportPath"
    Write-Output "Server error lines: $($errorLines.Count)"
    exit $loadExitCode
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    $env:QA_TEST_PASSWORD = ""
    $env:WAREHOUSE_LOAD_PASSWORD = ""
}
