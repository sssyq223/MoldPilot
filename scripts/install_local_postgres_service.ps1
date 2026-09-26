param(
    [string]$PostgresHome = 'D:\PostgreSQL\18',
    [string]$ServiceName = 'moldpilot-postgresql-55432',
    [string]$ResultFile = '',
    [string]$VerifiedConfiguration = ''
)
$ErrorActionPreference = 'Stop'
$env:LC_ALL = 'C'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root '.local\pgdata'
$pgCtl = Join-Path $PostgresHome 'bin\pg_ctl.exe'
$postgres = Join-Path $PostgresHome 'bin\postgres.exe'

function Save-Result([string]$State, [string]$Message = '') {
    $result = [ordered]@{state=$State; service=$ServiceName; data_directory=$data; port=55432; message=$Message; time=(Get-Date).ToString('o')}
    if ($ResultFile) { $result | ConvertTo-Json | Set-Content -LiteralPath $ResultFile -Encoding UTF8 }
    Write-Host "[$State] $Message"
}
function Native-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Native command failed: $File (exit $LASTEXITCODE)" }
}
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($ServiceName -ne 'moldpilot-postgresql-55432') { throw 'Unexpected service name; refusing to touch another PostgreSQL service.' }
    if (-not (Test-Path -LiteralPath $pgCtl) -or -not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION'))) { throw 'PostgreSQL binary or existing project data directory is missing. No initdb will be executed.' }
    if ((Get-Content -LiteralPath (Join-Path $data 'PG_VERSION') -Raw).Trim() -ne '18') { throw 'Existing data directory is not PostgreSQL 18.' }
    $version = & $pgCtl --version
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '\b18\.') { throw 'PostgreSQL binary major version does not match.' }
    $configFiles = @((Join-Path $data 'postgresql.conf'), (Join-Path $data 'postgresql.auto.conf'))
    foreach ($config in $configFiles) {
        if (Select-String -LiteralPath $config -Pattern '^\s*include(?:_if_exists|_dir)?(?:\s|=)' -Quiet) { throw 'External PostgreSQL config includes require a separate review before registration.' }
    }
    $fingerprint = (($configFiles | ForEach-Object {(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash}) -join ':')
    if (-not $elevated) {
        # postgres.exe 在 Windows 下拒绝管理员直接运行；只读解析在普通账号执行，
        # 提权后重新核对配置哈希，数据库服务自身仍使用低权限 NetworkService。
        $configuredData = (& $postgres -D $data -p 55432 -C data_directory | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Read-only PostgreSQL configuration inspection failed.' }
        if ([IO.Path]::GetFullPath($configuredData) -ne [IO.Path]::GetFullPath($data)) { throw 'PostgreSQL configuration redirects to another data directory.' }
        $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -PostgresHome `"$PostgresHome`" -ServiceName `"$ServiceName`" -VerifiedConfiguration `"$fingerprint`""
        if ($ResultFile) { $arguments += " -ResultFile `"$ResultFile`"" }
        Save-Result 'AWAITING_ADMIN_APPROVAL' 'Read-only configuration verified. Approve Windows UAC to install the service.'
        $installer = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -PassThru -WorkingDirectory $root
        Write-Host "Elevated installer PID: $($installer.Id)"
        exit 0
    }
    if (-not $VerifiedConfiguration -or $VerifiedConfiguration -ne $fingerprint) { throw 'Run this script from a normal PowerShell first, or configuration changed after verification.' }

    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if ($service) {
        if ($service.PathName -notlike "*$pgCtl*" -or $service.PathName -notlike "*$data*" -or $service.PathName -notlike '*-p 55432*' -or $service.StartName -ne 'NT AUTHORITY\NetworkService') { throw 'An unrelated service uses this name; no changes were made.' }
    }
    $alreadyRunning = $service -and $service.State -eq 'Running'
    if (-not $alreadyRunning) {
        if (Get-NetTCPConnection -State Listen -LocalPort 55432 -ErrorAction SilentlyContinue) { throw 'Port 55432 is already in use; refusing to start a duplicate database.' }
        & $pgCtl status -D $data | Out-Null
        if ($LASTEXITCODE -eq 0) { throw 'An unmanaged PostgreSQL already uses the project data directory. Stop it gracefully before service registration.' }
    }

    Save-Result 'REGISTERING' 'Using the existing project cluster; no database migration or initialization.'
    # NTFS/ReFS 上仅给服务账号数据目录修改权限；exFAT 不支持 ACL，不伪称已设置权限。
    $volume = Get-Volume -DriveLetter ([IO.Path]::GetPathRoot($data).Substring(0,1))
    if ($volume.FileSystemType -in @('NTFS','ReFS')) {
        Native-Checked 'icacls.exe' @($data, '/grant', '*S-1-5-20:(OI)(CI)M', '/T', '/Q')
    } else {
        Write-Warning 'The data volume has no NTFS ACL protection. Consider a separately planned move to NTFS; this script will not move business data.'
    }
    if (-not $service) {
        Native-Checked $pgCtl @('register', '-N', $ServiceName, '-D', $data, '-U', 'NT AUTHORITY\NetworkService', '-S', 'auto', '-w', '-t', '600', '-o', '-p 55432 -c logging_collector=on -c log_directory=log')
    }
    Native-Checked 'sc.exe' @('config', $ServiceName, 'start=', 'auto')
    Native-Checked 'sc.exe' @('description', $ServiceName, 'MoldPilot isolated PostgreSQL 18, port 55432. Existing project data only; no migrations.')
    Native-Checked 'sc.exe' @('failure', $ServiceName, 'reset=', '86400', 'actions=', 'restart/5000/restart/15000/restart/60000')
    Native-Checked 'sc.exe' @('failureflag', $ServiceName, '1')
    if ($alreadyRunning) { Save-Result 'RUNNING' 'Automatic startup and recovery actions verified; existing service was not restarted.'; exit 0 }
    Save-Result 'STARTING' 'Waiting up to 600 seconds for normal PostgreSQL crash recovery.'
    Start-Service -Name $ServiceName
    $controller = Get-Service -Name $ServiceName
    $controller.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(600))
    Save-Result 'RUNNING' 'Service is running with automatic startup and recovery actions. Verify port, data directory and SQL before launching the application.'
    exit 0
} catch {
    Save-Result 'FAILED' $_.Exception.Message
    exit 1
}
