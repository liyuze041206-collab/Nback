param([switch]$NoBrowser, [ValidateSet('/demo','/architecture.html','/portal.html#architecture','/portal.html#eeg')][string]$Page='/portal.html#architecture')

# ASCII source keeps Windows PowerShell 5.1 compatible with all system locales.
$ErrorActionPreference = 'Stop'
$CodeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Backend = Join-Path $CodeRoot 'backend'
$Frontend = Join-Path $CodeRoot 'frontend'
$Python = Join-Path $CodeRoot '.venv\Scripts\python.exe'
$PageParts = $Page -split '#', 2
$DemoUrl = 'http://127.0.0.1:8000' + $PageParts[0] + '?v=20261001-centered'
if ($PageParts.Count -gt 1) { $DemoUrl += '#' + $PageParts[1] }
$BrowserPath = $null
foreach ($BrowserRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if (-not $BrowserRoot) { continue }
    foreach ($BrowserRelativePath in @('Google\Chrome\Application\chrome.exe', 'Microsoft\Edge\Application\msedge.exe')) {
        $BrowserCandidate = Join-Path $BrowserRoot $BrowserRelativePath
        if (Test-Path -LiteralPath $BrowserCandidate) { $BrowserPath = $BrowserCandidate; break }
    }
    if ($BrowserPath) { break }
}
$browserJob = $null

try {
    if (-not (Test-Path -LiteralPath $Python)) {
        throw 'Project Python environment is missing. Run install.bat first.'
    }
    & $Python -B -c 'import fastapi,uvicorn,torch,numpy,scipy,multipart'
    if ($LASTEXITCODE -ne 0) {
        throw 'Dependencies are incomplete. Run install.bat first.'
    }

    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Node.js and npm are required. Install Node.js and run install.bat first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
        throw 'Frontend dependencies are missing. Run install.bat first.'
    }
    Write-Host 'Building the latest GSPM-Net pages...' -ForegroundColor Cyan
    Push-Location -LiteralPath $Frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) {
            throw 'Frontend build failed. The outdated page was not opened. See the error above.'
        }
    } finally { Pop-Location }

    $probe = [System.Net.Sockets.TcpClient]::new()
    $occupied = $false
    try {
        $attempt = $probe.BeginConnect('127.0.0.1', 8000, $null, $null)
        if ($attempt.AsyncWaitHandle.WaitOne(500)) {
            try { $probe.EndConnect($attempt); $occupied = $true }
            catch [System.Net.Sockets.SocketException] { }
        }
    } finally { $probe.Dispose() }

    if ($occupied) {
        try {
            $status = Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 10
            $examples = Invoke-RestMethod 'http://127.0.0.1:8000/api/demo/examples' -TimeoutSec 10
            if ($status.service -ne 'GSPM-Net' -or -not $examples.examples) {
                throw 'The listener is not the GSPM-Net demo service.'
            }
        } catch {
            throw 'Port 8000 is in use by another or unresponsive service. No process was stopped.'
        }
        Write-Host "GSPM-Net platform is ready: $DemoUrl" -ForegroundColor Cyan
        if (-not $NoBrowser) {
            if ($BrowserPath) { Start-Process -FilePath $BrowserPath -ArgumentList $DemoUrl }
            else { Start-Process $DemoUrl }
        }
        exit 0
    }

    $env:PYTHONPATH = $Backend
    $env:GSPM_DATA_DIR = Join-Path $CodeRoot 'runtime'
    Write-Host "Starting GSPM-Net platform: $DemoUrl" -ForegroundColor Cyan
    Write-Host 'Keep this window open. Press Ctrl+C here to stop the service.'
    if (-not $NoBrowser) {
        $browserJob = Start-Job -ArgumentList $DemoUrl, $BrowserPath -ScriptBlock {
            param($url, $browserPath)
            for ($i=0; $i -lt 60; $i++) {
                try {
                    $status = Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 2
                    if ($status.service -eq 'GSPM-Net') {
                        $null = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2
                        if ($browserPath) { Start-Process -FilePath $browserPath -ArgumentList $url }
                        else { Start-Process $url }
                        return
                    }
                } catch { }
                Start-Sleep -Milliseconds 500
            }
        }
    }
    Push-Location -LiteralPath $Backend
    try {
        & $Python -B -m uvicorn app:app --host 127.0.0.1 --port 8000
        $serviceExitCode = $LASTEXITCODE
    } finally { Pop-Location }
    if ($serviceExitCode -ne 0) { throw "Service exited with code $serviceExitCode. See the error above." }
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $browserJob) {
        Stop-Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}

