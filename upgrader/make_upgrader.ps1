# make_upgrader.ps1 - used by make_upgrader.bat when Python is not installed.
# Compiles make_upgrader.cs (UpgraderMaker) with Add-Type and runs it on the dropped files.
# Keep this file pure ASCII so Windows PowerShell reads it the same in every code page.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
try {
    Add-Type -Path (Join-Path $here 'make_upgrader.cs')
} catch {
    Write-Host $_.Exception.Message
    Read-Host 'Press Enter to close'
    exit 1
}
$rc = [UpgraderMaker]::Run($here, [string[]]$args, $true)
exit $rc
