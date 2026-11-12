$ErrorActionPreference='Stop'
$processFile=Join-Path $PSScriptRoot 'process.json'
if(Test-Path -LiteralPath $processFile){
 $record=Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
 $runningProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$record.pid)
 if($runningProcess -and $runningProcess.Name){
  if($runningProcess.Name -ne 'python.exe' -or -not $runningProcess.CommandLine -or -not $runningProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'service.py'))){throw 'Unexpected server process'}
  [Diagnostics.Process]::GetProcessById([int]$record.pid).Kill()
 }
}
& (Join-Path $PSScriptRoot 'start.ps1')
