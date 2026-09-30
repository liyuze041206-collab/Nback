param([string]$PythonCommand = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Directory]::GetParent($PSScriptRoot).FullName
Set-Location -LiteralPath $projectRoot
Write-Host 'GSPM-Net / Install local dependencies' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $PythonCommand -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment. Install Python 3.11+ first.' }
}
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $projectPython -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Check the network and try again.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 22.13+ is required.' }
$nodeBinary = (Get-Command node).Source
$npmCli = Join-Path ([IO.Directory]::GetParent($nodeBinary).FullName) 'node_modules\npm\bin\npm-cli.js'
Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
try {
    if (Test-Path -LiteralPath $npmCli) { & $nodeBinary $npmCli ci } else { & npm.cmd ci }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    if (Test-Path -LiteralPath $npmCli) { & $nodeBinary $npmCli run build } else { & npm.cmd run build }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Installation complete. Run start.bat.' -ForegroundColor Green
