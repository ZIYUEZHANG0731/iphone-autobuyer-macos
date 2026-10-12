$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $taskPython -m PyInstaller --noconfirm --clean AppleAutoBuyer.spec
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed' }
Write-Host 'Built dist\iPhone AutoBuyer\iPhone AutoBuyer.exe; keep its entire folder together.'
