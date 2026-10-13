param([string]$Python = 'py', [string]$PythonVersion = '3.13')
try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    if (-not (Test-Path -LiteralPath $TaskPython)) {
        if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
            throw 'Install Python 3.13 x64 from python.org, or pass -Python with its full executable path.'
        }
        $launcherArgs = @()
        if ($Python -eq 'py') { $launcherArgs += "-$PythonVersion" }
        & $Python @launcherArgs -m venv (Join-Path $TaskRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    Require-TaskPython
    Invoke-TaskPython -m pip install --upgrade pip
    Invoke-TaskPython -m pip install --only-binary=:all: -r (Join-Path $TaskRoot 'requirements-windows.txt')
    Invoke-TaskPython -m pip check
    Invoke-TaskPython -m playwright install chromium
    Invoke-TaskPython -m app.main --check-environment
    Write-Host 'Setup complete. Run scripts\run.ps1 or scripts\test.ps1.'
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
