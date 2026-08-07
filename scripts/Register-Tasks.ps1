[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $ProjectRoot,
    [string] $AsfRoot = 'E:\download\ASF-win-x64',
    [switch] $EnableCollector
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$AsfRoot = [System.IO.Path]::GetFullPath($AsfRoot)
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$AsfExe = Join-Path $AsfRoot 'ArchiSteamFarm.exe'
$TaskPath = '\SteamFreebieCollector\'
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Virtual-environment Python was not found at $PythonExe"
}
if (-not (Test-Path -LiteralPath $AsfExe -PathType Leaf)) {
    throw "ASF executable was not found at $AsfExe"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config.toml') -PathType Leaf)) {
    throw "Collector config.toml was not found in $ProjectRoot"
}

$TaskService = New-Object -ComObject 'Schedule.Service'
$TaskService.Connect()
$RootFolder = $TaskService.GetFolder('\')
try {
    $null = $TaskService.GetFolder($TaskPath.TrimEnd('\'))
} catch {
    if ($PSCmdlet.ShouldProcess($TaskPath, 'Create Task Scheduler folder')) {
        $null = $RootFolder.CreateFolder($TaskPath.Trim('\'))
    }
}

$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited

$AsfAction = New-ScheduledTaskAction -Execute $AsfExe -WorkingDirectory $AsfRoot
$AsfLogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$AsfDailyTrigger = New-ScheduledTaskTrigger -Daily -At '08:55'
$AsfSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$AsfTask = New-ScheduledTask -Action $AsfAction -Trigger @($AsfLogonTrigger, $AsfDailyTrigger) -Principal $Principal -Settings $AsfSettings `
    -Description 'Start ArchiSteamFarm at Windows logon and daily before SteamFreebieCollector.'

$CollectorAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument '-m steam_freebie_collector run --mode automatic' `
    -WorkingDirectory $ProjectRoot
$CollectorLogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$CollectorLogonTrigger.Delay = 'PT30S'
$CollectorDailyTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
$CollectorSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$CollectorTask = New-ScheduledTask -Action $CollectorAction -Trigger @($CollectorLogonTrigger, $CollectorDailyTrigger) -Principal $Principal -Settings $CollectorSettings `
    -Description 'Collect current Keylol Steam freebies at Windows logon and daily at 09:00 local time.'

if ($PSCmdlet.ShouldProcess("${TaskPath}ArchiSteamFarm", 'Register scheduled task')) {
    Register-ScheduledTask -TaskPath $TaskPath -TaskName 'ArchiSteamFarm' -InputObject $AsfTask -Force | Out-Null
}
if ($PSCmdlet.ShouldProcess("${TaskPath}Collector", 'Register scheduled task')) {
    Register-ScheduledTask -TaskPath $TaskPath -TaskName 'Collector' -InputObject $CollectorTask -Force | Out-Null
    if (-not $EnableCollector) {
        Disable-ScheduledTask -TaskPath $TaskPath -TaskName 'Collector' | Out-Null
    }
}

if ($WhatIfPreference) {
    Write-Output 'Task definitions validated; no tasks were registered because -WhatIf was used.'
    return
}

Write-Output "Registered ${TaskPath}ArchiSteamFarm"
if ($EnableCollector) {
    Write-Output "Registered and enabled ${TaskPath}Collector"
} else {
    Write-Output "Registered ${TaskPath}Collector in DISABLED state; enable it only after validation."
}
