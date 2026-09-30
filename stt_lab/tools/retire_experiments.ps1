param([Parameter(Mandatory=$true)][string]$PrivateRoot)
$ErrorActionPreference = 'Stop'
$sttRoot = (Resolve-Path -LiteralPath $PrivateRoot).Path.TrimEnd('\')
if ((Split-Path $sttRoot -Leaf) -ne 'STTApp' -or $sttRoot -match '(?i)OneDrive') { throw 'Expected private STTApp root outside OneDrive' }
$sttLab = Join-Path $sttRoot 'lab'
$sttArchive = Join-Path $sttRoot 'benchmark\retired-20260922'
if (Test-Path -LiteralPath $sttArchive) { throw 'Retirement archive already exists; inspect before retrying' }
$sttTargets = @('models\swift','models\srota','models\indicxlit','converted\whisper-cpp-b5130\swift','venvs\srota','venvs\indicxlit')
$sttResolved = @()
foreach ($relative in $sttTargets) {
    $candidate = Join-Path $sttLab $relative
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    if (-not $resolved.StartsWith($sttLab + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Deletion target outside lab' }
    $entries = @(Get-Item -LiteralPath $resolved) + @(Get-ChildItem -LiteralPath $resolved -Recurse -Force)
    if ($entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw 'Refusing reparse points in retirement targets' }
    $sttResolved += [pscustomobject]@{Relative=$relative; Path=$resolved; Files=@($entries|Where-Object {-not $_.PSIsContainer})}
}
New-Item -ItemType Directory -Path $sttArchive | Out-Null
$sttSourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$sttSourceFiles = @(Get-ChildItem -LiteralPath $sttSourceRoot -Recurse -File | Where-Object {$_.Extension -in @('.py','.ps1','.json','.toml','.md','.txt','.cjs')})
Compress-Archive -LiteralPath $sttSourceFiles.FullName -DestinationPath (Join-Path $sttArchive 'benchmark-source-before-retirement.zip')
$inventory = @()
foreach ($target in $sttResolved) {
    $inventory += [pscustomobject]@{Path=$target.Path; FileCount=$target.Files.Count; Bytes=($target.Files|Measure-Object Length -Sum).Sum}
    if (-not $target.Relative.StartsWith('venvs\')) {
        foreach ($file in $target.Files) {
            if ($file.Extension -in @('.safetensors','.bin','.pt','.whl','.zip','.pyc')) { continue }
            $relative = $file.FullName.Substring($sttLab.Length + 1)
            $destination = Join-Path $sttArchive ('metadata\' + $relative)
            New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $destination
            if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash) { throw 'Metadata copy mismatch' }
        }
    }
}
foreach ($name in @('srota-installed.txt','indicxlit-installed.txt')) {
    $source = Join-Path $sttLab $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $sttArchive }
}
$inventory | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $sttArchive 'removal-inventory.json') -Encoding utf8
foreach ($target in $sttResolved) {
    # Every fully resolved path and descendant was checked above in this shell.
    Remove-Item -LiteralPath $target.Path -Recurse -Force
    if (Test-Path -LiteralPath $target.Path) { throw 'Retirement target remains' }
}
@{Removed=$sttResolved.Count; Bytes=($inventory|Measure-Object Bytes -Sum).Sum; Archive=$sttArchive; Preserved='All recordings, results, corrections, manifests and model metadata'; Status='complete'} | ConvertTo-Json | Tee-Object -FilePath (Join-Path $sttArchive 'retirement-result.json')
