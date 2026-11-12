$ErrorActionPreference='Stop'
$relayRoot=$PSScriptRoot
foreach($cameraName in @((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'relay-config.json') -Raw | ConvertFrom-Json).relays | Where-Object {$_.type -eq 'tianyi' -and $_.key -match '^[A-Za-z0-9_-]+$'} | ForEach-Object {$_.key})) {
 $recordPath=Join-Path $relayRoot ($cameraName+'-managed-process.json')
 if(-not (Test-Path -LiteralPath $recordPath)){continue}
 $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
 foreach($taskPid in @($record.pid,$record.ffmpegPid)) {
  $taskProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$taskPid)
  if(-not $taskProcess -or [string]::IsNullOrEmpty($taskProcess.Name)){continue}
  if($taskProcess.Name -notin @('node.exe','ffmpeg.exe') -or [string]::IsNullOrEmpty($taskProcess.CommandLine) -or -not $taskProcess.CommandLine.Contains($relayRoot)){throw ('Unexpected process identity for PID '+$taskPid)}
  [Diagnostics.Process]::GetProcessById([int]$taskPid).Kill()
 }
}
& (Join-Path $relayRoot 'start-all-live.ps1')
