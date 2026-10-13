try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    Require-TaskPython
    & (Join-Path $PSScriptRoot 'check.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
    & (Join-Path $PSScriptRoot 'test.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Source regression failed; build stopped.' }
    Invoke-TaskPython -m PyInstaller --noconfirm --clean (Join-Path $TaskRoot 'AppleAutoBuyer.spec')
    & (Join-Path $PSScriptRoot 'self-test.ps1') -Distribution (Join-Path $TaskRoot 'dist\iPhone AutoBuyer') -WithChromium
    if ($LASTEXITCODE -ne 0) { throw 'Packaged offline self-test failed.' }
    Write-Host 'Built dist\iPhone AutoBuyer. Distribute the entire directory, including _internal.'
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
