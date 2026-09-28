[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$rootToken = $resolvedRoot.ToLowerInvariant()
$serviceNames = @('python.exe', 'node.exe', 'esbuild.exe', 'cmd.exe')
$selfProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue
$excludedProcessIds = @([int]$PID)
if ($selfProcess -and $selfProcess.ParentProcessId) {
    # Do not terminate the shell that invoked this cleanup script.
    $excludedProcessIds += [int]$selfProcess.ParentProcessId
}

function Get-ProjectServiceProcesses {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $anchors = @($processes | Where-Object {
        $name = [string]$_.Name
        $commandLine = ([string]$_.CommandLine).Replace('/', '\').ToLowerInvariant()
        $name.ToLowerInvariant() -in $serviceNames -and
        $commandLine.Contains($rootToken) -and
        [int]$_.ProcessId -notin $excludedProcessIds -and
        -not $commandLine.Contains('一键启动.bat')
    })

    # Service consoles spawn Python/Node children whose own command line does
    # not contain the project path. Walk the process tree from the anchored
    # project consoles so stale children are removed as well.
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($anchor in $anchors) { [void]$ids.Add([int]$anchor.ProcessId) }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $processes) {
            if ($ids.Contains([int]$process.ParentProcessId) -and $ids.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    }
    $processes | Where-Object {
        $name = [string]$_.Name
        $commandLine = ([string]$_.CommandLine).Replace('/', '\').ToLowerInvariant()
        $ids.Contains([int]$_.ProcessId) -and
        $name.ToLowerInvariant() -in $serviceNames -and
        [int]$_.ProcessId -notin $excludedProcessIds -and
        -not $commandLine.Contains('一键启动.bat')
    }
}

# A service console can spawn Python/Node children. Re-scan a few times so
# children are removed as well, while leaving unrelated processes untouched.
for ($attempt = 0; $attempt -lt 3; $attempt++) {
    $targets = @(Get-ProjectServiceProcesses)
    if ($targets.Count -eq 0) { break }
    foreach ($target in $targets | Sort-Object @{ Expression = { [int]$_.ParentProcessId }; Descending = $true }) {
        Stop-Process -Id ([int]$target.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 250
}

$remaining = @(Get-ProjectServiceProcesses)
if ($remaining.Count -gt 0) {
    Write-Host "[WARN] Some MoldPilot processes could not be stopped: $($remaining.ProcessId -join ', ')"
    exit 1
}
Write-Host '[OK] Existing MoldPilot services stopped.'
