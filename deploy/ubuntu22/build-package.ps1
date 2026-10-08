param(
    [string]$OutputDir = "dist",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$repoRootPath = $repoRoot.Path
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Get-GitRevision {
    try {
        $rev = git -C $repoRootPath rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $rev) {
            return $rev.Trim()
        }
    }
    catch {
    }
    return "nogit"
}

$revision = Get-GitRevision
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = "$timestamp-$revision"
}

$outputPath = Join-Path $repoRootPath $OutputDir
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$packageName = "after-sales-warehouse-$Version.zip"
$packagePath = Join-Path $outputPath $packageName
$shaPath = "$packagePath.sha256"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "after-sales-warehouse-package-$timestamp"

if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

$excludedRootDirs = @(".git", ".venv", ".venv312", "venv", "node_modules", "dist", "miniprogram")
$excludedRelativeDirs = @("backend\media", "backend\staticfiles")

function Get-RelativePath([string]$FullPath) {
    $relative = $FullPath.Substring($repoRootPath.Length).TrimStart("\", "/")
    return $relative.Replace("/", "\")
}

function Test-IsExcluded([string]$FullPath, [bool]$IsDirectory) {
    $relative = Get-RelativePath $FullPath
    $parts = $relative -split "[\\/]"
    if ($parts.Length -gt 0 -and $excludedRootDirs -contains $parts[0]) {
        return $true
    }
    if ($parts -contains "__pycache__") {
        return $true
    }
    foreach ($dir in $excludedRelativeDirs) {
        if ($relative -eq $dir -or $relative.StartsWith("$dir\")) {
            return $true
        }
    }
    if (-not $IsDirectory) {
        $name = Split-Path -Leaf $FullPath
        if ($name -eq ".env") {
            return $true
        }
        if ($name -like "*.xlsx") {
            return $true
        }
        if ($relative -eq "backend\db.sqlite3") {
            return $true
        }
        if ($name -like "*.pyc") {
            return $true
        }
        if ($name -like "nuc-package-*.zip" -or $name -like "after-sales-warehouse-*.zip") {
            return $true
        }
        if ($name -like "*.sha256") {
            return $true
        }
    }
    return $false
}

Get-ChildItem -LiteralPath $repoRootPath -Force -Recurse -File |
    Where-Object { -not (Test-IsExcluded $_.FullName $false) } |
    ForEach-Object {
        $relative = Get-RelativePath $_.FullName
        $target = Join-Path $tempRoot $relative
        $targetDir = Split-Path -Parent $target
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }

$files = Get-ChildItem -LiteralPath $tempRoot -Recurse -File | Sort-Object FullName
$manifestFiles = foreach ($file in $files) {
    $relative = $file.FullName.Substring($tempRoot.Length).TrimStart("\", "/").Replace("\", "/")
    [PSCustomObject]@{
        path = $relative
        size = $file.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    }
}

$manifest = [PSCustomObject]@{
    app = "after-sales-warehouse"
    version = $Version
    created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    source_revision = $revision
    build_host = $env:COMPUTERNAME
    file_count = @($manifestFiles).Count
    files = $manifestFiles
}

$manifestPath = Join-Path $tempRoot "release-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

if (Test-Path -LiteralPath $packagePath) {
    Remove-Item -LiteralPath $packagePath -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open(
    $packagePath,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($file in Get-ChildItem -LiteralPath $tempRoot -Recurse -File) {
        # ZIP paths must use '/', otherwise Linux unzip treats Windows '\' as part of the filename.
        $relative = $file.FullName.Substring($tempRoot.Length).TrimStart("\", "/").Replace("\", "/")
        $entry = $archive.CreateEntry(
            $relative,
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $sourceStream = [System.IO.File]::OpenRead($file.FullName)
        $entryStream = $entry.Open()
        try {
            $sourceStream.CopyTo($entryStream)
        }
        finally {
            $entryStream.Dispose()
            $sourceStream.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
}

$packageHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packagePath).Hash.ToLowerInvariant()
"$packageHash  $packageName" | Set-Content -LiteralPath $shaPath -Encoding ASCII

Remove-Item -LiteralPath $tempRoot -Recurse -Force

Write-Host "Package created: $packagePath"
Write-Host "Checksum file: $shaPath"
