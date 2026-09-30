param(
    [Parameter(Mandatory=$true)][string]$Python,
    [switch]$Direct
)
$ErrorActionPreference = 'Stop'
$sttLabRoot = Join-Path $env:LOCALAPPDATA 'STTApp\lab'
$sttVenv = Join-Path $sttLabRoot 'venvs\whisper'
New-Item -ItemType Directory -Force -Path $sttLabRoot | Out-Null
if ($Direct) {
    # Process-local transport setting. No user or machine environment is changed.
    $env:HTTP_PROXY = ''
    $env:HTTPS_PROXY = ''
    $env:ALL_PROXY = ''
}
$env:PIP_CACHE_DIR = Join-Path $sttLabRoot 'cache\pip'
$env:HF_HOME = Join-Path $sttLabRoot 'cache\huggingface'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
if (-not (Test-Path -LiteralPath (Join-Path $sttVenv 'Scripts\python.exe'))) {
    & $Python -m venv $sttVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated Whisper environment' }
}
$sttPython = Join-Path $sttVenv 'Scripts\python.exe'
& $sttPython -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cpu 'torch==2.8.0'
if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed' }
& $sttPython -m pip install --disable-pip-version-check --index-url https://pypi.org/simple 'transformers==4.56.2' 'soundfile==0.13.1' 'numpy==2.2.6' 'accelerate==1.10.1' 'scipy==1.15.3'
if ($LASTEXITCODE -ne 0) { throw 'Whisper dependencies installation failed' }
& $sttPython -m pip freeze | Set-Content -LiteralPath (Join-Path $sttLabRoot 'whisper-installed.txt') -Encoding utf8
Write-Output "Whisper environment ready: $sttPython"
