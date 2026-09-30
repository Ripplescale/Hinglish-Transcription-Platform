param([string]$PythonExe = '', [switch]$CheckHttps)
$ErrorActionPreference = 'Stop'
if (-not $PythonExe) {
    $sttProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $sttInstallPath = Join-Path $sttProjectRoot 'frontend\src-tauri\binaries\sttapp-local-install.json'
    $sttDataRoot = if ($env:STTAPP_DATA_DIR) { $env:STTAPP_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'STTApp' }
    if (-not $env:STTAPP_DATA_DIR -and (Test-Path -LiteralPath $sttInstallPath -PathType Leaf)) {
        $sttDataRoot = (Get-Content -LiteralPath $sttInstallPath -Raw | ConvertFrom-Json).data_root
    }
    $sttRuntimePath = Join-Path $sttDataRoot 'runtime.json'
    if (-not (Test-Path -LiteralPath $sttRuntimePath -PathType Leaf)) { throw 'Local STT runtime was not found. Supply -PythonExe explicitly.' }
    $PythonExe = (Get-Content -LiteralPath $sttRuntimePath -Raw | ConvertFrom-Json).python_executable
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonExe).Path
if (-not (Test-Path -LiteralPath $resolvedPython -PathType Leaf)) { throw 'Python executable was not found.' }
$sttHttpsHelper = Join-Path $PSScriptRoot 'windows_https.py'
if ($CheckHttps) {
    & $resolvedPython -B $sttHttpsHelper --check-https
    if ($LASTEXITCODE -ne 0) { throw 'Verified HTTPS preflight failed. Certificate verification remains enabled.' }
    return
}
Write-Host 'Sign into Hugging Face locally. The token is entered in this terminal, not in chat or command arguments.'
Write-Host 'First accept Community-1 access conditions at https://huggingface.co/pyannote/speaker-diarization-community-1'
& $resolvedPython -B $sttHttpsHelper --login
if ($LASTEXITCODE -ne 0) { throw 'Local Hugging Face login did not complete.' }
