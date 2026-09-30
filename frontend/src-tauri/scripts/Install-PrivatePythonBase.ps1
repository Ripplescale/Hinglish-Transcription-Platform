param(
    [Parameter(Mandatory = $true)]
    [string]$SourceBase,
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'STTApp'),
    [switch]$Activate
)
$ErrorActionPreference = 'Stop'
$SourceBase = [IO.Path]::GetFullPath($SourceBase)
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
if ($DataRoot -match '(^|[\\/])OneDrive([^\\/]*)([\\/]|$)') { throw 'The private runtime must be outside OneDrive.' }
$destination = Join-Path $DataRoot 'runtime\python-3.12.14'
$venvRoot = Join-Path $DataRoot 'lab\venvs'
$venvs = @('whisper', 'community1-cpu-py312') | ForEach-Object { Join-Path $venvRoot $_ }

function Get-RuntimeFiles([string]$Directory) {
    $directoryInfo = Get-Item -LiteralPath $Directory
    if ($directoryInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked runtime directory: $Directory" }
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked runtime item: $($item.FullName)" }
        if ($item.PSIsContainer) {
            if ($item.Name -eq 'site-packages') { continue }
            Get-RuntimeFiles $item.FullName
        } else { $item }
    }
}

# Keep the executable, standard library, extension DLLs, Tcl runtime, and license.
# Installed global packages, command wrappers, headers, and static import libraries
# are deliberately excluded; application packages already live in private venvs.
$runtimeFiles = @(Get-ChildItem -LiteralPath $SourceBase -File | Where-Object { $_.Name -match '(\.exe$|\.dll$|^LICENSE)' })
foreach ($directory in @('DLLs', 'Lib', 'tcl')) {
    $path = Join-Path $SourceBase $directory
    if (Test-Path -LiteralPath $path) { $runtimeFiles += @(Get-RuntimeFiles $path) }
}
if (-not ($runtimeFiles | Where-Object { $_.Name -eq 'python312.dll' })) { throw 'Expected the existing CPython 3.12 runtime.' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
if ((Get-Item -LiteralPath $destination).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'The private runtime destination must not be a link.' }
$manifest = foreach ($file in $runtimeFiles) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked runtime file: $($file.FullName)" }
    $relative = [IO.Path]::GetRelativePath($SourceBase, $file.FullName)
    $target = Join-Path $destination $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    if (-not (Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $sourceHash) {
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $sourceHash) { throw "Runtime copy mismatch: $relative" }
    [pscustomobject]@{ path=$relative; bytes=$file.Length; sha256=$sourceHash }
}
$evidence = [pscustomobject]@{ version='3.12.14'; source=$SourceBase; destination=$destination; files=@($manifest); copied_at_utc=[DateTime]::UtcNow.ToString('o') }
$evidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination 'copy-manifest.json') -Encoding utf8
$baseProbe = & (Join-Path $destination 'python.exe') -I -c 'import json,sys,ssl,sqlite3; print(json.dumps({"version":sys.version,"base_prefix":sys.base_prefix}))'
if ($LASTEXITCODE -ne 0) { throw 'The staged Python base failed its isolated standard-library probe.' }
$probe = $baseProbe | ConvertFrom-Json
if ($probe.version -notlike '3.12.14 *' -or [IO.Path]::GetFullPath($probe.base_prefix) -ne $destination) { throw 'Unexpected staged Python version or base prefix.' }

if ($Activate) {
    # The caller must coordinate this with recording/model workers. Refuse a
    # visible private Python process as an additional protection against races.
    $active = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            ($_.ExecutablePath -and (
                $_.ExecutablePath.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase) -or
                $_.ExecutablePath.StartsWith($destination, [StringComparison]::OrdinalIgnoreCase)
            )) -or ($_.CommandLine -and $_.CommandLine.IndexOf($venvRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        }
    if ($active) { throw 'Stop the STT/speaker workers before retargeting their Python environments.' }
    $updated = @()
    try {
        foreach ($venv in $venvs) {
            $cfg = Join-Path $venv 'pyvenv.cfg'
            if (-not (Test-Path -LiteralPath $cfg)) { continue }
            $original = [IO.File]::ReadAllText($cfg)
            $backup = "$cfg.before-private-base"
            if (-not (Test-Path -LiteralPath $backup)) { [IO.File]::WriteAllText($backup, $original, [Text.UTF8Encoding]::new($false)) }
            $updated += @{path=$cfg; original=$original}
            $newText = ($original -split '\r?\n' | Where-Object { $_ -notmatch '^command\s*=' } | ForEach-Object {
                if ($_ -match '^home\s*=') { "home = $destination" }
                elseif ($_ -match '^executable\s*=') { "executable = $(Join-Path $destination 'python.exe')" }
                else { $_ }
            }) -join "`r`n"
            [IO.File]::WriteAllText($cfg, $newText, [Text.UTF8Encoding]::new($false))
            $venvProbe = & (Join-Path $venv 'Scripts\python.exe') -I -c 'import json,sys,ssl,sqlite3; print(json.dumps({"version":sys.version,"base_prefix":sys.base_prefix}))'
            if ($LASTEXITCODE -ne 0) { throw "The private base failed for $venv" }
            $result = $venvProbe | ConvertFrom-Json
            if ($result.version -notlike '3.12.14 *' -or [IO.Path]::GetFullPath($result.base_prefix) -ne $destination) { throw "Incorrect private base for $venv" }
        }
    } catch {
        foreach ($item in $updated) { [IO.File]::WriteAllText($item.path, $item.original, [Text.UTF8Encoding]::new($false)) }
        throw
    }
}
[pscustomobject]@{ destination=$destination; files=$manifest.Count; bytes=($manifest | Measure-Object bytes -Sum).Sum; activated=[bool]$Activate; probe=$probe } | ConvertTo-Json -Depth 3
