param(
    [switch]$PrepareOnly,
    [string]$DataRoot = '',
    [string]$AssetRoot = '',
    [string]$PythonExe = ''
)
$ErrorActionPreference = 'Stop'
$sttProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $DataRoot) {
    if ($env:STTAPP_DATA_DIR) {
        $DataRoot = $env:STTAPP_DATA_DIR
    } else {
        $DataRoot = Join-Path $env:LOCALAPPDATA 'STTApp'
        $sttInstallPath = Join-Path $sttProjectRoot 'frontend\src-tauri\binaries\sttapp-local-install.json'
        if (Test-Path -LiteralPath $sttInstallPath -PathType Leaf) {
            $DataRoot = (Get-Content -LiteralPath $sttInstallPath -Raw | ConvertFrom-Json).data_root
        }
    }
}
$sttRuntime = $null
$sttRuntimePath = Join-Path $DataRoot 'runtime.json'
if (Test-Path -LiteralPath $sttRuntimePath -PathType Leaf) {
    $sttRuntime = Get-Content -LiteralPath $sttRuntimePath -Raw | ConvertFrom-Json
}
if (-not $PythonExe) {
    if (-not $sttRuntime) { throw 'Local STT runtime not found; supply -PythonExe explicitly.' }
    $PythonExe = $sttRuntime.python_executable
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
if (-not $AssetRoot) {
    $AssetRoot = $DataRoot
    # Reuse the existing private asset root without changing the Whisper venv.
    if ($sttRuntime -and $sttRuntime.worker_script) {
        $sttVersionFolder = [IO.Directory]::GetParent($sttRuntime.worker_script)
        if ($sttVersionFolder.Parent.Name -eq 'runtime') {
            $AssetRoot = $sttVersionFolder.Parent.Parent.FullName
        }
    }
}
$sttSetupArgs = @('-B', (Join-Path $PSScriptRoot 'setup_speaker_runtime.py'), '--data-root', $DataRoot, '--asset-root', $AssetRoot)
if (-not $PrepareOnly) { $sttSetupArgs += '--activate' }
& $PythonExe @sttSetupArgs
if ($LASTEXITCODE -eq 2) {
    Write-Host 'Dependencies are ready. Accept Community-1 access conditions and sign into Hugging Face locally, then run this command again.'
    exit 2
}
if ($LASTEXITCODE -ne 0) { throw 'Speaker runtime setup failed; inspect the setup log shown above.' }
