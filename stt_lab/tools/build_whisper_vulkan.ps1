param(
    [Parameter(Mandatory=$true)][string]$Root,
    [ValidateSet('sdk','configure','build')][string]$Step = 'configure',
    [ValidateRange(1,8)][int]$Jobs = 2
)
$ErrorActionPreference = 'Stop'
$sttVkRoot = [IO.Path]::GetFullPath($Root)
$sttVkManifest = Get-Content -LiteralPath (Join-Path $sttVkRoot 'toolchain-manifest.json') -Raw | ConvertFrom-Json
foreach ($sttAsset in $sttVkManifest.artifacts) {
    $sttAssetFile = Join-Path $sttVkRoot $sttAsset.path
    if ((Get-FileHash -LiteralPath $sttAssetFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sttAsset.sha256) {
        throw "Toolchain archive hash mismatch: $($sttAsset.path)"
    }
}
$sttSdkRoot = Join-Path $sttVkRoot 'sdk'
if ($Step -eq 'sdk') {
    if (Test-Path -LiteralPath (Join-Path $sttSdkRoot 'Bin\glslc.exe')) { throw 'SDK already present; refusing to repeat installation' }
    $sttBefore = @{}
    foreach ($sttScope in @('Machine','User')) {
        foreach ($sttKey in @('PATH','VULKAN_SDK','VK_SDK_PATH')) {
            $sttBefore["${sttScope}:${sttKey}"] = [Environment]::GetEnvironmentVariable($sttKey,$sttScope)
        }
    }
    $sttInstaller = Join-Path $sttVkRoot 'vulkan-sdk-1.4.357.0.exe'
    # LunarG documents copy_only=1 as no registry, shortcuts, or PATH changes.
    # No optional components, driver installation, or system SDK registration.
    $sttArgs = @('--root', ('"{0}"' -f $sttSdkRoot), '--accept-licenses', '--default-answer', '--confirm-command', 'install', 'copy_only=1')
    $sttProcess = Start-Process -FilePath $sttInstaller -ArgumentList $sttArgs -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $sttVkRoot 'sdk-copy.stdout.log') -RedirectStandardError (Join-Path $sttVkRoot 'sdk-copy.stderr.log')
    $sttUnchanged = $true
    foreach ($sttScope in @('Machine','User')) {
        foreach ($sttKey in @('PATH','VULKAN_SDK','VK_SDK_PATH')) {
            if ($sttBefore["${sttScope}:${sttKey}"] -cne [Environment]::GetEnvironmentVariable($sttKey,$sttScope)) { $sttUnchanged = $false }
        }
    }
    @{exit_code=$sttProcess.ExitCode; mode='copy_only=1'; environment_unchanged=$sttUnchanged; sdk_root=$sttSdkRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $sttVkRoot 'sdk-copy-result.json') -Encoding utf8
    if ($sttProcess.ExitCode -ne 0 -or -not $sttUnchanged) { throw 'SDK copy failed or unexpectedly changed persistent environment settings' }
    Get-Content -LiteralPath (Join-Path $sttVkRoot 'sdk-copy-result.json')
    exit 0
}
$sttCompiler = Join-Path $sttVkRoot 'compiler\llvm-mingw-20260908-ucrt-x86_64\bin'
$sttCmake = Join-Path $sttVkRoot 'cmake\cmake-3.31.8-windows-x86_64\bin\cmake.exe'
$sttNinja = Join-Path $sttVkRoot 'ninja\ninja.exe'
$sttSource = Join-Path $sttVkRoot ('source\whisper.cpp-' + $sttVkManifest.source_revision)
$sttBuild = Join-Path $sttVkRoot 'build'
$sttCompat = Join-Path $PSScriptRoot 'whisper_cpp_libcxx.cmake'
# Process-local environment only; inherited by CMake's shader-generator child.
$env:PATH = "$sttCompiler;$(Split-Path $sttCmake);$(Split-Path $sttNinja);$sttSdkRoot\Bin;$env:PATH"
$env:VULKAN_SDK = $sttSdkRoot
$env:CC = Join-Path $sttCompiler 'clang.exe'
$env:CXX = Join-Path $sttCompiler 'clang++.exe'
if ($Step -eq 'configure') {
    & $sttCmake -S $sttSource -B $sttBuild -G Ninja "-DCMAKE_MAKE_PROGRAM=$sttNinja" '-DCMAKE_BUILD_TYPE=Release' "-DCMAKE_C_COMPILER=$env:CC" "-DCMAKE_CXX_COMPILER=$env:CXX" "-DCMAKE_PREFIX_PATH=$sttSdkRoot" "-DCMAKE_PROJECT_INCLUDE=$sttCompat" '-USTT_WHISPER_CPP_LIBCXX_FIX_SCHEDULED' '-DGGML_VULKAN=ON' '-DGGML_OPENMP=OFF' '-DGGML_NATIVE=ON' '-DWHISPER_BUILD_TESTS=OFF' '-DWHISPER_BUILD_SERVER=OFF' '-DBUILD_SHARED_LIBS=OFF' '-DCMAKE_EXE_LINKER_FLAGS=-static-libgcc -static-libstdc++'
    if ($LASTEXITCODE -ne 0) { throw 'Vulkan CMake configuration failed' }
} else {
    & $sttCmake --build $sttBuild --config Release --target whisper-cli whisper-quantize --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw 'Vulkan build failed' }
    $sttBin = Join-Path $sttBuild 'bin'
    $sttBinaryRecords = @()
    foreach ($sttExeName in @('whisper-cli.exe','whisper-quantize.exe')) {
        $sttExe = Join-Path $sttBin $sttExeName
        $sttImports = & (Join-Path $sttCompiler 'llvm-objdump.exe') -p $sttExe
        if ($LASTEXITCODE -ne 0) { throw "Could not inspect executable imports: $sttExeName" }
        $sttImports | Set-Content -LiteralPath (Join-Path $sttVkRoot ($sttExeName + '.imports.txt')) -Encoding utf8
        foreach ($sttImport in $sttImports) {
            if ($sttImport -match 'DLL Name:\s*(\S+)') {
                $sttDllName = $Matches[1]
                $sttCompilerDll = Join-Path $sttCompiler $sttDllName
                if (Test-Path -LiteralPath $sttCompilerDll) {
                    Copy-Item -LiteralPath $sttCompilerDll -Destination (Join-Path $sttBin $sttDllName)
                }
            }
        }
    }
    foreach ($sttBinary in (Get-ChildItem -LiteralPath $sttBin -File)) {
        if ($sttBinary.Extension -in @('.exe','.dll')) {
            $sttBinaryRecords += @{path=$sttBinary.FullName; size=$sttBinary.Length; sha256=(Get-FileHash -LiteralPath $sttBinary.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
        }
    }
    @{
        source_revision=$sttVkManifest.source_revision
        source_archive_sha256=($sttVkManifest.artifacts | Where-Object path -eq 'whisper-source.zip').sha256
        upstream_source_modified=$false
        external_compatibility_hook=@{path=$sttCompat; sha256=(Get-FileHash -LiteralPath $sttCompat -Algorithm SHA256).Hash.ToLowerInvariant(); reason='Force include algorithm for common target; O1 only for ggml-vulkan.cpp host dispatch after prolonged Clang O3 optimization'}
        optimization=@{default='O3'; vulkan_host_cpp='O1'; gpu_shaders='unchanged upstream'; cpu_kernels='unchanged O3'}
        compiler='LLVM-MinGW 20260908 / Clang 23.1.1'; cmake='3.31.8'; ninja='1.13.1'; vulkan_sdk='1.4.357.0'
        backend='CPU and Vulkan'; openmp=$false; native_cpu=$true; ninja_jobs=$Jobs
        sdk_copy_only=$true
        setup_environment_check=(Get-Content -LiteralPath (Join-Path $sttVkRoot 'sdk-copy-result.json') -Raw | ConvertFrom-Json)
        binaries=$sttBinaryRecords
        inference_validated=$false
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $sttVkRoot 'build-manifest.json') -Encoding utf8
}
