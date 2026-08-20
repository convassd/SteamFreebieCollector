[CmdletBinding()]
param(
    [string] $ProjectRoot,
    [switch] $CheckAsf
)

$ErrorActionPreference = 'Continue'
$TaskPath = '\SteamFreebieCollector\'
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue |
    Sort-Object TaskName |
    ForEach-Object {
        $Info = Get-ScheduledTaskInfo -TaskPath $_.TaskPath -TaskName $_.TaskName
        [PSCustomObject]@{
            Task = "$($_.TaskPath)$($_.TaskName)"
            State = $_.State
            Enabled = $_.Settings.Enabled
            LastRunTime = $Info.LastRunTime
            LastTaskResult = $Info.LastTaskResult
            NextRunTime = $Info.NextRunTime
            Action = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join '; '
            Triggers = ($_.Triggers | ForEach-Object {
                $Kind = $_.CimClass.CimClassName
                "$Kind start=$($_.StartBoundary) delay=$($_.Delay) days=$($_.DaysInterval)"
            }) -join '; '
            WakeToRun = $_.Settings.WakeToRun
        }
    } | Format-List

$Database = Join-Path $ProjectRoot 'data\collector.sqlite3'
$Logs = Join-Path $ProjectRoot 'logs'
Write-Output "Database: $Database (exists: $(Test-Path -LiteralPath $Database))"
Write-Output "Logs: $Logs (exists: $(Test-Path -LiteralPath $Logs))"
if (Test-Path -LiteralPath $Database) {
    $PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $PythonExe) {
        & $PythonExe -m steam_freebie_collector cycle list --limit 5
    }
}

$LegacyAsfTask = Get-ScheduledTask -TaskPath $TaskPath -TaskName 'ArchiSteamFarm' -ErrorAction SilentlyContinue
Write-Output "Legacy standalone ASF task present: $($null -ne $LegacyAsfTask)"

if ($CheckAsf) {
    $Headers = @{}
    if ($env:STEAM_FREEBIE_ASF_IPC_PASSWORD) {
        $Headers['Authentication'] = $env:STEAM_FREEBIE_ASF_IPC_PASSWORD
    }
    try {
        $Response = Invoke-RestMethod -Method Get -Uri 'http://localhost:1242/Api/ASF' -Headers $Headers -TimeoutSec 5
        Write-Output "ASF IPC health: Success=$($Response.Success) Message=$($Response.Message)"
    } catch {
        Write-Warning "ASF IPC health check failed: $($_.Exception.Message)"
    }
}
