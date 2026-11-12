$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot 'service.py'
$recordFile=Join-Path $PSScriptRoot 'process.json'
if(Test-Path -LiteralPath $recordFile){
 $record=Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json
 $running=Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
 if($running){
  try {
   $existing=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$record.pid) -ErrorAction Stop
   if($existing -and $existing.CommandLine -and $existing.CommandLine.Contains($scriptPath)){Write-Output '智能录像优化已运行';exit 0}
  } catch {
   $recordedAt=(Get-Item -LiteralPath $recordFile).LastWriteTime
   if($running.ProcessName -eq 'python' -and $running.StartTime -and $running.StartTime -le $recordedAt){Write-Output '智能录像优化已运行';exit 0}
  }
 }
}
$stopFile=Join-Path $PSScriptRoot 'stop.request'
if(Test-Path -LiteralPath $stopFile){Remove-Item -LiteralPath $stopFile}
$python=Join-Path (Split-Path $PSScriptRoot -Parent) 'object-detection\.venv\Scripts\python.exe'
$process=Start-Process -FilePath $python -ArgumentList @(('"'+$scriptPath+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $PSScriptRoot 'service.log') -PassThru
Write-Output ('智能录像优化 PID：'+$process.Id)
