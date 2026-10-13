param([string]$Distribution = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\iPhone AutoBuyer'))
try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    $taskDistribution = (Resolve-Path -LiteralPath $Distribution).Path
    $taskExe = Join-Path $taskDistribution 'iPhone AutoBuyer.exe'
    if (-not (Test-Path -LiteralPath $taskExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $taskDistribution '_internal') -PathType Container)) {
        throw 'The complete onedir distribution is required.'
    }
    $taskEvidence = Join-Path ([IO.Path]::GetTempPath()) ('AutoBuyer package ' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $taskEvidence | Out-Null
    $taskJson = Join-Path $taskEvidence 'environment.json'
    Invoke-TaskExecutable -Executable $taskExe -Arguments @('--check-environment', '--report', $taskJson)
    $taskResult = Get-Content -LiteralPath $taskJson -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $taskResult.ok -or -not $taskResult.frozen -or $taskResult.version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'The packaged environment check failed.'
    }
    $taskBase = Split-Path -Leaf $taskDistribution
    $taskArchive = Join-Path (Split-Path -Parent $taskDistribution) ('iPhone AutoBuyer Windows v' + $taskResult.version + '.zip')
    $taskTemporary = $taskArchive + '.' + [guid]::NewGuid() + '.tmp'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [IO.Compression.ZipFile]::CreateFromDirectory($taskDistribution, $taskTemporary,
            [IO.Compression.CompressionLevel]::Optimal, $true)
        $taskZip = [IO.Compression.ZipFile]::Open($taskTemporary, [IO.Compression.ZipArchiveMode]::Update)
        try {
            $taskExtraFiles = @{
                'README.md' = (Join-Path $TaskRoot 'docs\WINDOWS_QUICKSTART.md')
                'scripts/self-test.ps1' = (Join-Path $PSScriptRoot 'self-test.ps1')
                'scripts/windows-common.ps1' = (Join-Path $PSScriptRoot 'windows-common.ps1')
            }
            foreach ($taskEntry in $taskExtraFiles.GetEnumerator()) {
                [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskZip, $taskEntry.Value,
                    ($taskBase + '/' + $taskEntry.Key), [IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        } finally { $taskZip.Dispose() }
        Move-Item -LiteralPath $taskTemporary -Destination $taskArchive -Force
    } finally {
        if (Test-Path -LiteralPath $taskTemporary) { Remove-Item -LiteralPath $taskTemporary }
    }
    $taskHash = (Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    ($taskHash + '  ' + (Split-Path -Leaf $taskArchive)) |
        Set-Content -LiteralPath ($taskArchive + '.sha256') -Encoding ASCII
    Write-Host "Portable package: $taskArchive"
    Write-Host "SHA256: $taskHash"
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
