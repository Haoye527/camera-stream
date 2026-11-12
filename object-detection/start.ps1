$ErrorActionPreference='Stop'
$processFile=Join-Path $PSScriptRoot 'process.json'
$scriptPath=Join-Path $PSScriptRoot 'detector.py'
if(Test-Path -LiteralPath $processFile){
 $saved=Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
 $existing=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$saved.pid)
 if($existing -and $existing.CommandLine -and $existing.CommandLine.Contains($scriptPath)){Write-Output '本地目标识别已运行';exit 0}
}
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$process=Start-Process -FilePath $python -ArgumentList @(('"'+$scriptPath+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $PSScriptRoot 'detector.log') -PassThru
Write-Output ('本地目标识别 PID：'+$process.Id)
