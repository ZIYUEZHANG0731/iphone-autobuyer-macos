# Shared helpers for Windows PowerShell 5.1 and PowerShell 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Initialize-Task {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'These entry points require native Windows.'
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $script:TaskRoot = Split-Path -Parent $PSScriptRoot
    Set-Location -LiteralPath $script:TaskRoot
    $script:TaskPython = Join-Path $script:TaskRoot '.venv\Scripts\python.exe'
}

function Invoke-TaskPython {
    & $script:TaskPython @args
    if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit $LASTEXITCODE)." }
}

function Require-TaskPython {
    if (-not (Test-Path -LiteralPath $script:TaskPython -PathType Leaf)) {
        throw 'Missing .venv. Run scripts\setup.ps1 first (Python 3.13 x64 recommended).'
    }
    Invoke-TaskPython -c "import sys, struct, platform; assert sys.platform == 'win32' and sys.version_info >= (3,12) and struct.calcsize('P') == 8 and platform.machine().upper() in ('AMD64','X86_64'), 'Python 3.12+ x64 required'"
}

function Invoke-TaskExecutable {
    param([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 60)
    # ProcessStartInfo on .NET Framework needs Windows command-line quoting.
    $quoted = @($Arguments | ForEach-Object {
        if ($_ -match '"' -or $_.EndsWith('\')) { throw 'Unsupported self-test argument.' }
        '"' + $_ + '"'
    }) -join ' '
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = $quoted
    $start.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw 'Offline self-test timed out.'
        }
        if ($process.ExitCode -ne 0) { throw "Offline self-test failed (exit $($process.ExitCode))." }
    } finally { $process.Dispose() }
}
