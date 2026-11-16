param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$Wapiti = ".\.worktmp\security-tools\Scripts\wapiti.exe",
    [int]$Port = 8765,
    [int]$MaxScanSeconds = 240
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workDirectory = Join-Path $repositoryRoot ".worktmp"
$pythonTemp = Join-Path $workDirectory "python-temp"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$databasePath = Join-Path $workDirectory "security-dast-e2e-$stamp.sqlite3"
$reportPath = Join-Path $workDirectory "wapiti-report-$stamp.json"
$sessionPath = Join-Path $workDirectory "wapiti-session-$stamp"
$configPath = Join-Path $workDirectory "wapiti-config-$stamp"
$stdoutPath = Join-Path $workDirectory "dast-server-$stamp.stdout.log"
$stderrPath = Join-Path $workDirectory "dast-server-$stamp.stderr.log"

New-Item -ItemType Directory -Force -Path $workDirectory, $pythonTemp | Out-Null
$pythonPath = (Resolve-Path (Join-Path $repositoryRoot $Python)).Path
$wapitiPath = (Resolve-Path (Join-Path $repositoryRoot $Wapiti)).Path
$backendPath = Join-Path $repositoryRoot "backend"
$baseUrl = "http://127.0.0.1:$Port/"

$env:TEMP = $pythonTemp
$env:TMP = $pythonTemp
$env:DEBUG = "1"
$env:SECRET_KEY = "dast-$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))"
$env:DB_ENGINE = "django.db.backends.sqlite3"
$env:DB_NAME = $databasePath
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"
$env:QA_TEST_PASSWORD = "Qa-$([guid]::NewGuid().ToString('N'))!"

& $pythonPath (Join-Path $backendPath "manage.py") migrate --noinput | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Disposable DAST database migration failed."
}
& $pythonPath (Join-Path $repositoryRoot "tools\prepare_e2e_fixture.py") | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Disposable DAST fixture creation failed."
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
        throw "Disposable DAST server did not become ready."
    }

    & $wapitiPath `
        -u $baseUrl `
        --scope folder `
        --form-user "qa_admin" `
        --form-password $env:QA_TEST_PASSWORD `
        --form-url "${baseUrl}login/" `
        --exclude "${baseUrl}logout/" `
        --exclude "${baseUrl}admin/" `
        -m "csrf,methods,htaccess,sql,xss,file,redirect,ssrf,xxe,exec,upload,backup" `
        --scan-force polite `
        --depth 2 `
        --max-links-per-page 40 `
        --max-files-per-dir 40 `
        --max-scan-time $MaxScanSeconds `
        --max-attack-time 40 `
        --tasks 3 `
        --timeout 8 `
        --flush-session `
        --store-session $sessionPath `
        --store-config $configPath `
        --no-bugreport `
        -v 1 `
        -f json `
        -o $reportPath
    $scanExitCode = $LASTEXITCODE

    Write-Output "Wapiti exit code: $scanExitCode"
    Write-Output "Wapiti report: $reportPath"
    if (Test-Path $reportPath) {
        $report = Get-Content -Raw $reportPath | ConvertFrom-Json
        $findings = @(
            $report.vulnerabilities.PSObject.Properties |
                ForEach-Object {
                    [pscustomobject]@{
                        Category = $_.Name
                        Count = @($_.Value).Count
                    }
                } |
                Where-Object { $_.Count -gt 0 }
        )
        if ($findings.Count -eq 0) {
            Write-Output "Wapiti vulnerabilities: 0"
        } else {
            $findings | Format-Table -AutoSize
        }
    }
    exit $scanExitCode
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    $env:QA_TEST_PASSWORD = ""
}
