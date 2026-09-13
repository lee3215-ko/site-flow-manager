$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe not found' }
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) {
    $desktop = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders').Desktop)
}
if (-not (Test-Path -LiteralPath $desktop)) { throw 'Desktop folder not found' }
$startup = [Environment]::GetFolderPath('Startup')
$script = Join-Path $PSScriptRoot 'dev_build.py'
$shell = New-Object -ComObject WScript.Shell
$state = Get-Content -LiteralPath (Join-Path $root '.dev-build\current.json') -Raw | ConvertFrom-Json
foreach ($entry in @(
    @{Path=(Join-Path $desktop 'SiteFlow Developer.lnk'); Mode='launch'},
    @{Path=(Join-Path $startup 'SiteFlow Developer Build Watcher.lnk'); Mode='watch'}
)) {
    $shortcut = $shell.CreateShortcut($entry.Path)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = ('"{0}" {1}' -f $script, $entry.Mode)
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$($state.exe),0"
    $shortcut.Description = 'SiteFlow local developer build'
    $shortcut.Save()
    Write-Output $entry.Path
}
Start-Process -FilePath $pythonw -ArgumentList ('"{0}" watch' -f $script) -WorkingDirectory $root -WindowStyle Hidden
