param([string]$Distribution = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\iPhone AutoBuyer'),
      [switch]$WithChromium, [switch]$InstallChromium)
try {
    . (Join-Path $PSScriptRoot 'windows-common.ps1')
    Initialize-Task
    $taskExe = Join-Path ([IO.Path]::GetFullPath($Distribution)) 'iPhone AutoBuyer.exe'
    if (-not (Test-Path -LiteralPath $taskExe -PathType Leaf)) { throw 'Distribution executable missing.' }
    $taskTestRoot = Join-Path ([IO.Path]::GetTempPath()) ('AutoBuyer packaged ' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $taskTestRoot | Out-Null
    $taskOldData = $env:AUTOBUYER_DATA_DIR
    $env:AUTOBUYER_DATA_DIR = Join-Path $taskTestRoot 'data'
    try {
        foreach ($check in @('environment', 'gui', 'embedded')) {
            $taskFlag = @{ environment = '--check-environment'; gui = '--smoke-test'; embedded = '--embedded-smoke-test' }[$check]
            $taskJson = Join-Path $taskTestRoot ($check + '.json')
            Invoke-TaskExecutable -Executable $taskExe -Arguments @($taskFlag, '--report', $taskJson)
            if (-not (Test-Path -LiteralPath $taskJson)) { throw 'Self-test produced no evidence.' }
            $taskResult = Get-Content -LiteralPath $taskJson -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not $taskResult.ok) { throw "Failed $check self-test." }
        }
        if ($InstallChromium) {
            Invoke-TaskExecutable -Executable $taskExe -Arguments @('--install-browser') -TimeoutSeconds 300
        }
        if ($WithChromium -or $InstallChromium) {
            $taskJson = Join-Path $taskTestRoot 'synthetic-order.json'
            Invoke-TaskExecutable -Executable $taskExe -Arguments @('--local-test', 'first_available', '--headless', '--report', $taskJson) -TimeoutSeconds 120
            $taskResult = @(Get-Content -LiteralPath $taskJson -Raw -Encoding UTF8 | ConvertFrom-Json)
            if ($taskResult.Count -ne 1 -or $taskResult[0].state -ne 'PAYMENT_PENDING' -or
                $taskResult[0].submit_count -ne 1 -or $taskResult[0].synthetic_orders -ne 1) {
                throw 'Packaged synthetic order check failed.'
            }
        }
    } finally {
        $env:AUTOBUYER_DATA_DIR = $taskOldData
        Write-Host "Packaged evidence and synthetic data: $taskTestRoot"
    }
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
