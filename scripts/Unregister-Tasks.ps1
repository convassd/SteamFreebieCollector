[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param()

$ErrorActionPreference = 'Stop'
$TaskPath = '\SteamFreebieCollector\'

foreach ($TaskName in @('Collector', 'ArchiSteamFarm')) {
    $Task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $Task -and $PSCmdlet.ShouldProcess("${TaskPath}${TaskName}", 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
    }
}

$TaskService = New-Object -ComObject 'Schedule.Service'
$TaskService.Connect()
$RootFolder = $TaskService.GetFolder('\')
try {
    $Folder = $TaskService.GetFolder($TaskPath.TrimEnd('\'))
    if ($Folder.GetTasks(0).Count -eq 0 -and $Folder.GetFolders(0).Count -eq 0) {
        if ($PSCmdlet.ShouldProcess($TaskPath, 'Delete empty Task Scheduler folder')) {
            $RootFolder.DeleteFolder($TaskPath.Trim('\'), 0)
        }
    }
} catch {
    # The task folder is already absent; there is nothing left to remove.
}
