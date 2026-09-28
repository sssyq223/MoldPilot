param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [switch]$IncludeDocument
)

$root = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$python = Join-Path $root '.venv\Scripts\python.exe'
$web = Join-Path $root 'web'
$logRoot = Join-Path $root '.local\logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONLEGACYWINDOWSSTDIO = '0'
$env:PYTHONPATH = Join-Path $root 'backend'

function Start-DetachedService {
    param(
        [Parameter(Mandatory = $true)] [string]$Name,
        [Parameter(Mandatory = $true)] [string]$FilePath,
        [Parameter(Mandatory = $true)] [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)] [string]$WorkingDirectory
    )

    $stamp = Get-Date -Format 'yyyyMMdd'
    $stdout = Join-Path $logRoot ("{0}-detached-{1}.out.log" -f $Name, $stamp)
    $stderr = Join-Path $logRoot ("{0}-detached-{1}.err.log" -f $Name, $stamp)
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-Output ("[OK] {0} started (PID {1})" -f $Name, $process.Id)
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment is missing: $python"
}

Start-DetachedService -Name 'api' -FilePath $python -WorkingDirectory $root -ArgumentList @(
    '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1', '--port', '8001', '--no-access-log'
)
Start-DetachedService -Name 'agent-worker' -FilePath $python -WorkingDirectory $root -ArgumentList @(
    '-m', 'app.agent_worker'
)
Start-DetachedService -Name 'message-worker' -FilePath $python -WorkingDirectory $root -ArgumentList @(
    '-m', 'app.message_worker'
)

if ($IncludeDocument) {
    $ocrConfigured = Select-String -LiteralPath (Join-Path $root '.env') `
        -Pattern '^AGENT_OCR_SERVICE_TOKEN\s*=\s*[^#\s].*$' -Quiet -ErrorAction SilentlyContinue
    if ($ocrConfigured) {
        Start-DetachedService -Name 'document-worker' -FilePath $python -WorkingDirectory $root -ArgumentList @(
            '-m', 'app.document_worker'
        )
    } else {
        Write-Output '[INFO] Document Worker skipped: AGENT_OCR_SERVICE_TOKEN is not configured.'
    }
}

$node = (Get-Command node.exe -ErrorAction Stop).Source
$vite = Join-Path $web 'node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $vite)) {
    throw "Vite entry point is missing: $vite"
}
Start-DetachedService -Name 'web' -FilePath $node -WorkingDirectory $web -ArgumentList @(
    $vite, '--host', '127.0.0.1', '--port', '5173'
)
