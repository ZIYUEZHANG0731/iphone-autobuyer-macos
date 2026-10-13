try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    Require-TaskPython
    & (Join-Path $PSScriptRoot 'check.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
    & (Join-Path $PSScriptRoot 'test.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Source regression failed; build stopped.' }
    # Resolve native dependencies against Windows and the wheel hook paths.
    # A developer PATH can contain an unrelated ICU (e.g. Poppler), whose
    # filename matches Windows ICU but whose exports cannot satisfy Qt.
    $taskOldPath = $env:PATH
    try {
        $env:PATH = @((Join-Path $TaskRoot '.venv\Scripts'),
            (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
        Invoke-TaskPython -m PyInstaller --noconfirm --clean (Join-Path $TaskRoot 'AppleAutoBuyer.spec')
    } finally { $env:PATH = $taskOldPath }
    & (Join-Path $PSScriptRoot 'self-test.ps1') -Distribution (Join-Path $TaskRoot 'dist\iPhone AutoBuyer') -WithChromium
    if ($LASTEXITCODE -ne 0) { throw 'Packaged offline self-test failed.' }
    & (Join-Path $PSScriptRoot 'package.ps1') -Distribution (Join-Path $TaskRoot 'dist\iPhone AutoBuyer')
    if ($LASTEXITCODE -ne 0) { throw 'Portable packaging failed.' }
    Write-Host 'Built dist\iPhone AutoBuyer. Distribute the entire directory, including _internal.'
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
