param([string]$Message = '', [switch]$CheckOnly, [switch]$NoBuild)

# ASCII keeps Windows PowerShell 5.1 compatible with Chinese Windows locales.
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$ExpectedRemote = 'https://github.com/liyuze041206-collab/Nback.git'
$Branch = 'main'

function Invoke-ProjectGit {
    param([string[]]$GitArgs)
    & git -C $ProjectRoot @GitArgs
    if ($LASTEXITCODE -ne 0) { throw ('Git failed: ' + ($GitArgs -join ' ')) }
}

function Assert-PublishableFiles {
    $fileList = (Invoke-ProjectGit -GitArgs @('-c', 'core.quotepath=false', 'ls-files', '--cached', '--others', '--exclude-standard', '-z') | Out-String).TrimEnd([char]13, [char]10)
    $paths = @($fileList.Split([char]0) | Where-Object { $_.Length -gt 0 })
    $weights = @($paths | Where-Object { $_ -match '^backend/models/folds/[^/]+/encoder\.pt$' })
    if ($weights.Count -ne 26) { throw 'Expected all 26 encoder checkpoints. Check backend/models/folds and .gitignore.' }
    foreach ($path in $paths) {
        if ($path -ne '.env.example' -and $path -match '^(runtime|archive|\.venv|frontend/node_modules)/|(^|/)\.env($|\.)|\.(pem|key)$') {
            throw ('Local-only file is tracked: ' + $path + '. Untrack it before uploading.')
        }
        $file = Join-Path $ProjectRoot $path
        if ((Test-Path -LiteralPath $file -PathType Leaf) -and (Get-Item -LiteralPath $file).Length -ge 100MB) {
            throw ('File exceeds the GitHub regular-Git limit: ' + $path)
        }
    }
    Write-Host ('Publishable files: ' + $paths.Count + ' | Model checkpoints: ' + $weights.Count)
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git for Windows first.' }
    $actualRoot = (Invoke-ProjectGit -GitArgs @('rev-parse', '--show-toplevel') | Out-String).Trim()
    if ([IO.Path]::GetFullPath($actualRoot) -ne $ProjectRoot) { throw 'This folder must be the repository root.' }
    $remote = (Invoke-ProjectGit -GitArgs @('remote', 'get-url', 'origin') | Out-String).Trim()
    if ($remote -ne $ExpectedRemote) { throw ('Unexpected origin. Expected ' + $ExpectedRemote) }
    $currentBranch = (Invoke-ProjectGit -GitArgs @('branch', '--show-current') | Out-String).Trim()
    if ($currentBranch -ne $Branch) { throw 'Switch to main before using this updater.' }
    foreach ($marker in @('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD')) {
        $gitPath = (Invoke-ProjectGit -GitArgs @('rev-parse', '--git-path', $marker) | Out-String).Trim()
        if (-not [IO.Path]::IsPathRooted($gitPath)) { $gitPath = Join-Path $ProjectRoot $gitPath }
        if (Test-Path -LiteralPath $gitPath) { throw 'Finish or abort the current Git operation before uploading.' }
    }
    Assert-PublishableFiles
    if ($CheckOnly) { Write-Host 'Repository and upload scope checks passed.' -ForegroundColor Green; exit 0 }

    Invoke-ProjectGit -GitArgs @('fetch', 'origin', $Branch)
    if (-not $NoBuild) {
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.13+ first.' }
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend/node_modules'))) {
            throw 'Run install.bat first to install the frontend build dependencies.'
        }
        Push-Location -LiteralPath $ProjectRoot
        try {
            & npm.cmd --prefix frontend run build
            if ($LASTEXITCODE -ne 0) { throw 'Build failed. No commit or upload was made.' }
        } finally { Pop-Location }
    }
    Assert-PublishableFiles
    Invoke-ProjectGit -GitArgs @('add', '--all', '--', '.')
    & git -C $ProjectRoot diff --cached --quiet
    $diffCode = $LASTEXITCODE
    if ($diffCode -eq 1) {
        if ([string]::IsNullOrWhiteSpace($Message)) { $Message = 'Update GSPM-Net ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') }
        Invoke-ProjectGit -GitArgs @('commit', '-m', $Message)
    } elseif ($diffCode -ne 0) { throw 'Could not inspect staged changes.' }

    & git -C $ProjectRoot merge-base --is-ancestor ('origin/' + $Branch) HEAD
    $ancestorCode = $LASTEXITCODE
    if ($ancestorCode -eq 1) {
        # Rebase only local unpublished commits. Never force-push or discard files.
        & git -C $ProjectRoot rebase ('origin/' + $Branch)
        if ($LASTEXITCODE -ne 0) {
            & git -C $ProjectRoot rebase --abort
            throw 'Remote changes conflict with local work. Rebase was aborted; local commits were kept. Resolve the differences and run again.'
        }
    } elseif ($ancestorCode -ne 0) { throw 'Could not compare local and remote history.' }

    Invoke-ProjectGit -GitArgs @('push', 'origin', 'HEAD:main')
    $localHead = (Invoke-ProjectGit -GitArgs @('rev-parse', 'HEAD') | Out-String).Trim()
    $remoteLine = (Invoke-ProjectGit -GitArgs @('ls-remote', 'origin', 'refs/heads/main') | Out-String).Trim()
    if (($remoteLine -split '\s+')[0] -ne $localHead) { throw 'Remote verification failed. Check GitHub and retry.' }
    Write-Host ('Uploaded and verified: ' + $localHead) -ForegroundColor Green
    Write-Host 'https://github.com/liyuze041206-collab/Nback'
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
