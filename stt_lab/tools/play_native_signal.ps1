param([Parameter(Mandatory = $true)][string]$WavePath)
$ErrorActionPreference = 'Stop'
$sttPlayer = New-Object System.Media.SoundPlayer([System.IO.Path]::GetFullPath($WavePath))
try {
    $sttPlayer.Load()
    [Console]::Out.WriteLine('READY')
    [Console]::Out.Flush()
    if ([Console]::ReadLine() -eq 'PLAY') { $sttPlayer.PlaySync() }
} finally { $sttPlayer.Stop(); $sttPlayer.Dispose() }

