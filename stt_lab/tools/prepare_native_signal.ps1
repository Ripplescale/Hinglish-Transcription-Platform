param([Parameter(Mandatory = $true)][string]$WavePath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$sttSignal = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $sttSignal.Rate = 0
    $sttSignal.SetOutputToWaveFile([System.IO.Path]::GetFullPath($WavePath))
    $sttSignal.Speak('This is a local microphone and system audio test. Mira says Project Willow needs twenty four pencils for a drawing workshop. Test reference number three seven two five.')
} finally { $sttSignal.Dispose() }
