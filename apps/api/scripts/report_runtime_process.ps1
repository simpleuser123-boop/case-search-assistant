param(
    [int]$Port = 8000,
    [string]$HealthUrl = "http://127.0.0.1:8000/health"
)

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue

if (-not $connections) {
    Write-Output "No listening process found on port $Port."
    Write-Output "Nothing was killed or restarted."
    exit 0
}

$healthSourceRoot = $null
$healthProcessId = $null
try {
    $health = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 5
    if ($health.runtime) {
        $healthSourceRoot = $health.runtime.source_root
        $healthProcessId = $health.runtime.process_id
    }
} catch {
    $healthSourceRoot = "unavailable: $($_.Exception.GetType().Name)"
}

$connections |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
        $processId = $_
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        [PSCustomObject]@{
            Port = $Port
            ProcessId = $processId
            ProcessName = $process.Name
            ExecutablePath = $process.ExecutablePath
            CommandLine = $process.CommandLine
            HealthProcessId = $healthProcessId
            HealthSourceRoot = $healthSourceRoot
        }
    } |
    Format-List

Write-Output "Report only. Nothing was killed or restarted."
Write-Output "If HealthSourceRoot is missing or does not match the current project, restart or switch the API process before gray release."
