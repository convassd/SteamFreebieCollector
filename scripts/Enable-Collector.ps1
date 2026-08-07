[CmdletBinding(SupportsShouldProcess = $true)]
param()

$TaskPath = '\SteamFreebieCollector\'
if ($PSCmdlet.ShouldProcess("${TaskPath}Collector", 'Enable scheduled task')) {
    Enable-ScheduledTask -TaskPath $TaskPath -TaskName 'Collector' | Out-Null
    Write-Output "Enabled ${TaskPath}Collector"
}

