try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    Require-TaskPython
    Invoke-TaskPython -m app.main --check-environment
    Invoke-TaskPython -m app.main @args
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
