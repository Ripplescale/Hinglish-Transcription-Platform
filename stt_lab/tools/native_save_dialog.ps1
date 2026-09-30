param(
    [Parameter(Mandatory = $true)][int]$AppProcessId,
    [string]$TargetPath,
    [switch]$InspectOnly,
    [switch]$InspectDialog,
    [switch]$Cancel
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$sttProcess = Get-Process -Id $AppProcessId -ErrorAction Stop
if ($sttProcess.ProcessName -notin @('meetily', 'hinglish-stt')) { throw 'Unexpected target process name' }
$sttPidCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $AppProcessId)
$sttRoot = [System.Windows.Automation.AutomationElement]::RootElement
if ($InspectOnly) {
    $sttWindows = $sttRoot.FindAll([System.Windows.Automation.TreeScope]::Children, $sttPidCondition)
    @($sttWindows | ForEach-Object { @{name=$_.Current.Name; automation_id=$_.Current.AutomationId; class=$_.Current.ClassName} }) | ConvertTo-Json -Compress
    exit 0
}
if (-not $InspectDialog -and -not $Cancel) {
    $sttResolved = [System.IO.Path]::GetFullPath($TargetPath)
    if ($sttResolved -notmatch '\\native-qa-[^\\]+\\export-qa\\[^\\]+\.(txt|md|json)$') { throw 'Only isolated QA export files are allowed' }
    if (Test-Path -LiteralPath $sttResolved) { throw 'Refusing to overwrite an existing file' }
}
$sttDeadline = [DateTime]::UtcNow.AddSeconds(12)
$sttDialog = $null
while ([DateTime]::UtcNow -lt $sttDeadline) {
    $sttWindows = $sttRoot.FindAll([System.Windows.Automation.TreeScope]::Children, $sttPidCondition)
    foreach ($sttWindow in $sttWindows) {
        if ($sttWindow.Current.ClassName -eq '#32770' -and $sttWindow.Current.Name -match '^Save') { $sttDialog = $sttWindow; break }
    }
    if ($sttDialog) { break }
    Start-Sleep -Milliseconds 100
}
if (-not $sttDialog) { throw 'No Save dialog found in the approved app process' }
if ($InspectDialog) {
    $sttControls = $sttDialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    @($sttControls | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -or $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button } | ForEach-Object { @{name=$_.Current.Name; automation_id=$_.Current.AutomationId; class=$_.Current.ClassName; type=$_.Current.ControlType.ProgrammaticName} }) | ConvertTo-Json -Compress
    exit 0
}
if ($Cancel) {
    $sttCancel = $sttDialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '2')))
    if (-not $sttCancel -or $sttCancel.Current.Name -notmatch '^Cancel$') { throw 'Cancel is not uniquely identified' }
    $sttCancel.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    exit 0
}
$sttEdits = $sttDialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))
$sttFilename = @($sttEdits | Where-Object { $_.Current.AutomationId -eq '1001' -and $_.Current.Name -match 'File name' })
if ($sttFilename.Count -ne 1) { throw 'File-name edit control is not uniquely identifiable; no fallback clicks attempted' }
$sttPattern = $sttFilename[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$sttPattern.SetValue($sttResolved)
$sttButtons = $sttDialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)))
$sttSave = @($sttButtons | Where-Object { $_.Current.AutomationId -eq '1' -and $_.Current.Name -match '^Save$' })
if ($sttSave.Count -ne 1) { throw 'Save button is not uniquely identifiable; no fallback clicks attempted' }
$sttSave[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
@{saved_via_native_dialog=$true; target=$sttResolved; process_id=$AppProcessId} | ConvertTo-Json -Compress
