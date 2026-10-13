param([switch]$UnitOnly)
try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    Require-TaskPython
    Invoke-TaskPython -m pip check
    $taskTestRoot = Join-Path ([IO.Path]::GetTempPath()) ('AutoBuyer test ' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $taskTestRoot | Out-Null
    $taskOldData = $env:AUTOBUYER_DATA_DIR
    $taskOldQt = $env:QT_QPA_PLATFORM
    $env:AUTOBUYER_DATA_DIR = Join-Path $taskTestRoot 'data'
    $env:QT_QPA_PLATFORM = 'offscreen'
    try {
        $taskReport = Join-Path $TaskRoot ('reports\windows-source-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Path $taskReport -Force | Out-Null
        Invoke-TaskPython -m app.main --check-environment --report (Join-Path $taskReport 'environment.json')
        $taskMarkers = 'not live'
        if ($UnitOnly) { $taskMarkers = 'not live and not e2e' }
        Invoke-TaskPython -m pytest -q -ra -m $taskMarkers --basetemp (Join-Path $taskTestRoot 'pytest') --junitxml (Join-Path $taskReport 'tests.xml')
    } finally {
        $env:AUTOBUYER_DATA_DIR = $taskOldData
        $env:QT_QPA_PLATFORM = $taskOldQt
        Write-Host "Synthetic data: $taskTestRoot"
    }
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
